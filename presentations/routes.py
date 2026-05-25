import json
import logging
import secrets

log = logging.getLogger(__name__)
from datetime import datetime, timezone
from pathlib import Path

from flask import Response, render_template, current_app, request, url_for
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.graph import GraphState, run_pipeline
from presentations import duck
from presentations.migration import ensure_nested
from .aggregation_gate import GateError

from .uploads import (
    parse_xlsx,
    parse_pasted_tsv,
    df_to_xlsx_bytes,
    new_upload_id,
    upload_s3_key,
    next_paste_name,
    sanitise_identifier,
    MAX_UPLOAD_BYTES,
)

# Seed manifest used the first time p_demo is opened.
_DEMO_MANIFEST = Path(__file__).parent.parent / "examples" / "sample_manifest.json"
_DEFAULT_CATALOG_PATH = Path(__file__).parent / "catalog.json"


def _catalog_path() -> Path:
    """Catalog dosyasının yolu — Flask config'inden override edilebilir.
    DEV_MODE'da `examples/sample_catalog.json` set ediliyor (app.py)."""
    return Path(current_app.config.get("CATALOG_PATH") or _DEFAULT_CATALOG_PATH)

# Ephemeral chat job table: token → job dict. Consumed by SSE stream.
_CHAT_JOBS: dict[str, dict] = {}


# ── Pages ────────────────────────────────────────────────────────────────────

@presentations_bp.route("/")
@login_required
def list_presentations():
    """List all presentations owned by this user, plus the demo seed.
    Now reads from S3 via the SessionRegistry."""
    registry = current_app.config["SESSION_REGISTRY"]
    items = registry.list_user_presentations(current_user.sicil)

    # Mark the demo entry if it exists
    for it in items:
        it["is_demo"] = (it.get("id") == "p_demo")

    # Always surface the demo seed if the user has never opened it.
    if not any(i["id"] == "p_demo" for i in items):
        items.append({
            "id": "p_demo",
            "title": "Q4 2025 Hazine Performans Raporu (örnek)",
            "date": "Aralık 2025",
            "blocks_count": None,
            "updated_at": "",
            "is_demo": True,
        })

    # ── Phase 6.5.d: prefetch BlockStore listing server-side so the Bloklar
    # tab is hydrated on first paint. Without this, JS fires /blocks/api/list
    # after page parse + IIFE init, with a visible "Bloklar yükleniyor…"
    # message during the cold-call to S3/local block dir. Embedding the
    # initial listing in the HTML eliminates that ~500ms FOUC.
    blocks_initial: list[dict] = []
    try:
        block_store = current_app.config.get("BLOCK_STORE")
        if block_store is not None:
            blocks_initial = [s.to_dict() for s in block_store.list_blocks()]
    except Exception:
        current_app.logger.exception("prefetch BlockStore listing failed")
        blocks_initial = []

    resp = Response(
        render_template(
            "presentations/list.html",
            presentations=items,
            blocks_initial=blocks_initial,
            blocks_initial_json=json.dumps(blocks_initial, ensure_ascii=False, default=str),
        ),
        mimetype="text/html",
    )
    # Prevent aggressive caching — when a user saves a new block and comes
    # back to /presentations/ they need the latest JS that re-fetches the
    # listing on every tab activation. Without this, browsers can serve a
    # stale HTML doc whose embedded JS still has the old `blocksLoaded`
    # gating logic. Re-validate on each navigation.
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@presentations_bp.route("/", methods=["POST"])
@login_required
def create_presentation():
    """Create a new presentation owned by the current user.

    Body (optional):
      - title : str — initial meta.title (default "Yeni Sunum")
      - basket: list — pre-populate the basket (e.g. carryover from another
                       presentation the user is editing)
    """
    body = request.get_json(silent=True) or {}
    new_pid = "p_" + secrets.token_urlsafe(8)
    now = datetime.now(timezone.utc).isoformat()

    title = (body.get("title") or "").strip() or "Yeni Sunum"
    basket = body.get("basket") if isinstance(body.get("basket"), list) else []

    manifest = {
        "id": new_pid,
        "version": 1,
        "owner_id": current_user.sicil,
        "created_at": now,
        "updated_at": now,
        "meta": {
            "title": title,
            "eyebrow": "Treasury Report",
            "date": "",
            "author_label": current_user.sicil,
        },
        "basket": basket,
        "blocks": [],
    }

    session = current_app.config["SESSION_REGISTRY"].get_or_create(
        current_user.sicil, new_pid
    )
    session.set_manifest(manifest)

    return Response(
        json.dumps({
            "id": new_pid,
            "url": url_for("presentations.editor", pid=new_pid),
            "title": title,
        }, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>")
@login_required
def editor(pid: str):
    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    # Phase 8.b: read-only scope banner when the dashboard has a scope_ref.
    from presentations.routes_scope import load_scope_for_manifest, scope_banner
    banner = scope_banner(load_scope_for_manifest(manifest))
    return render_template(
        "presentations/editor.html",
        presentation_id=pid,
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
        scope_banner=banner,
    )


# ── Manifest ─────────────────────────────────────────────────────────────────

@presentations_bp.route("/<pid>/manifest")
@login_required
def get_manifest(pid: str):
    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404,
            mimetype="application/json",
        )
    return Response(json.dumps(manifest, ensure_ascii=False), mimetype="application/json")


# ── Sources catalog ──────────────────────────────────────────────────────────

@presentations_bp.route("/<pid>/sources")
@login_required
def get_sources(pid: str):
    """Return the catalog of available tables grouped by domain.
    Includes a synthetic 'Yüklenenler' domain if the manifest has uploads.

    Uses the same unified catalog source as Hazırlık (CatalogLoader →
    TableDocStore, falls back to catalog.json) so Sunum's basket reads
    the same 30+ tables Keşif sees.
    """
    from presentations.routes_scope import _catalog_json
    catalog = _catalog_json()
 
    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    uploads = manifest.get("uploads") or []
 
    if uploads:
        upload_domain = {
            "id":     "dom_uploads",
            "label":  "Yüklenenler",
            "icon":   "upload",
            "engine": "duckdb",
            "tables": [],
        }
        for u in uploads:
            for sheet in u.get("sheets") or []:
                # Table id = "upload__<upload_id>__<sheet_name>"
                table_id = f"upload__{u['id']}__{sheet['name']}"
                upload_domain["tables"].append({
                    "id":      table_id,
                    "desc":    f"{u['filename']} — {sheet.get('display_name', sheet['name'])}",
                    "rows":    f"{sheet.get('row_count', 0):,}".replace(",", "."),
                    "engine":  "duckdb",
                    "columns": [
                        {
                            "name":     c["name"],
                            "type":     c["type"],
                            "nullable": c.get("nullable", True),
                        }
                        for c in sheet.get("columns", [])
                    ],
                    "common_filters": [],
                    "_upload_id":   u["id"],
                    "_s3_key":      u["s3_key"],
                    "_sheet_name":  sheet["display_name"],
                })
        catalog["domains"].append(upload_domain)
 
    return Response(json.dumps(catalog, ensure_ascii=False), mimetype="application/json")

# ── Basket ───────────────────────────────────────────────────────────────────

@presentations_bp.route("/<pid>/basket", methods=["POST"])
@login_required
def update_basket(pid: str):
    """Replace the manifest's basket. Triggers a refetch on the next chat turn."""
    body = request.get_json(silent=True) or {}
    new_basket = body.get("basket")
    if not isinstance(new_basket, list):
        return Response(
            json.dumps({"error": "`basket` bir liste olmalı."}, ensure_ascii=False),
            status=400,
            mimetype="application/json",
        )

    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid)) or _empty_manifest(pid)
    manifest["basket"] = new_basket
    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)

    return Response(
        json.dumps({"ok": True, "basket": new_basket, "version": manifest["version"]}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/duckdb/views")
@login_required
def duckdb_views(pid: str):
    """List the views currently registered in this presentation's DuckDB."""
    session = _get_session(pid)
    return Response(
        json.dumps({"views": session.loaded_views()}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>", methods=["DELETE"])
@login_required
def delete_presentation(pid: str):
    """Remove a user's presentation: S3 manifest + pod-local DuckDB cache."""
    if pid == "p_demo":
        return Response(
            json.dumps({"error": "Örnek sunum silinemez."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    registry = current_app.config["SESSION_REGISTRY"]
    session = registry.get_or_create(current_user.sicil, pid)

    # Verify the manifest exists in S3 before deleting
    manifest = session.get_manifest()
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    session.delete_manifest()

    # Drop from registry's in-memory map
    key = (current_user.sicil, pid)
    if key in registry._sessions:
        del registry._sessions[key]

    return Response(json.dumps({"ok": True}, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/<pid>/patch", methods=["POST"])
@login_required
def direct_patch(pid: str):
    """Apply user-driven patches directly (no LLM in the loop).

    Used by UI controls like the lock toggle and the width picker. Bypasses
    the immutability check (so the user CAN flip `locked` / set `width`)
    but still validates op type, path scope and chart-length invariants.

    Body: {"patches": [{op, path, value?}, ...]}
    """
    from presentations.patch import apply_patches, SUPPORTED_OPS
    from presentations.manifest import _validate_chart_length, ALLOWED_PATCH_PREFIXES

    body = request.get_json(silent=True) or {}
    patches = body.get("patches")
    if not isinstance(patches, list):
        return Response(
            json.dumps({"error": "`patches` bir liste olmalı."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    errors = []
    for i, p in enumerate(patches):
        if p.get("op") not in SUPPORTED_OPS:
            errors.append(f"patch[{i}]: unsupported op {p.get('op')!r}")
        path = p.get("path", "")
        if not any(path.startswith(pre) for pre in ALLOWED_PATCH_PREFIXES):
            errors.append(f"patch[{i}]: path {path!r} outside allowed scope")

    if errors:
        return Response(
            json.dumps({"error": "; ".join(errors)}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    try:
        new_manifest = apply_patches(manifest, patches)
    except Exception as exc:
        return Response(
            json.dumps({"error": f"apply error: {exc}"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    # Chart invariants still apply (width change shouldn't break charts; lock
    # toggle doesn't touch chart data — but be safe).
    invariant_errors = []
    for block in new_manifest.get("blocks", []):
        invariant_errors.extend(_validate_chart_length(block))
    if invariant_errors:
        return Response(
            json.dumps({"error": "; ".join(invariant_errors)}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    # Phase 6.5.c: dedupe filters[] by id before persisting. Catches the
    # rare race where two /patch requests for the same filter id slip
    # through the frontend's local check (fast clicks / StrictMode).
    _dedupe_filters(new_manifest)

    new_manifest["version"] = manifest.get("version", 0) + 1
    new_manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    session.set_manifest(new_manifest)

    return Response(
        json.dumps({"ok": True, "version": new_manifest["version"]}, ensure_ascii=False),
        mimetype="application/json",
    )


def _dedupe_filters(manifest: dict) -> int:
    """Collapse manifest['filters'] to unique ids, keeping the first
    occurrence. Returns the number of duplicates removed."""
    filters = manifest.get("filters")
    if not isinstance(filters, list):
        return 0
    seen: set = set()
    deduped: list = []
    dropped = 0
    for f in filters:
        if not isinstance(f, dict):
            continue
        fid = f.get("id")
        if fid in seen:
            dropped += 1
            continue
        seen.add(fid)
        deduped.append(f)
    if dropped:
        manifest["filters"] = deduped
        current_app.logger.info(
            "patch: dedupe filters dropped %d duplicates (kept %d)",
            dropped, len(deduped),
        )
    return dropped


@presentations_bp.route("/<pid>/snapshot", methods=["POST"])
@login_required
def create_snapshot(pid: str):
    """Freeze the current manifest into a shareable snapshot.

    Body (Phase 10D — all optional, missing body keeps prior behaviour):
      - title         : str  — override meta.title for this snapshot only
      - description   : str  — short description, persists in snapshot meta
      - bound_experts : list[str] — expert IDs this snapshot is bound to;
                                    appears under each expert's citation grid
    """
    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    body = request.get_json(silent=True) or {}
    title = body.get("title")
    description = body.get("description") or ""
    bound_experts = body.get("bound_experts")

    # Validate bound_experts against the live ExpertStore — reject unknown
    # ids early so the snapshot isn't published with a stale reference.
    if bound_experts is not None:
        if not isinstance(bound_experts, list):
            return Response(
                json.dumps({"error": "bound_experts bir liste olmalı."}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
        expert_store = current_app.config.get("EXPERT_STORE")
        if expert_store is not None:
            unknown = [
                eid for eid in bound_experts
                if not isinstance(eid, str) or not expert_store.exists(eid)
            ]
            if unknown:
                return Response(
                    json.dumps({
                        "error": f"Bilinmeyen uzman id'leri: {unknown}",
                    }, ensure_ascii=False),
                    status=400, mimetype="application/json",
                )

    store = current_app.config["SNAPSHOT_STORE"]
    meta = store.save(
        manifest,
        owner_id=current_user.sicil,
        title_override=title,
        description=description,
        bound_experts=bound_experts,
    )

    # Return the meta + the URL the user can share. We compute the URL relative
    # to /presentations/ so reverse-proxy SCRIPT_NAME prefixes are honored.
    from flask import url_for
    share_url = url_for("presentations.view_snapshot", sid=meta["snapshot_id"])
    return Response(
        json.dumps({**meta, "url": share_url}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/snapshots")
@login_required
def list_snapshots():
    """List the current user's snapshots — JSON, used by the share modal."""
    store = current_app.config["SNAPSHOT_STORE"]
    items = store.list_for_owner(current_user.sicil)
    return Response(json.dumps(items, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/snapshot/<sid>")
@login_required
def view_snapshot(sid: str):
    """Render a frozen snapshot read-only.

    Auth is still required (corporate intranet). Anyone with login + URL can
    view; that's the share contract. Snapshot IDs have ~64 bits of entropy so
    they're not enumerable.
    """
    store = current_app.config["SNAPSHOT_STORE"]
    payload = store.load(sid)
    if payload is None:
        return Response("Snapshot bulunamadı.", status=404)

    manifest = payload["manifest"]
    return render_template(
        "presentations/snapshot.html",
        snapshot_id=sid,
        meta=payload["meta"],
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )


# ════════════════════════════════════════════════════════════════════════════
# Dashboard publishing — R > Ekip Raporları
# ════════════════════════════════════════════════════════════════════════════

@presentations_bp.route("/user")
@login_required
def whoami():
    """Frontend için: sicil, isim, departman, dashboard_maker yetkisi."""
    return Response(
        json.dumps({
            "sicil":           current_user.sicil,
            "name":            getattr(current_user, "name", ""),
            "department":      getattr(current_user, "department", ""),
            "dashboard_maker": bool(getattr(current_user, "dashboard_maker", False)),
        }, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/users/search")
@login_required
def users_search():
    """Sicil/isim arama — audience picker autocomplete."""
    from presentations.directory import search_users
    q = request.args.get("q", "")
    return Response(
        json.dumps(search_users(q), ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/dept/<path:dept>/members")
@login_required
def dept_members(dept: str):
    """Departman üyeleri — group expand için."""
    from presentations.directory import list_dept_members
    return Response(
        json.dumps(list_dept_members(dept), ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/publish", methods=["POST"])
@login_required
def publish_dashboard(pid: str):
    """Sunum'u Ekip Raporları altına yayınla (dashboard_maker zorunlu)."""
    if not getattr(current_user, "dashboard_maker", False):
        return Response(
            json.dumps({"error": "Bu işlem için yetkiniz yok."}, ensure_ascii=False),
            status=403, mimetype="application/json",
        )

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    audience = body.get("audience_sicils") or []
    if not isinstance(audience, list):
        audience = []
    audience = [str(s).strip() for s in audience if str(s).strip()]

    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    store = current_app.config["DASHBOARD_STORE"]
    meta = store.save(
        manifest,
        name=name,
        owner_id=current_user.sicil,
        owner_department=getattr(current_user, "department", "") or "",
        audience=audience,
    )

    from flask import url_for
    view_url = url_for("presentations.view_dashboard", did=meta["dashboard_id"])
    return Response(
        json.dumps({**meta, "url": view_url}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/dashboards")
@login_required
def list_dashboards():
    """Kullanıcının görebileceği dashboard'ları döner (owner + audience)."""
    store = current_app.config["DASHBOARD_STORE"]
    items = store.list_visible(
        user_sicil=current_user.sicil,
        user_department=getattr(current_user, "department", "") or "",
    )
    return Response(json.dumps(items, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/dashboard/<did>")
@login_required
def view_dashboard(did: str):
    """Yayınlanmış dashboard'u read-only göster (snapshot.html ile aynı template)."""
    store = current_app.config["DASHBOARD_STORE"]
    payload = store.load(did)
    if payload is None:
        return Response("Rapor bulunamadı.", status=404)

    meta = payload.get("meta") or {}
    if not _can_see_dashboard(meta,
                              current_user.sicil,
                              getattr(current_user, "department", "") or ""):
        return Response("Bu rapora erişiminiz yok.", status=403)

    manifest = payload["manifest"]
    return render_template(
        "presentations/snapshot.html",
        snapshot_id=did,
        meta=meta,
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )


def _can_see_dashboard(meta, user_sicil, user_department):
    if not meta:
        return False
    if meta.get("owner_id") == user_sicil:
        return True
    if user_sicil and user_sicil in (meta.get("audience_sicils") or []):
        return True
    if user_department and user_department in (meta.get("audience_departments") or []):
        return True
    return False


# ════════════════════════════════════════════════════════════════════════════
# Library — yeniden kullanılabilir blok şablonları
# ════════════════════════════════════════════════════════════════════════════

@presentations_bp.route("/<pid>/blocks/save", methods=["POST"])
@login_required
def save_block_to_library(pid: str):
    """Sunum içindeki bir bloğu library'e kaydet.

    Request body: {
      block_id: "b_xxx",
      name: "...",
      description: "...",
      tags: ["...", ...],
      audience_sicils: [...]
    }
    """
    from presentations.manifest import find_block_by_id
    body = request.get_json(silent=True) or {}
    block_id = (body.get("block_id") or "").strip()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    tags = body.get("tags") or []
    audience = body.get("audience_sicils") or []
    if not isinstance(tags, list):
        tags = []
    if not isinstance(audience, list):
        audience = []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    audience = [str(s).strip() for s in audience if str(s).strip()]

    if not block_id:
        return Response(
            json.dumps({"error": "block_id zorunlu."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    block, _path = find_block_by_id(manifest, block_id)
    if block is None:
        return Response(
            json.dumps({"error": f"Blok '{block_id}' bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    # Section_header / filter_bar / carousel kütüphaneye uygun değil
    if block.get("type") in ("section_header",):
        return Response(
            json.dumps({"error": "Bölüm başlıkları kütüphaneye kaydedilemez."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    store = current_app.config["LIBRARY_STORE"]
    meta = store.save(
        block,
        name=name,
        description=description,
        tags=tags,
        owner_id=current_user.sicil,
        owner_department=getattr(current_user, "department", "") or "",
        audience=audience,
    )
    return Response(json.dumps(meta, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/library")
@login_required
def list_library():
    """Kullanıcının görebildiği library bloklarının özet listesi (UI grid + arama).

    Query params: ?type=bar_chart (filter), ?tag=mevduat (filter), ?q=foo (search)
    """
    store = current_app.config["LIBRARY_STORE"]
    items = store.list_visible(
        user_sicil=current_user.sicil,
        user_department=getattr(current_user, "department", "") or "",
    )
    # Filter / search query params
    btype = (request.args.get("type") or "").strip()
    tag   = (request.args.get("tag") or "").strip().lower()
    q     = (request.args.get("q") or "").strip().lower()
    if btype:
        items = [m for m in items if (m.get("block_type") or "").lower() == btype.lower()]
    if tag:
        items = [m for m in items if tag in [t.lower() for t in (m.get("tags") or [])]]
    if q:
        items = [m for m in items
                 if q in (m.get("name") or "").lower()
                 or q in (m.get("description") or "").lower()]
    return Response(json.dumps(items, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/library/<bid>/preview")
@login_required
def preview_library_block(bid: str):
    """Bir library bloğunu read-only preview olarak render et.
    Bloğu sentetik bir manifest içine sarar (tek section + bu blok), SQL'ini
    çalıştırır, snapshot.html ile render eder."""
    store = current_app.config["LIBRARY_STORE"]
    payload = store.load(bid)
    if payload is None:
        return Response("Blok bulunamadı.", status=404)
    meta = payload.get("meta") or {}
    if not _can_see_dashboard(meta,
                              current_user.sicil,
                              getattr(current_user, "department", "") or ""):
        return Response("Bu bloka erişiminiz yok.", status=403)

    block = payload.get("block") or {}
    # Yeni id ver (clone gibi) — preview'da çakışma olmasın
    import secrets as _sec
    preview_block = json.loads(json.dumps(block))
    preview_block["id"] = "prv_" + _sec.token_urlsafe(6)

    # Sentetik manifest
    manifest = {
        "id": f"preview_{bid}",
        "version": 1,
        "meta": {"title": meta.get("name", "Blok Önizleme"), "date": ""},
        "blocks": [
            {
                "id": "h_preview",
                "type": "section_header",
                "title": meta.get("name", "Önizleme"),
                "locked": False,
                "config": {},
                "children": [preview_block],
            }
        ],
    }

    # SQL'i hemen çalıştır — DuckDB cache'e kaydolacak ve config dolacak
    if preview_block.get("type") in (
        "kpi", "bar_chart", "line_chart", "area_chart",
        "pie_chart", "heatmap", "radial_bar", "data_table",
    ):
        try:
            _execute_preview_sql(preview_block)
        except Exception as exc:
            log.warning("library preview: SQL execution failed for %s: %s", bid, exc)

    return render_template(
        "presentations/block_preview.html",
        meta={"title": meta.get("name", "Önizleme")},
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )


def _execute_preview_sql(block: dict) -> None:
    """Library preview için SQL'i çalıştırıp block.config'i doldur.
    Bağımsız bir DuckDB connection kullanır — session kontaminasyonu yok."""
    from presentations.duck import execute_block_sql
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    import duckdb

    ds = block.get("data_source") or {}
    sql = (ds.get("original_sql") or ds.get("sql") or "").strip()
    if not sql:
        return

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return

    conn = duckdb.connect(":memory:")
    try:
        new_ds = execute_block_sql(dc, conn, block.get("id", "preview"), sql)
        block["data_source"] = {**ds, **new_ds}
        apply_data_to_config(block, block["data_source"])
    finally:
        conn.close()


@presentations_bp.route("/library/<bid>")
@login_required
def get_library_block(bid: str):
    """Bir library bloğunun tam içeriği (block + meta) — detay modal + clone."""
    store = current_app.config["LIBRARY_STORE"]
    payload = store.load(bid)
    if payload is None:
        return Response(
            json.dumps({"error": "Blok bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )
    meta = payload.get("meta") or {}
    if not _can_see_dashboard(meta,
                              current_user.sicil,
                              getattr(current_user, "department", "") or ""):
        return Response(
            json.dumps({"error": "Bu bloka erişiminiz yok."}, ensure_ascii=False),
            status=403, mimetype="application/json",
        )
    return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/<pid>/export.html")
@login_required
def export_html(pid: str):
    """Mevcut sunum'u tek HTML dosyası olarak indir.
    Snapshot.html template'i ile aynı, sadece Content-Disposition: attachment.
    İçerideki bundle.js + CSS referansları absolute URL'lerle korunur — kullanıcı
    aynı ağdan açtığında tam render olur."""
    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response("Sunum bulunamadı.", status=404)

    html = render_template(
        "presentations/snapshot.html",
        snapshot_id=pid,
        meta={"title": manifest.get("meta", {}).get("title", pid)},
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
    )

    safe_title = (manifest.get("meta", {}).get("title") or pid)
    safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in safe_title).strip()
    filename = f"{safe_title or pid}.html"

    return Response(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@presentations_bp.route("/<pid>/duckdb/preview/<view_name>")
@login_required
def duckdb_preview(pid: str, view_name: str):
    """Return columns + first 10 rows of a DuckDB view (for the basket UI)."""
    session = _get_session(pid)
    try:
        conn = session.get_duck_conn()
        # Lazy refetch if the basket isn't loaded yet.
        manifest = session.get_manifest()
        if manifest and session.needs_refetch(manifest.get("basket", [])):
            dc = current_app.config.get("DATA_CLIENT")
            if dc is not None:
                session.fetch_basket(dc, manifest.get("basket", []))
        preview = duck.preview_view(conn, view_name)
    except ValueError as exc:
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False),
                        status=400, mimetype="application/json")
    except Exception as exc:
        current_app.logger.exception("duckdb_preview failed")
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False),
                        status=500, mimetype="application/json")
    return Response(json.dumps(preview, ensure_ascii=False, default=str),
                    mimetype="application/json")


@presentations_bp.route("/<pid>/table/preview")
@login_required
def table_preview(pid: str):
    """Return first 5000 rows of a catalog table — for the docs modal preview.

    Query params:
        table = "A16438.MEVDUAT_YETKILER" (Oracle) or
                "upload__u_<id>__<sheet>" (Excel)

    Behavior:
        - Oracle: SELECT * FROM <table> FETCH FIRST 5000 ROWS ONLY via DataClient
        - Excel:  Read sheet from S3 → DuckDB → SELECT * LIMIT 5000
    """
    table_id = request.args.get("table", "").strip()
    if not table_id:
        return Response(
            json.dumps({"error": "table parametresi zorunlu."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    session = _get_session(pid)
    conn = session.get_duck_conn()
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış."}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    try:
        # Reuse the existing block-sql executor — it knows both engines.
        # We synthesise a dummy block_id for the cache namespace.
        manifest = session.get_manifest() or {}
        upload_lookup = duck.build_upload_lookup(manifest)
        s3_get = current_app.config.get("S3_GET")

        if table_id.startswith("upload__"):
            sql = f"SELECT * FROM {table_id} LIMIT 5000"
        else:
            sql = f"SELECT * FROM {table_id} FETCH FIRST 5000 ROWS ONLY"

        preview_block_id = f"preview_{table_id.replace('.', '_')}"
        ds = duck.execute_block_sql(
            dc, conn, preview_block_id, sql,
            upload_lookup=upload_lookup,
            s3_get=s3_get,
        )
        # Preview view'ı LLM'in tablo sanmasını önlemek için temizle.
        # `execute_block_sql` `block_<id>` adıyla view register etti.
        try:
            conn.unregister(f"block_{preview_block_id}")
        except Exception:
            pass
    except GateError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "gate"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
    except Exception as exc:
        current_app.logger.exception("table_preview failed")
        return Response(
            json.dumps({"error": str(exc)[:240]}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    # Strip preview-irrelevant fields (sql, original_sql) to keep the payload lean.
    return Response(
        json.dumps({
            "columns":   ds["columns"],
            "rows":      ds["rows"],
            "row_count": ds["row_count"],
            "truncated": ds["truncated"],
            "cap":       ds["cap"],
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )


# ── Chat (LLM patch generation) ──────────────────────────────────────────────

@presentations_bp.route("/<pid>/chat", methods=["POST"])
@login_required
def chat(pid: str):
    body = request.get_json(silent=True) or {}
    user_message = (body.get("message") or "").strip()
    selected_block_id = body.get("selected_block_id")

    if not user_message:
        return Response(
            json.dumps({"error": "Mesaj boş olamaz."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    token = secrets.token_urlsafe(16)
    _CHAT_JOBS[token] = {
        "user_id": current_user.sicil,
        "presentation_id": pid,
        "user_message": user_message,
        "selected_block_id": selected_block_id,
    }
    return Response(
        json.dumps({"token": token}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/stream/<token>")
@login_required
def chat_stream(pid: str, token: str):
    job = _CHAT_JOBS.pop(token, None)
    if not job or job["presentation_id"] != pid or job["user_id"] != current_user.sicil:
        return Response("Geçersiz veya süresi dolmuş token.", status=404)

    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid)) or _empty_manifest(pid)

    from presentations.routes_scope import load_scope_for_manifest
    state = GraphState(
        presentation_id=pid,
        manifest=manifest,
        user_message=job["user_message"],
        selected_block_id=job["selected_block_id"],
        session=session,
        scope_contract=load_scope_for_manifest(manifest),
    )

    app = current_app._get_current_object()
    sicil = current_user.sicil
    user_msg = job["user_message"]

    def generate():
        # Captured for the inner closure to append at the end.
        assistant_msgs: list[dict] = []

        with app.app_context():
            try:
                for event in run_pipeline(state):
                    data = event["data"]
                    payload = json.dumps(data, ensure_ascii=False, default=str)

                    # Capture explanation text(s) for chat history.
                    if event["event"] == "patch" and data.get("explanation"):
                        assistant_msgs.append({
                            "role": "assistant",
                            "text": data["explanation"],
                            "ts":   datetime.now(timezone.utc).isoformat(),
                        })
                    elif event["event"] == "status" and data.get("phase") == "noop" and data.get("explanation"):
                        assistant_msgs.append({
                            "role": "assistant",
                            "text": data["explanation"],
                            "ts":   datetime.now(timezone.utc).isoformat(),
                            "status": "noop",
                        })
                    elif event["event"] == "error" and data.get("message"):
                        assistant_msgs.append({
                            "role": "assistant",
                            "text": data["message"],
                            "ts":   datetime.now(timezone.utc).isoformat(),
                            "status": "error",
                        })

                    yield f"event: {event['event']}\ndata: {payload}\n\n"

                # Persist chat history. Re-read the manifest fresh because
                # apply_patch may have written it during the pipeline.
                try:
                    sess2 = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, pid)
                    m = sess2.get_manifest() or {}
                    chat_history = list(m.get("chat_history") or [])
                    chat_history.append({
                        "role": "user",
                        "text": user_msg,
                        "ts":   datetime.now(timezone.utc).isoformat(),
                    })
                    chat_history.extend(assistant_msgs)
                    # Cap history length to avoid the manifest growing unbounded.
                    MAX_CHAT_MSGS = 200
                    if len(chat_history) > MAX_CHAT_MSGS:
                        chat_history = chat_history[-MAX_CHAT_MSGS:]
                    m["chat_history"] = chat_history
                    m["version"] = m.get("version", 0) + 1
                    sess2.set_manifest(m)
                except Exception:
                    app.logger.exception("chat history persist failed (non-fatal)")

            except Exception as exc:
                app.logger.exception("presentations: pipeline error")
                err = json.dumps({"message": f"Sunucu hatası: {exc}"}, ensure_ascii=False)
                yield f"event: error\ndata: {err}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

@presentations_bp.route("/<pid>/block/sql/execute", methods=["POST"])
@login_required
def execute_block_sql_route(pid: str):
    """Adım 1 manuel test endpoint'i. Bir block_id + Oracle SQL gönder,
    aggregation gate çalışır, sonuç DuckDB'ye view olur, data_source döner.
 
    Body:
        { "block_id": "b_test_chart", "sql": "SELECT branch_code, SUM(...) ..." }
 
    Response:
        200 + data_source dict   (success)
        400 + {error: ...}       (gate reddetti veya body kötü)
        500 + {error: ...}       (Oracle error)
    """
    body = request.get_json(silent=True) or {}
    block_id = (body.get("block_id") or "").strip()
    sql = body.get("sql") or ""
 
    if not block_id:
        return Response(
            json.dumps({"error": "block_id zorunlu."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
 
    session = _get_session(pid)
    conn = session.get_duck_conn()
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış."}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )
 
    try:
        manifest = session.get_manifest() or {}
        upload_lookup = duck.build_upload_lookup(manifest)
        s3_get = current_app.config.get("S3_GET")
        data_source = duck.execute_block_sql(
            dc, conn, block_id, sql,
            upload_lookup=upload_lookup,
            s3_get=s3_get,
        )
    except GateError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "gate"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
    except Exception as exc:
        current_app.logger.exception("execute_block_sql_route failed")
        return Response(
            json.dumps({"error": str(exc), "kind": "oracle"}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )
 
    return Response(
        json.dumps(data_source, ensure_ascii=False, default=str),
        mimetype="application/json",
    )
 
 
@presentations_bp.route("/<pid>/block/<bid>/refresh", methods=["POST"])
@login_required
def refresh_block_data(pid: str, bid: str):
    """Re-run the block's stored SQL against Oracle and persist the fresh
    data_source. Returns the full updated block so the UI can swap it in.
 
    Errors:
        404 {error: ...}                       — presentation/block missing
        400 {error: ..., kind: "no_sql"}       — block has no SQL to refresh
        400 {error: ..., kind: "gate"}         — aggregation gate rejected it
        500 {error: ..., kind: "oracle"}       — Oracle execution failed
        500 {error: ..., kind: "config"}       — DATA_CLIENT missing
    """
    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )
 
    from .manifest import find_block_by_id
    block, _path = find_block_by_id(manifest, bid)
    if block is None:
        return Response(
            json.dumps({"error": f"Blok '{bid}' bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )
 
    body = request.get_json(silent=True) or {}
    new_sql = (body.get("sql") or "").strip() if isinstance(body.get("sql"), str) else None

    ds = block.get("data_source") or {}
    # Body'de SQL geldiyse onu kullan (UI'dan SQL düzenleme), yoksa block'tan al.
    # Prefer original_sql (LLM intent) over the gate-wrapped `sql`.
    sql = new_sql or ds.get("original_sql") or ds.get("sql")
    if not sql:
        return Response(
            json.dumps({
                "error": "Bu blokta kaynak SQL yok — yenilenecek bir veri kaynağı tanımlı değil.",
                "kind": "no_sql",
            }, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
 
    conn = session.get_duck_conn()
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış.", "kind": "config"},
                       ensure_ascii=False),
            status=500, mimetype="application/json",
        )
 
    try:
        upload_lookup = duck.build_upload_lookup(manifest)
        s3_get = current_app.config.get("S3_GET")
        new_ds = duck.execute_block_sql(
            dc, conn, bid, sql,
            upload_lookup=upload_lookup,
            s3_get=s3_get,
        )
    except GateError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "gate"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
    except Exception as exc:
        current_app.logger.exception("refresh_block_data failed")
        msg = str(exc).strip().splitlines()[0][:240]
        return Response(
            json.dumps({"error": msg, "kind": "oracle"}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )
 
    # Persist new data_source on the block, bump manifest version.
    block["data_source"] = new_ds

    from .nodes.execute_block_sqls import apply_data_to_config
    apply_data_to_config(block, new_ds)

    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)
 
    return Response(
        json.dumps({
            "ok": True,
            "version": manifest["version"],
            "block": block,   # full updated block for UI re-render
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )
 
# The manifest renderer's block-type vocabulary (pie_chart, area_chart,
# heatmap, radial_bar, data_table, ...) is broader than the Phase 6.5 Block
# schema's VizType literal. When we hydrate a throwaway stand-in Block for the
# resolver / binder / cache, its viz type is irrelevant to resolution — coerce
# the manifest type to a schema-valid VizType so e.g. pie_chart blocks don't
# fail Block validation here (the bug behind apply-filters' "block_schema"
# errors on pie/area/heatmap/radial/table blocks).
_STANDIN_VIZ_TYPE = {
    "kpi": "kpi",
    "bar_chart": "bar_chart",
    "line_chart": "line_chart",
    "area_chart": "line_chart",
    "pie_chart": "pie",
    "heatmap": "table",
    "radial_bar": "kpi",
    "data_table": "table",
}


def _standin_viz_type(manifest_type: str | None) -> str:
    """Map a manifest block type → a valid Phase 6.5 VizType (default 'table')."""
    return _STANDIN_VIZ_TYPE.get(manifest_type or "", "table")


@presentations_bp.route("/<pid>/block/<bid>/run-manual", methods=["POST"])
@login_required
def run_block_manual(pid: str, bid: str):
    """Phase 6.5 manual-SQL block execution path.

    Body:
        {
          "query": "<SQL with :binds>",
          "variables": [{name, semantic_tag, type, required, default, allowed_values}, ...],
          "variable_overrides": {<name>: <value>, ...}     (optional)
        }

    Flow:
        1. Validate the SQL via the Phase 6.5 whitelist validator (catches DDL/DML/multi-statement).
        2. Build a transient Block model, resolve variables, expand binds.
        3. Execute through the existing duck.execute_block_sql plumbing so the
           result lands in the same `data_source` shape the renderer expects.
        4. Persist query/variables on the manifest leaf and bump manifest version.

    Returns the same shape as /block/<bid>/refresh: {ok, version, block}.
    """
    from .manifest import find_block_by_id
    from .blocks.schema import Block, Variable
    from .sql.validator import validate_sql
    from .sql.binder import expand_binds
    from .variables.resolver import resolve_variables, ResolutionError
    from .nodes.execute_block_sqls import apply_data_to_config
    from .concepts.integration import strip_concept_sentinel

    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    block, _path = find_block_by_id(manifest, bid)
    if block is None:
        return Response(
            json.dumps({"error": f"Blok '{bid}' bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    variables_raw = body.get("variables") or []
    # When scan_only=true, server runs SQL validation + variable
    # auto-discovery (SELECT DISTINCT) ONLY — no resolve, no bind, no
    # execute. UI's "Şemayı Tara" button uses this to populate variable
    # rows + allowed_values without committing to a full Oracle round-trip.
    scan_only = bool(body.get("scan_only"))
    overrides = body.get("variable_overrides") or {}
    if not query:
        return Response(
            json.dumps({"error": "SQL boş.", "kind": "no_sql"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    # ── Auto-discover enum allowed_values from SQL ────────────────────────
    # Before strict validation, for any enum_multi / enum_single variable
    # that lacks allowed_values, try to discover them by running
    # SELECT DISTINCT <col> against the source table. Saves the user from
    # typing every distinct value by hand. Source: spec §6.5.b /
    # jobs/sample_distinct_values.py (same logic, run on-demand here).
    dc_for_discover = current_app.config.get("DATA_CLIENT")
    discovered_info: list[dict] = []  # for response
    if dc_for_discover is not None:
        for vraw in variables_raw:
            if not isinstance(vraw, dict):
                continue
            if vraw.get("type") not in ("enum_multi", "enum_single"):
                continue
            if vraw.get("allowed_values"):
                continue  # user already filled it in
            vname = vraw.get("name")
            if not vname:
                continue
            try:
                values = _discover_distinct_values(query, vname, dc_for_discover)
            except Exception as exc:
                current_app.logger.warning(
                    "discover allowed_values for :%s failed: %s", vname, exc,
                )
                values = None
            if values:
                vraw["allowed_values"] = values
                discovered_info.append({"name": vname, "count": len(values)})

    # Build transient Variable models — partial variables (missing semantic_tag
    # or allowed_values) are reported as user-actionable errors rather than
    # crashing in Pydantic land.
    try:
        var_models = [Variable.model_validate(v) for v in variables_raw]
    except Exception as exc:
        return Response(
            json.dumps({
                "error": "Değişken tanımı geçersiz: " + str(exc),
                "kind": "variable_schema",
            }, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    range_var_names = [v.name for v in var_models if v.type in ("date_range", "number_range")]
    sql_check = validate_sql(
        query,
        declared_variables=[v.name for v in var_models],
        range_variables=range_var_names,
    )
    if not sql_check.ok:
        return Response(
            json.dumps({
                "error": "; ".join(sql_check.errors),
                "kind": "sql",
                "warnings": sql_check.warnings,
            }, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    # ── scan_only short-circuit ─────────────────────────────────────────
    # UI's "Şemayı Tara" button only wants discovery + validation results;
    # the user hasn't filled in defaults yet so resolve/bind would error.
    # Return the (possibly enriched) variables so the React side can replace
    # block.variables and surface the discovered allowed_values as chips.
    if scan_only:
        block["query"] = query
        block["variables"] = [v.model_dump(mode="json", exclude_none=True) for v in var_models]

        # Auto-conceptualize: detect concept-bound literal predicates in the
        # WHERE and propose lifting them into dashboard concept filters. The
        # UI shows the rewrite + seeds the filters on confirm.
        conceptualize = None
        _reg = current_app.config.get("CONCEPT_REGISTRY")
        _cat = current_app.config.get("CONCEPT_BINDING_CATALOG")
        if _reg is not None and _cat is not None:
            try:
                from .concepts.integration import conceptualize_query, derive_source_tables
                from .concepts.user_scope import build_effective_registry
                reg_snap = _reg.snapshot if hasattr(_reg, "snapshot") else _reg
                cat_snap = _cat.snapshot if hasattr(_cat, "snapshot") else _cat
                eff = build_effective_registry(reg_snap, manifest.get("user_concepts"))
                tables = derive_source_tables({"query": query})
                if tables:
                    schema, table = tables[0]
                    res = conceptualize_query(query, schema, table, cat_snap, eff)
                    if res["seeded_filters"] or res["skipped"]:
                        conceptualize = res
            except Exception:
                current_app.logger.exception("conceptualize during scan failed")

        manifest["version"] = manifest.get("version", 0) + 1
        session.set_manifest(manifest)
        return Response(
            json.dumps({
                "ok": True,
                "version": manifest["version"],
                "block": block,
                "warnings": sql_check.warnings,
                "discovered": discovered_info,
                "conceptualize": conceptualize,
                "scan_only": True,
            }, ensure_ascii=False, default=str),
            mimetype="application/json",
        )

    # Synthesize a stand-in Block for the resolver / binder. We don't write it
    # back — it's purely for type-driven coercion.
    try:
        stand_in = Block.model_validate({
            "id": bid if len(bid) >= 3 else f"blk_{bid}",
            "version": 1,
            "title": block.get("title") or "block",
            "team": "in_presentation",
            "owner": current_user.sicil,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "variables": [v.model_dump() for v in var_models],
            "visualization": {"type": _standin_viz_type(block.get("type")), "config": {}},
        })
    except Exception as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "block_schema"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    try:
        resolved = resolve_variables(stand_in, overrides)
    except ResolutionError as exc:
        return Response(
            json.dumps({
                "error": "; ".join(exc.errors),
                "kind": "resolution",
            }, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    try:
        bound = expand_binds(stand_in, resolved)
    except ValueError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "bind"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    conn = session.get_duck_conn()
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış.", "kind": "config"},
                       ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    # Bind params are routed through DataClient.get_data (DEV stub passes them
    # to DuckDB; prod oracledb honours :name binds natively). We mirror the
    # query_params signature the rest of the code uses.
    # Neutralize an un-injected {{concept_filters}} sentinel — a manual run has
    # no dashboard concept filters, so the sentinel becomes a no-op 1 = 1.
    exec_sql = strip_concept_sentinel(bound.sql)
    try:
        df = dc.get_data(
            base_prefix=None,
            dataset=f"block::{bid}",
            query=exec_sql,
            query_params=bound.params,
        )
    except GateError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "gate"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
    except Exception as exc:
        current_app.logger.exception("run_block_manual failed")
        msg = str(exc).strip().splitlines()[0][:240]
        return Response(
            json.dumps({"error": msg, "kind": "oracle"}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    if df is None:
        import pandas as _pd
        df = _pd.DataFrame()

    df = df.reset_index(drop=True) if hasattr(df, "reset_index") else df
    columns = [str(c) for c in df.columns]
    total_rows = int(len(df))

    all_rows = []
    if total_rows > 0:
        for row in df.itertuples(index=False, name=None):
            all_rows.append([duck._jsonable(v) for v in row])

    new_ds = {
        "sql":           exec_sql,
        "original_sql":  query,
        "rewritten":     False,
        "truncated":     False,
        "cap":           total_rows,
        "reason":        "manual_sql",
        "executed_at":   datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "row_count":     total_rows,
        "columns":       columns,
        "preview_rows":  all_rows[:5],
        "rows":          all_rows,
        "view_name":     f"v_{bid}",
        "engine":        "manual_sql",
        "bind_params":   {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                          for k, v in bound.params.items()},
    }

    # Persist Phase 6.5 fields alongside the executed data. The renderer
    # then reads block.config (which apply_data_to_config fills below) so
    # the canvas re-renders without any client-side data plumbing.
    block["query"] = query
    block["variables"] = [v.model_dump(mode="json", exclude_none=True) for v in var_models]
    block["data_source"] = new_ds
    block.pop("manual_sql", None)  # legacy flag from earlier iterations; not used anywhere now
    block.pop("data_stale", None)  # successful run clears the stale flag

    apply_data_to_config(block, new_ds)

    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)

    return Response(
        json.dumps({
            "ok": True,
            "version": manifest["version"],
            "block": block,
            "warnings": sql_check.warnings,
            "discovered": discovered_info,
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )


# ════════════════════════════════════════════════════════════════════════
# Phase 6.5.c — dashboard-level filter apply
# ════════════════════════════════════════════════════════════════════════

@presentations_bp.route("/<pid>/apply-filters", methods=["POST"])
@login_required
def apply_dashboard_filters(pid: str):
    """Re-resolve every block with new dashboard filter state.

    Body:
        {
            "filter_state": {"<filter_id>": <value or {from,to}/{min,max}>, ...}
        }

    Returns:
        {
            "ok": true,
            "version": <new manifest version>,
            "blocks": [
                {"id": "...", "status": "cache_hit"|"subset"|"refetched"|"error", ...},
                ...
            ]
        }

    Behaviour per block (spec §5.5):
      1. Compose binding resolver from variable_bindings + filter_state.
      2. Resolve variables → if any required var unresolved, status=error.
      3. Compute cache key. If exact hit, status=cache_hit, no execution.
      4. Else if subset parent exists, derive via DuckDB filter,
         status=subset.
      5. Else execute against Oracle via DataClient, write cache, status=refetched.

    The "Güncelle" UI button in the React filter bar is the sole caller.
    """
    from .manifest import find_block_by_id, iter_all_blocks
    from .blocks.schema import Block, Variable
    from .dashboards.schema import VariableBinding, DashboardFilter
    from .dashboards.binding import build_binding_resolver
    from .variables.resolver import resolve_variables, ResolutionError
    from .cache.block_cache import BlockCache, cache_key as _cache_key
    from .sql.binder import EmptySelectionError, expand_binds
    from .concepts.integration import (
        dashboard_filters_to_resolved,
        apply_concepts_to_block,
    )

    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    body = request.get_json(silent=True) or {}
    filter_state = body.get("filter_state") or {}
    if not isinstance(filter_state, dict):
        return Response(
            json.dumps({"error": "filter_state must be an object"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    # Persist filter state into the manifest (defaults + ad-hoc per session).
    manifest["filter_state"] = filter_state

    # Defensive dedupe (see _dedupe_filters in this module): catches any
    # duplicate filter ids that slipped through earlier patch races.
    _dedupe_filters(manifest)

    conn = session.get_duck_conn()
    cache = BlockCache(conn)
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış.", "kind": "config"},
                       ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    # ── Phase 7.b — concept filter compilation (additive) ────────────────
    # Bridge the dashboard's filters into concept-level ResolvedFilters once,
    # up front. Per-block injection happens inside the loop. When the registry
    # / catalog aren't configured, or no filter maps to a concept, this stays
    # empty and the route behaves exactly as in Phase 6.5.c (zero regression).
    _registry = current_app.config.get("CONCEPT_REGISTRY")
    _catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")
    resolved_concept_filters = []
    if _registry is not None and _catalog is not None:
        try:
            from .concepts.user_scope import build_effective_registry
            _base = _registry.snapshot if hasattr(_registry, "snapshot") else _registry
            # Effective registry = base ⊕ this presentation's user concepts
            # (Phase 7.d). Base wins id collisions (extension-only, §10.5).
            reg_snapshot = build_effective_registry(_base, manifest.get("user_concepts"))
            cat_snapshot = _catalog.snapshot if hasattr(_catalog, "snapshot") else _catalog
            resolved_concept_filters = dashboard_filters_to_resolved(
                manifest.get("filters") or [], filter_state, reg_snapshot,
            )
        except Exception:
            current_app.logger.exception("concept filter bridge failed; skipping")
            resolved_concept_filters = []
            reg_snapshot = cat_snapshot = None
    else:
        reg_snapshot = cat_snapshot = None

    # Informational concept metadata for blocks that did NOT inject (no
    # sentinel) — merged into their result after the loop.
    concept_info: dict[str, dict] = {}

    from .concepts.integration import derive_source_tables as _derive_st

    results: list[dict] = []
    touched = 0

    for block in iter_all_blocks(manifest):
        if block.get("type") == "section_header":
            continue
        # Data-bound blocks participate if they EITHER declare variables
        # (Phase 6.5) OR are concept-native (source_tables + active concept
        # filters, Phase 7). Concept-native blocks have no `variables`, so the
        # old `not variables_raw → skip` guard wrongly dropped them.
        # SQL may live on block.query (Phase 6.5/7 shape) OR on
        # data_source.original_sql (legacy LLM shape) — read both so concept
        # filtering reaches legacy-shaped blocks too.
        query = block.get("query") or (block.get("data_source") or {}).get("original_sql") or ""
        variables_raw = block.get("variables") or []
        concept_eligible = bool(resolved_concept_filters) and bool(_derive_st(block))
        if not query or (not variables_raw and not concept_eligible):
            continue

        # Hydrate Pydantic Block stand-in for resolver / binder / cache.
        try:
            var_models = [Variable.model_validate(v) for v in variables_raw]
            stand_in = Block.model_validate({
                "id": block["id"] if len(block["id"]) >= 3 else f"blk_{block['id']}",
                "version": int(block.get("version", 1)),
                "title": block.get("title") or "block",
                "team": "in_presentation",
                "owner": current_user.sicil,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "variables": [v.model_dump() for v in var_models],
                "visualization": {"type": _standin_viz_type(block.get("type")), "config": {}},
            })
        except Exception as exc:
            results.append({"id": block["id"], "status": "error",
                             "kind": "block_schema", "error": str(exc)})
            continue

        # Parse variable_bindings if any.
        raw_bindings = block.get("variable_bindings") or {}
        try:
            bindings = {
                name: VariableBinding.model_validate(b)
                for name, b in raw_bindings.items()
            }
        except Exception as exc:
            results.append({"id": block["id"], "status": "error",
                             "kind": "binding_schema", "error": str(exc)})
            continue

        # Resolve.
        try:
            resolver_cb = build_binding_resolver(bindings, filter_state)
            resolved = resolve_variables(stand_in, binding_resolver=resolver_cb)
        except ResolutionError as exc:
            results.append({"id": block["id"], "status": "error",
                             "kind": "resolution",
                             "error": "; ".join(exc.errors)})
            continue

        # ── Empty selection short-circuit ────────────────────────────────
        # If any enum_multi variable resolved to an empty list (user
        # deselected everything), the SQL would crash on `IN ()`. Return
        # an empty result and skip the query entirely.
        empty_var = None
        for v in stand_in.variables:
            if v.type == "enum_multi" and resolved.get(v.name) == []:
                empty_var = v.name
                break
        if empty_var is not None:
            import pandas as _pd
            _apply_df_to_block(block, _pd.DataFrame(), engine="empty", query=query)
            results.append({
                "id": block["id"], "status": "empty",
                "row_count": 0,
                "reason": f"variable :{empty_var} has no selected values",
            })
            touched += 1
            continue

        # ── Phase 7.b — concept predicate injection ──────────────────────
        # Only blocks that declare source_tables AND embed the
        # {{concept_filters}} sentinel opt in. When predicates are injected
        # the SQL no longer matches the variable-keyed cache, so we bypass
        # the cache and fetch fresh (subset/incremental for concept-injected
        # blocks is backlog). Blocks that don't opt in fall through to the
        # unchanged Phase 6.5.c cache path below.
        if resolved_concept_filters and reg_snapshot is not None \
                and _derive_st(block):
            try:
                _bound = expand_binds(stand_in, resolved)
                inj = apply_concepts_to_block(
                    block, _bound.sql, _bound.params,
                    resolved_concept_filters, reg_snapshot, cat_snapshot,
                )
            except Exception:
                current_app.logger.exception(
                    "concept injection failed for %s; falling back", block["id"])
                inj = None

            if inj is not None and inj.injected:
                try:
                    if inj.empty:
                        import pandas as _pd
                        df = _pd.DataFrame()
                        engine = "empty"
                    else:
                        df = dc.get_data(
                            base_prefix=None,
                            dataset=f"block::{block['id']}",
                            query=inj.sql,
                            query_params=inj.params,
                        )
                        if df is None:
                            import pandas as _pd
                            df = _pd.DataFrame()
                        engine = "refetched"
                    _apply_df_to_block(block, df, engine=engine, query=query)
                    results.append({
                        "id": block["id"],
                        "status": "empty" if inj.empty else "refetched",
                        "row_count": int(len(df)),
                        "blind_filters": inj.blind,
                        "applied_predicates": inj.applied,
                        "concept_injected": True,
                    })
                    touched += 1
                    continue
                except Exception as exc:
                    msg = str(exc).strip().splitlines()[0][:240]
                    results.append({"id": block["id"], "status": "error",
                                     "kind": "oracle", "error": msg,
                                     "blind_filters": inj.blind})
                    continue
            elif inj is not None and (inj.blind or inj.applied):
                # Concept filters apply but the block didn't embed the
                # sentinel — surface what would apply (informational) and let
                # the normal cache path run unchanged.
                concept_info[block["id"]] = {
                    "blind_filters": inj.blind,
                    "applied_predicates": inj.applied,
                    "concept_injected": False,
                }

        # ── Cache lookup ─────────────────────────────────────────────────
        ck = _cache_key(stand_in.id, stand_in.version, resolved)
        hit = cache.find_exact(ck)
        if hit is not None:
            # Pull cached rows back into block.data_source for renderer.
            df = conn.execute(f'SELECT * FROM "{hit.view_name}"').fetchdf()
            _apply_df_to_block(block, df, engine="cache_hit", query=query)
            results.append({
                "id": block["id"], "status": "cache_hit",
                "row_count": hit.row_count, "cache_key": ck.short,
            })
            touched += 1
            continue

        parent = cache.find_subset_parent(stand_in, resolved)
        if parent is not None:
            df = _derive_from_parent(conn, parent, stand_in, resolved)
            cache.write(stand_in, resolved, df)
            _apply_df_to_block(block, df, engine="subset", query=query)
            results.append({
                "id": block["id"], "status": "subset",
                "row_count": int(len(df)), "parent_key": parent.key.short,
            })
            touched += 1
            continue

        # Cache miss — fetch from Oracle.
        try:
            from .sql.binder import expand_binds
            from .concepts.integration import strip_concept_sentinel
            bound = expand_binds(stand_in, resolved)
            # This block carries the sentinel but reached the non-concept path
            # (no source_tables or no active concept filter) — neutralize it.
            df = dc.get_data(
                base_prefix=None,
                dataset=f"block::{block['id']}",
                query=strip_concept_sentinel(bound.sql),
                query_params=bound.params,
            )
            if df is None:
                import pandas as _pd
                df = _pd.DataFrame()
            cache.write(stand_in, resolved, df)
            _apply_df_to_block(block, df, engine="refetched", query=query)
            results.append({
                "id": block["id"], "status": "refetched",
                "row_count": int(len(df)),
            })
            touched += 1
        except Exception as exc:
            msg = str(exc).strip().splitlines()[0][:240]
            results.append({"id": block["id"], "status": "error",
                             "kind": "oracle", "error": msg})

    # Attach informational concept metadata (blind/applied) to the results of
    # blocks that matched concept filters but didn't inject (no sentinel).
    if concept_info:
        for r in results:
            ci = concept_info.get(r.get("id"))
            if ci:
                r.setdefault("blind_filters", ci["blind_filters"])
                r.setdefault("applied_predicates", ci["applied_predicates"])
                r.setdefault("concept_injected", ci["concept_injected"])

    if touched:
        manifest["version"] = manifest.get("version", 0) + 1
        session.set_manifest(manifest)

    return Response(
        json.dumps({
            "ok": True,
            "version": manifest.get("version"),
            "blocks": results,
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )


def _discover_distinct_values(query: str, var_name: str, dc, limit: int = 50) -> list | None:
    """For an enum variable that lacks allowed_values, infer the column +
    source table from the user's SQL and run SELECT DISTINCT to populate.

    Heuristics:
    - Find ``<COL> IN (:varname)`` or ``<COL> = :varname`` → column.
    - Find the first ``FROM <ident>`` in the query → table.
    - Run ``SELECT DISTINCT "<COL>" FROM <table> WHERE "<COL>" IS NOT NULL
      FETCH FIRST <limit> ROWS ONLY``.

    Returns the distinct values as a Python list, or None if column / table
    couldn't be inferred or the query failed. Values are jsonable strings /
    numbers (dates / timestamps stringified via isoformat).
    """
    import re as _re

    name = _re.escape(var_name)
    col_patterns = [
        rf"\b([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(\s*:{name}\s*\)",
        rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*:{name}\b",
    ]
    column = None
    for pat in col_patterns:
        m = _re.search(pat, query, _re.IGNORECASE)
        if m:
            column = m.group(1)
            break
    if not column:
        return None

    # First FROM table (schema.table or table). JOINs would need smarter
    # inference; v0 sticks with the leading FROM.
    m = _re.search(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_.]*)", query, _re.IGNORECASE)
    if not m:
        return None
    table = m.group(1)

    sql = (
        f'SELECT DISTINCT "{column}" '
        f'FROM {table} '
        f'WHERE "{column}" IS NOT NULL '
        f'FETCH FIRST {limit} ROWS ONLY'
    )
    # ``block::`` prefix routes DEV's _StubDataClient through DuckDB (SQL
    # execute) instead of returning the fake_db table verbatim. In PROD,
    # DataClient ignores ``dataset`` and just runs ``query`` against Oracle.
    df = dc.get_data(
        base_prefix=None,
        dataset=f"block::discover/{table}/{column}",
        query=sql,
        query_params={},
    )
    if df is None or len(df.columns) == 0 or len(df) == 0:
        return None
    raw_vals = df.iloc[:, 0].dropna().tolist()
    out: list = []
    for v in raw_vals:
        if hasattr(v, "isoformat"):
            out.append(v.isoformat())
        elif isinstance(v, (int, float, str, bool)):
            out.append(v)
        else:
            out.append(str(v))
    return out


def _apply_df_to_block(block: dict, df, *, engine: str, query: str) -> None:
    """Push a result DataFrame back into the manifest block (data_source +
    config), so the renderer picks it up unchanged."""
    from .nodes.execute_block_sqls import apply_data_to_config
    import datetime as _dt

    df = df.reset_index(drop=True) if hasattr(df, "reset_index") else df
    columns = [str(c) for c in df.columns]
    total = int(len(df))
    rows = [
        [duck._jsonable(v) for v in row]
        for row in df.itertuples(index=False, name=None)
    ]
    block["data_source"] = {
        "sql":          query,
        "original_sql": query,
        "rewritten":    False,
        "truncated":    False,
        "cap":          total,
        "reason":       engine,
        "executed_at":  _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "row_count":    total,
        "columns":      columns,
        "preview_rows": rows[:5],
        "rows":         rows,
        "view_name":    f"v_{block['id']}",
        "engine":       engine,
    }
    block.pop("data_stale", None)
    apply_data_to_config(block, block["data_source"])


def _derive_from_parent(conn, parent, block, resolved):
    """Filter the parent's cached view down to the narrower resolved set.

    Spec §4.3: when ``is_subset(resolved, parent.resolved) is True``, we
    don't need to hit Oracle. Run a DuckDB WHERE on the cached view.

    The mapping from variable name → column name is *not* explicit in the
    Phase 6.5 schema (the user writes raw SQL with :binds). We use a
    heuristic: scan the block's query for ``WHERE / AND <COL> = :<var>``
    or ``IN (:<var>)`` to infer the column. Misses fall back to no filter
    for that variable, which still yields a correct (possibly wider)
    subset since the parent already contained the data.
    """
    import re
    import pandas as pd

    # Build per-variable column inference from the SQL.
    col_for: dict[str, str] = {}
    for var in block.variables:
        # Match `<COL> = :name` or `<COL> IN (:name)` or `<COL> BETWEEN :n AND ...`
        patterns = [
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*:{re.escape(var.name)}\b",
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\s+IN\s*\(\s*:{re.escape(var.name)}\s*\)",
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\s+BETWEEN\s+:{re.escape(var.name)}\b",
        ]
        for pat in patterns:
            m = re.search(pat, block.query, re.IGNORECASE)
            if m:
                col_for[var.name] = m.group(1).upper()
                break

    # Build WHERE filter from resolved values (only for vars we mapped).
    clauses: list[str] = []
    params: dict[str, object] = {}
    for var in block.variables:
        col = col_for.get(var.name)
        if col is None:
            continue
        val = resolved.get(var.name)
        if val is None:
            continue
        if var.type == "date":
            clauses.append(f'"{col}" = ${var.name}_subset')
            params[f"{var.name}_subset"] = val
        elif var.type == "date_range":
            clauses.append(
                f'"{col}" BETWEEN ${var.name}_from_subset AND ${var.name}_to_subset'
            )
            params[f"{var.name}_from_subset"] = val["from"]
            params[f"{var.name}_to_subset"] = val["to"]
        elif var.type == "enum_multi":
            placeholders = [f"${var.name}_subset_{i}" for i in range(len(val))]
            for i, v in enumerate(val):
                params[f"{var.name}_subset_{i}"] = v
            clauses.append(f'"{col}" IN ({", ".join(placeholders)})')
        elif var.type == "enum_single":
            clauses.append(f'"{col}" = ${var.name}_subset')
            params[f"{var.name}_subset"] = val
        elif var.type == "number_range":
            clauses.append(
                f'"{col}" BETWEEN ${var.name}_min_subset AND ${var.name}_max_subset'
            )
            params[f"{var.name}_min_subset"] = val["min"]
            params[f"{var.name}_max_subset"] = val["max"]

    sql = f'SELECT * FROM "{parent.view_name}"'
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    if params:
        return conn.execute(sql, params).fetchdf()
    return conn.execute(sql).fetchdf()


@presentations_bp.route("/help.json")
@login_required
def help_doc():
    """Serve the help catalog (plot types + examples). Hot-reloaded from disk
    so editing help.json doesn't need a restart."""
    help_path = Path(__file__).parent / "help.json"
    if not help_path.exists():
        return Response(
            json.dumps({"error": "help.json bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )
    try:
        data = help_path.read_text(encoding="utf-8")
    except Exception as exc:
        current_app.logger.exception("help_doc failed")
        return Response(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )
    return Response(data, mimetype="application/json")

@presentations_bp.route("/<pid>/uploads", methods=["GET"])
@login_required
def list_uploads(pid: str):
    """Return the metadata of all uploads attached to this presentation."""
    session = _get_session(pid)
    manifest = session.get_manifest()
    if manifest is None:
        return Response(json.dumps({"uploads": []}), mimetype="application/json")
    return Response(
        json.dumps({"uploads": manifest.get("uploads", [])}, ensure_ascii=False),
        mimetype="application/json",
    )
 
 
@presentations_bp.route("/<pid>/uploads/parse", methods=["POST"])
@login_required
def parse_upload(pid: str):
    """Parse an uploaded file OR pasted TSV WITHOUT saving — preview only.
 
    Body modes:
      A. multipart form with `file` field      → parse xlsx
      B. JSON {"paste": "...", "table_name": "x", "has_header": bool|null} → parse TSV
    """
    if "file" in request.files:
        f = request.files["file"]
        body = f.read()
        if len(body) > MAX_UPLOAD_BYTES:
            return Response(
                json.dumps({
                    "error": f"Dosya çok büyük (maksimum {MAX_UPLOAD_BYTES // 1024 // 1024} MB).",
                    "kind": "too_large",
                }, ensure_ascii=False),
                status=413, mimetype="application/json",
            )
        try:
            sheets = parse_xlsx(body)
        except ValueError as exc:
            return Response(
                json.dumps({"error": str(exc), "kind": "parse"}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
        return Response(
            json.dumps({
                "kind": "xlsx",
                "filename": f.filename or "yuklenen.xlsx",
                "size": len(body),
                "sheets": sheets,
            }, ensure_ascii=False, default=str),
            mimetype="application/json",
        )
 
    body = request.get_json(silent=True) or {}
    paste_text = body.get("paste") or ""
    if not paste_text.strip():
        return Response(
            json.dumps({"error": "Dosya veya yapıştırma içeriği gerekli."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
 
    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    existing_names = {
        u.get("filename", "") for u in (manifest.get("uploads") or [])
    }
    default_name = next_paste_name(existing_names)
    table_name = (body.get("table_name") or default_name).strip()
 
    try:
        sheet = parse_pasted_tsv(
            paste_text,
            table_name=table_name,
            has_header=body.get("has_header"),
        )
    except ValueError as exc:
        return Response(
            json.dumps({"error": str(exc), "kind": "parse"}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )
 
    return Response(
        json.dumps({
            "kind": "paste",
            "filename": table_name,
            "sheets": [sheet],
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )
 
 
@presentations_bp.route("/<pid>/uploads", methods=["POST"])
@login_required
def commit_upload(pid: str):
    """Commit a parsed upload to S3 and attach it to the manifest.
 
    Body modes:
      A. multipart form with `file`            → re-parse + save xlsx
      B. JSON {"paste": ..., "table_name": ..., "has_header": ...} → TSV → xlsx → save
    """
    # ── multipart (xlsx) ──
    if "file" in request.files:
        f = request.files["file"]
        body = f.read()
        if len(body) > MAX_UPLOAD_BYTES:
            return Response(
                json.dumps({"error": "Dosya çok büyük."}, ensure_ascii=False),
                status=413, mimetype="application/json",
            )
        try:
            sheets = parse_xlsx(body)
        except ValueError as exc:
            return Response(
                json.dumps({"error": str(exc)}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
        original_filename = f.filename or "yuklenen.xlsx"
        xlsx_bytes = body
 
    # ── JSON paste ──
    else:
        body_json = request.get_json(silent=True) or {}
        paste_text = body_json.get("paste") or ""
        if not paste_text.strip():
            return Response(
                json.dumps({"error": "Yapıştırılan içerik yok."}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
        session = _get_session(pid)
        manifest_pre = session.get_manifest() or {}
        existing = {u.get("filename", "") for u in (manifest_pre.get("uploads") or [])}
        table_name = (body_json.get("table_name") or next_paste_name(existing)).strip()
        try:
            sheet = parse_pasted_tsv(
                paste_text,
                table_name=table_name,
                has_header=body_json.get("has_header"),
            )
        except ValueError as exc:
            return Response(
                json.dumps({"error": str(exc)}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
 
        # Reconstruct DataFrame from preview_rows + headers (we already parsed
        # the whole sheet upstream, but parse_pasted_tsv didn't keep the full
        # DataFrame; re-parse the raw text once more for full data).
        # ↓ rebuild full DataFrame for save:
        import pandas as pd, io
        full_df = _full_df_from_tsv(paste_text, body_json.get("has_header"))
        xlsx_bytes = df_to_xlsx_bytes(full_df, sheet_name=sheet["display_name"][:31] or "Sheet1")
        sheets = [sheet]
        original_filename = f"{table_name}.xlsx"
 
    # ── Persist ──
    upload_id = new_upload_id()
    s3_key = upload_s3_key(current_user.sicil, upload_id)
 
    try:
        _s3_put(s3_key, xlsx_bytes)
    except Exception as exc:
        current_app.logger.exception("commit_upload: S3 put failed")
        return Response(
            json.dumps({"error": f"Yükleme başarısız: {exc}"}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )
 
    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    uploads_list = list(manifest.get("uploads") or [])
 
    upload_meta = {
        "id":               upload_id,
        "filename":         original_filename,
        "s3_key":           s3_key,
        "size":             len(xlsx_bytes),
        "uploaded_at":      datetime.now(timezone.utc).isoformat(),
        "uploaded_by":      current_user.sicil,
        "sheets":           sheets,
    }
    uploads_list.append(upload_meta)
    manifest["uploads"] = uploads_list
    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)
 
    return Response(
        json.dumps({
            "ok": True,
            "upload": upload_meta,
            "version": manifest["version"],
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )
 
 
@presentations_bp.route("/<pid>/uploads/<upload_id>", methods=["DELETE"])
@login_required
def delete_upload(pid: str, upload_id: str):
    """Remove an upload from S3 and the manifest. Idempotent."""
    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    uploads_list = manifest.get("uploads") or []
    upload = next((u for u in uploads_list if u.get("id") == upload_id), None)
    if upload is None:
        return Response(
            json.dumps({"error": f"Upload {upload_id!r} bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )
 
    # Try S3 delete but don't fail the request if S3 is flaky — manifest is
    # the source of truth for the UI.
    try:
        _s3_delete(upload["s3_key"])
    except Exception as exc:
        current_app.logger.warning("delete_upload: S3 delete failed for %s: %s",
                                   upload["s3_key"], exc)
 
    manifest["uploads"] = [u for u in uploads_list if u.get("id") != upload_id]
    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)
 
    return Response(
        json.dumps({"ok": True, "version": manifest["version"]}, ensure_ascii=False),
        mimetype="application/json",
    )


# ════════════════════════════════════════════════════════════════════════
# Phase 7.d — user-scoped concepts (per-presentation) + promotion
# ════════════════════════════════════════════════════════════════════════

def _base_registry_snapshot():
    reg = current_app.config.get("CONCEPT_REGISTRY")
    if reg is None:
        return None
    return reg.snapshot if hasattr(reg, "snapshot") else reg


@presentations_bp.route("/<pid>/concepts", methods=["GET"])
@login_required
def list_presentation_concepts(pid: str):
    """Effective concepts for a presentation: base (global+dept) ⊕ user.

    Returns ``{concepts: [...], user_concepts: [...]}`` where each concept
    carries its scope so the UI can flag user-defined ones.
    """
    from .concepts.user_scope import build_effective_registry

    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    base = _base_registry_snapshot()
    if base is None:
        return Response(json.dumps({"concepts": [], "user_concepts": []}),
                        mimetype="application/json")
    user_raw = manifest.get("user_concepts") or []
    eff = build_effective_registry(base, user_raw)

    def _rank(c):
        s = c.scope or ""
        return (0 if s == "global" else 1 if s.startswith("dept:") else 2, c.id)

    concepts = sorted(eff.all_concepts(), key=_rank)
    return Response(
        json.dumps({
            "concepts": [c.model_dump(mode="json", exclude_none=True) for c in concepts],
            "user_concepts": user_raw,
        }, ensure_ascii=False, default=str),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/concepts", methods=["POST"])
@login_required
def add_presentation_concept(pid: str):
    """Add a user-scoped concept to this presentation.

    Body: a concept dict (id, name, type, canonical_values, ...). Validated
    against the base registry — an id colliding with a global/dept concept is
    rejected (extension-only, §10.5).
    """
    from .concepts.user_scope import validate_user_concept, UserConceptError

    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
                        status=404, mimetype="application/json")
    base = _base_registry_snapshot()
    if base is None:
        return Response(json.dumps({"error": "Concept registry yapılandırılmamış."},
                                   ensure_ascii=False),
                        status=500, mimetype="application/json")

    raw = request.get_json(silent=True) or {}
    try:
        concept = validate_user_concept(base, raw)
    except UserConceptError as exc:
        return Response(json.dumps({"error": str(exc), "kind": "user_concept"},
                                   ensure_ascii=False),
                        status=400, mimetype="application/json")

    user_concepts = list(manifest.get("user_concepts") or [])
    # Replace an existing same-id user concept (edit), else append.
    cid = concept.id
    user_concepts = [c for c in user_concepts if c.get("id") != cid]
    user_concepts.append(concept.model_dump(mode="json", exclude_none=True))
    manifest["user_concepts"] = user_concepts
    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)

    return Response(
        json.dumps({"ok": True, "version": manifest["version"],
                    "concept": concept.model_dump(mode="json", exclude_none=True)},
                   ensure_ascii=False, default=str),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/concepts/<cid>", methods=["DELETE"])
@login_required
def delete_presentation_concept(pid: str, cid: str):
    """Remove a user-scoped concept from this presentation."""
    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
                        status=404, mimetype="application/json")
    user_concepts = list(manifest.get("user_concepts") or [])
    new_list = [c for c in user_concepts if c.get("id") != cid]
    if len(new_list) == len(user_concepts):
        return Response(json.dumps({"error": f"Kullanıcı kavramı '{cid}' bulunamadı."},
                                   ensure_ascii=False),
                        status=404, mimetype="application/json")
    manifest["user_concepts"] = new_list
    manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(manifest)
    return Response(json.dumps({"ok": True, "version": manifest["version"]},
                               ensure_ascii=False), mimetype="application/json")


@presentations_bp.route("/<pid>/concepts/<cid>/promote", methods=["POST"])
@login_required
def promote_presentation_concept(pid: str, cid: str):
    """Record a promotion intent (user concept → departmental).

    Phase 7 only records the intent in the promotions ledger; a review queue
    UI lands in Phase 11 (spec §3.4).
    """
    from .concepts.promotions import record_promotion

    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return Response(json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
                        status=404, mimetype="application/json")
    user_concepts = manifest.get("user_concepts") or []
    concept = next((c for c in user_concepts if c.get("id") == cid), None)
    if concept is None:
        return Response(json.dumps({"error": f"Kullanıcı kavramı '{cid}' bulunamadı."},
                                   ensure_ascii=False),
                        status=404, mimetype="application/json")

    body = request.get_json(silent=True) or {}
    target = (body.get("target_scope") or "dept:treasury").strip()
    try:
        import presentations as _pkg
        from pathlib import Path as _Path
        root = current_app.config.get("CONCEPT_CATALOG_ROOT") or \
            (_Path(_pkg.__file__).parent / "catalog")
        entry = record_promotion(
            root, concept=concept, presentation_id=pid,
            requested_by=getattr(current_user, "sicil", "unknown"),
            target_scope=target,
        )
    except Exception as exc:
        current_app.logger.exception("record_promotion failed")
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False),
                        status=500, mimetype="application/json")
    return Response(json.dumps({"ok": True, "promotion": entry},
                               ensure_ascii=False, default=str),
                    mimetype="application/json")


@presentations_bp.route("/<pid>/concepts/filter-suggestions", methods=["GET"])
@login_required
def concept_filter_suggestions(pid: str):
    """Propose dashboard filters from the blocks' concept bindings (Phase 7).

    Concept-native blocks carry ``source_tables`` (not ``variables``), so the
    old variable-based suggestion engine finds nothing. This walks every
    block's source tables, collects the concepts those tables bind to
    (human_verified only), and proposes a filter per concept that isn't
    already on the dashboard. The React filter modal merges these with the
    legacy variable-based suggestions.
    """
    from .manifest import iter_all_blocks
    from .concepts.user_scope import build_effective_registry
    from .concepts.integration import derive_source_tables

    session = _get_session(pid)
    manifest = session.get_manifest() or {}
    base = _base_registry_snapshot()
    catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")
    if base is None or catalog is None:
        return Response(json.dumps({"suggestions": []}), mimetype="application/json")

    cat = catalog.snapshot if hasattr(catalog, "snapshot") else catalog
    eff = build_effective_registry(base, manifest.get("user_concepts"))
    existing = {f.get("concept_ref") or f.get("semantic_tag")
                for f in (manifest.get("filters") or [])}

    seen: set[str] = set()
    suggestions: list[dict] = []
    block_count: dict[str, int] = {}

    for block in iter_all_blocks(manifest):
        # Explicit source_tables, else derived from the block's FROM clause.
        for (schema, table) in derive_source_tables(block):
            for b in cat.get_bindings(schema, table):   # human_verified only
                cid = b.concept
                block_count[cid] = block_count.get(cid, 0) + 1
                if cid in existing or cid in seen:
                    continue
                concept = eff.get(cid)
                if concept is None:
                    continue
                seen.add(cid)
                suggestions.append(_filter_proposal_from_concept(concept))

    for s in suggestions:
        s["block_count"] = block_count.get(s["semantic_tag"], 1)

    return Response(
        json.dumps({"suggestions": suggestions}, ensure_ascii=False, default=str),
        mimetype="application/json",
    )


def _filter_proposal_from_concept(concept) -> dict:
    """Build a dashboard-filter proposal dict from a concept definition."""
    codes = [cv.code for cv in concept.canonical_values]
    if concept.type == "time":
        return {
            "id": "f_" + concept.id,
            "semantic_tag": concept.id,
            "type": "date_range",
            "label": concept.name,
            "default": {"from": "today - 30d", "to": "today"},
            "source": "concept",
        }
    # enum / bucket / scalar → enum_multi (multi-select); default = all codes
    # so the initial state shows everything and the user narrows.
    return {
        "id": "f_" + concept.id,
        "semantic_tag": concept.id,
        "type": "enum_multi",
        "label": concept.name,
        "allowed_values": codes,
        "default": codes,
        "source": "concept",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_session(pid: str):
    """Resolve the per-(user, presentation) session via the app's registry."""
    registry = current_app.config["SESSION_REGISTRY"]
    return registry.get_or_create(current_user.sicil, pid)


def _seed_manifest(pid: str) -> dict | None:
    """Seed for the demo presentation only; new sessions start empty.
    Always migrated to nested form."""
    if pid == "p_demo":
        try:
            return ensure_nested(json.loads(_DEMO_MANIFEST.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
    return None


def _empty_manifest(pid: str) -> dict:
    return {
        "id": pid,
        "version": 1,
        "owner_id": current_user.sicil,
        "meta": {
            "title": "Yeni Sunum",
            "eyebrow": "Treasury Report",
            "date": "",
            "author_label": current_user.sicil,
        },
        "basket": [],
        "blocks": [],
    }


def _s3_put(key: str, body: bytes) -> None:
    current_app.config["S3_PUT"](key, body)
 
def _s3_get(key: str) -> bytes:
    return current_app.config["S3_GET"](key)
 
def _s3_delete(key: str) -> None:
    current_app.config["S3_DELETE"](key)
 

def _full_df_from_tsv(raw_text: str, has_header: bool | None):
    """Re-parse pasted TSV and return the full DataFrame for xlsx export."""
    import io
    import pandas as pd
    from .uploads import _coerce_series, looks_like_header
 
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    rows = [line.split("\t") for line in raw_text.split("\n")]
    width = max(len(r) for r in rows)
    rows = [r + [None] * (width - len(r)) for r in rows]
 
    if has_header is None:
        from .uploads import _coerce_row
        second = _coerce_row(rows[1]) if len(rows) > 1 else None
        has_header = looks_like_header(rows[0], second)
 
    if has_header:
        df = pd.DataFrame(rows[1:], columns=rows[0])
    else:
        df = pd.DataFrame(rows, columns=[f"col_{i+1}" for i in range(width)])
 
    for col in df.columns:
        df[col] = _coerce_series(df[col])
    return df
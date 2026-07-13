import json
import logging
import re
import secrets

log = logging.getLogger(__name__)

# M6 (madde 8) — Oracle ':name' bind'lerini DuckDB '$name'e çevir (DuckDB ':name'
# kabul etmez, '$name' + dict ile bind eder). ':' string/'::' cast'ı bozmamak için
# yalnız kelime-başı bind'i hedefler; param dict anahtarları (':'siz) aynı kalır.
_ORACLE_BIND_RE = re.compile(r'(?<![:\w]):([A-Za-z_]\w*)')


def _oracle_binds_to_duckdb(sql: str) -> str:
    return _ORACLE_BIND_RE.sub(r'$\1', sql or "")
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

# Seed manifest used the first time p_demo is opened (optional; absent → boş sunum).
_DEMO_MANIFEST = Path(__file__).parent.parent / "dev_data" / "sample_manifest.json"

# Ephemeral chat job table: token → job dict. Consumed by SSE stream.
_CHAT_JOBS: dict[str, dict] = {}


# ── Pages ────────────────────────────────────────────────────────────────────

@presentations_bp.route("/")
@login_required
def list_presentations():
    """Legacy alias — old /presentations/ now lands on the Sunum pipeline
    checkpoint page. The previous tabbed UI (Sunumlar + Bloklar) was
    retired; presentation cards live under Pipeline > Sunum and blocks
    under Kütüphane > Bloklar.
    """
    from flask import redirect, url_for
    return redirect(url_for("presentations.pipeline_sunum"))


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
    TableDocStore, no static fallback) so Sunum's basket reads the same
    table universe Keşif sees.
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
    from presentations.manifest import (
        _validate_chart_length, ALLOWED_PATCH_PREFIXES, iter_all_blocks,
    )

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
    # toggle doesn't touch chart data — but be safe). NESTED gez: manifest
    # yapısı section_header.children (+ carousel/canvas) — düz blocks[]
    # taraması leaf chart'ları atlıyordu, bozuk seri/kategori uzunlukları
    # sessizce kabul ediliyordu.
    invariant_errors = []
    for block in iter_all_blocks(new_manifest):
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


@presentations_bp.route("/snapshot/<sid>", methods=["DELETE"])
@login_required
def delete_snapshot(sid: str):
    """Permanently remove a snapshot (manifest + meta).

    Auth: only the owner can delete. If the snapshot is bound to one or
    more experts, those experts' briefing caches are invalidated so the
    next render no longer cites the dead snapshot. Returns 404 if the
    snapshot is gone, 403 if the caller isn't the owner.
    """
    store = current_app.config["SNAPSHOT_STORE"]
    payload = store.load(sid)
    if payload is None:
        return Response(
            json.dumps({"error": "Snapshot bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    meta = payload.get("meta") or {}
    owner = meta.get("owner_id")
    if owner and owner != current_user.sicil:
        return Response(
            json.dumps({
                "error": "Bu snapshot'ı sadece sahibi silebilir.",
                "owner": owner,
            }, ensure_ascii=False),
            status=403, mimetype="application/json",
        )

    ok = store.delete(sid)
    if not ok:
        return Response(
            json.dumps({"error": "Silme başarısız."}, ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    # Invalidate briefing cache for every expert this snapshot was bound
    # to — otherwise a freshly-deleted snapshot lingers in cited citations
    # until the per-expert TTL expires.
    engine = current_app.config.get("BRIEFING_ENGINE")
    bound = meta.get("bound_experts") or []
    if engine is not None and hasattr(engine, "invalidate"):
        for eid in bound:
            try:
                engine.invalidate(eid)
            except Exception:
                log.warning("delete_snapshot: cache invalidate failed for expert=%s",
                            eid, exc_info=True)

    log.info("snapshot deleted: %s (owner=%s, bound_experts=%s)", sid, owner, bound)
    return Response(
        json.dumps({"ok": True, "snapshot_id": sid}, ensure_ascii=False),
        mimetype="application/json",
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
    dc = current_app.config.get("DATA_CLIENT")
    try:
        with session.duck_conn() as conn:
            # Lazy refetch if the basket isn't loaded yet.
            manifest = session.get_manifest()
            if manifest and session.needs_refetch(manifest.get("basket", [])):
                if dc is not None:
                    session.fetch_basket(dc, manifest.get("basket", []))
            # Scope-derived views (manuel SQL / join / filter / aggregate) aren't
            # persisted in the session DuckDB file — build registers them in-memory,
            # so a fresh conn doesn't have them. Hydrate the requested view (+ its
            # sources) on demand, exactly like the block-run path, otherwise
            # preview_view 500s with "Table … does not exist".
            try:
                from presentations.routes_scope import (
                    hydrate_block_datasets, load_scope_for_manifest,
                )
                _scope = load_scope_for_manifest(manifest)
                if _scope is not None and dc is not None:
                    hydrate_block_datasets(dc, conn, _scope, f'SELECT * FROM "{view_name}"')
            except Exception:
                current_app.logger.warning(
                    "duckdb_preview: scope hydrate failed for %s", view_name, exc_info=True)
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
        with session.duck_conn() as conn:
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
        all_patches: list = []          # B1 (N3) — audit: LLM'in ürettiği patch'ler (kod)

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

                    if event["event"] == "patch" and isinstance(data.get("patches"), list):
                        all_patches.extend(data["patches"])   # B1 — üretilen kod (audit)

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
                    # M5 (madde 7) — concept filtre seed'i BUILD'e taşındı
                    # (_run_build_core: her Hazırlık→Sunum geçişinde cross-check).
                    # Chat-stream-only seed KALDIRILDI (ilk promptta yüklenmiyordu).
                    m["version"] = m.get("version", 0) + 1
                    sess2.set_manifest(m)
                except Exception:
                    app.logger.exception("chat history persist failed (non-fatal)")

                # Audit (Oturum 8 / H1) — kim ne prompt yazdı, asistan ne döndü,
                # hangi session/kullanıcı. Best-effort (audit.log_event hata yutar).
                try:
                    from presentations import audit
                    audit.log_event(
                        "llm_chat", user_sicil=sicil, presentation_id=pid,
                        request_id=token, stage="sunum", prompt=user_msg,
                        llm_response=" | ".join(a.get("text", "") for a in assistant_msgs),
                        sql_text=(json.dumps(all_patches, ensure_ascii=False, default=str)
                                  if all_patches else None),   # B1 — üretilen kod (patch'ler)
                        meta={"assistant_msgs": len(assistant_msgs),
                              "patch_count": len(all_patches)},
                    )
                except Exception:
                    pass

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
        with session.duck_conn() as conn:
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

    # ── Parametreli (Phase 6.5 run-manual) blok ───────────────────────────
    # Kayıtlı query :bind'ler içerir; çıplak koşmak DEV'de parser error,
    # Oracle'da ORA-01008 (not all variables bound) demektir. run-manual ile
    # AYNI çekirdekten geç: değişken default'ları (+ body.variable_overrides)
    # çözülür, bind'ler positional expand edilir, gate + routing uygulanır.
    variables_raw = block.get("variables") or []
    if variables_raw:
        from .blocks.schema import Variable
        try:
            var_models = [Variable.model_validate(v) for v in variables_raw]
        except Exception as exc:
            return Response(
                json.dumps({"error": f"Blok değişkenleri okunamadı: {exc}",
                            "kind": "block_schema"}, ensure_ascii=False),
                status=400, mimetype="application/json",
            )
        overrides = body.get("variable_overrides") or {}
        # Query: body.sql (UI düzenlemesi) > kayıtlı block.query > data_source.
        query = new_sql or block.get("query") or sql
        new_ds, err = _execute_manual_block_sql(
            session, manifest, bid, block, query, var_models, overrides)
        if err is not None:
            return err
        block["query"] = query
        block["data_source"] = new_ds
        block.pop("data_stale", None)
        from .nodes.execute_block_sqls import apply_data_to_config
        apply_data_to_config(block, new_ds)
        manifest["version"] = manifest.get("version", 0) + 1
        session.set_manifest(manifest)
        return Response(
            json.dumps({"ok": True, "version": manifest["version"], "block": block},
                       ensure_ascii=False, default=str),
            mimetype="application/json",
        )

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
        with session.duck_conn() as conn:
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

    # Phase B+ — if this block is library-cache eligible, push the fresh
    # rows into the shared cache so other viewers see them too. Default-vars
    # cache key is used (refresh from this endpoint does NOT carry the
    # caller's filter overlays).
    lib_cache = current_app.config.get("LIBRARY_BLOCK_CACHE")
    if lib_cache is not None:
        try:
            from .cache.library_block_integration import maybe_write_library_cache
            import pandas as _pd
            df_for_cache = _pd.DataFrame(
                new_ds.get("rows") or [], columns=new_ds.get("columns") or [],
            )
            wrote = maybe_write_library_cache(
                block=block, resolved_vars={}, df=df_for_cache,
                sql=new_ds.get("original_sql") or sql, cache=lib_cache,
            )
            if wrote:
                current_app.logger.info(
                    "library cache warmed via manual refresh: block=%s lib=%s",
                    bid, (block.get("imported_from") or {}).get("library_id"),
                )
        except Exception:
            current_app.logger.warning(
                "library cache warm on manual refresh failed", exc_info=True,
            )

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
    "combo_chart": "bar_chart",
    "pie_chart": "pie",
    "heatmap": "table",
    "radial_bar": "kpi",
    "data_table": "table",
}


def _standin_viz_type(manifest_type: str | None) -> str:
    """Map a manifest block type → a valid Phase 6.5 VizType (default 'table')."""
    return _STANDIN_VIZ_TYPE.get(manifest_type or "", "table")


def _execute_manual_block_sql(session, manifest, bid: str, block: dict,
                              query: str, var_models: list, overrides: dict):
    """Phase 6.5 manuel-SQL yürütme ÇEKİRDEĞİ — run-manual ve refresh paylaşır.

    Standin Block → resolve_variables (default + overrides) → expand_binds
    (değerler ASLA SQL'e gömülmez, positional :bind'ler) → concept sentinel
    nötralize → scope dataset'leri hydrate → aggregation gate → DuckDB/Oracle
    routing. Başarıda ``(new_ds, None)``, hatada ``(None, <Flask Response>)``
    döner. (refresh eskiden ham query'yi bind'siz koşuyordu → :var'lı blokta
    DEV parser error / Oracle ORA-01008; tek çekirdek bu drift'i kapatır.)
    """
    from .blocks.schema import Block
    from .variables.resolver import resolve_variables, ResolutionError
    from .sql.binder import expand_binds
    from .concepts.integration import strip_concept_sentinel

    def _err(payload: dict, status: int):
        return None, Response(json.dumps(payload, ensure_ascii=False),
                              status=status, mimetype="application/json")

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
        return _err({"error": str(exc), "kind": "block_schema"}, 400)

    try:
        resolved = resolve_variables(stand_in, overrides)
    except ResolutionError as exc:
        return _err({"error": "; ".join(exc.errors), "kind": "resolution"}, 400)

    try:
        bound = expand_binds(stand_in, resolved)
    except ValueError as exc:
        return _err({"error": str(exc), "kind": "bind"}, 400)

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _err({"error": "DATA_CLIENT yapılandırılmamış.", "kind": "config"}, 500)

    # Bind params are routed through DataClient.get_data (DEV stub passes them
    # to DuckDB; prod oracledb honours :name binds natively).
    # Neutralize an un-injected {{concept_filters}} sentinel — a manual run has
    # no dashboard concept filters, so the sentinel becomes a no-op 1 = 1.
    exec_sql = strip_concept_sentinel(bound.sql)
    gate = None
    try:
        # Route to DuckDB when the SQL references scope datasets (Hazırlık-
        # produced manual-SQL / filter / aggregate nodes are session DuckDB
        # views, not Oracle tables) or uploads; else Oracle. Without this a
        # block sourced from such a node hits ORA-00942 (table or view).
        upload_lookup = duck.build_upload_lookup(manifest)
        s3_get = current_app.config.get("S3_GET")
        from .routes_scope import hydrate_block_datasets, load_scope_for_manifest
        _scope = load_scope_for_manifest(manifest)
        scope_aliases = [b.alias for b in _scope.basket] if _scope is not None else []
        with session.duck_conn() as conn:
            if _scope is not None:
                hydrate_block_datasets(dc, conn, _scope, exec_sql)
            from .aggregation_gate import validate_and_wrap
            _names = [v for v in duck.list_views(conn) if not v.startswith("block_preview_")]
            _will_duck = bool(duck.find_upload_refs(exec_sql)) or bool(
                duck.find_view_refs(exec_sql, list({*_names, *scope_aliases})))
            gate = validate_and_wrap(exec_sql, dialect="duckdb" if _will_duck else None)
            df, _engine = duck.run_block_sql_routed(
                dc, conn, bid, gate.sql, bound.params,
                upload_lookup=upload_lookup, s3_get=s3_get,
                extra_view_names=scope_aliases,
            )
    except GateError as exc:
        return _err({"error": str(exc), "kind": "gate"}, 400)
    except Exception as exc:
        current_app.logger.exception("manual block SQL execution failed (%s)", bid)
        msg = str(exc).strip().splitlines()[0][:240]
        return _err({"error": msg, "kind": "oracle"}, 500)

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
        "sql":           gate.sql if gate is not None else exec_sql,
        "original_sql":  query,
        "rewritten":     bool(gate.rewritten) if gate is not None else False,
        "truncated":     bool(gate.truncated) if gate is not None else False,
        "cap":           gate.cap if gate is not None else total_rows,
        "reason":        gate.reason if gate is not None else "manual_sql",
        "executed_at":   datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "row_count":     total_rows,
        "columns":       columns,
        "column_types":  duck.infer_column_kinds(df),
        "preview_rows":  all_rows[:5],
        "rows":          all_rows,
        "view_name":     f"v_{bid}",
        "engine":        "manual_sql",
        "bind_params":   {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                          for k, v in bound.params.items()},
    }
    return new_ds, None


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
    from .blocks.schema import Variable
    from .sql.validator import validate_sql
    from .nodes.execute_block_sqls import apply_data_to_config

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
                    # Madde 5 — lifted :bind'ler concept filtreye taşındı; SQL'i
                    # ve blok değişkenlerini de güncelle ki orphan :bind kalmasın
                    # (rewritten_sql'de o bind artık yok).
                    lifted = set(res.get("lifted_binds") or [])
                    if lifted:
                        block["query"] = res["rewritten_sql"]
                        block["variables"] = [
                            v for v in (block.get("variables") or [])
                            if v.get("name") not in lifted
                        ]
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

    # Ortak yürütme çekirdeği (standin → resolve → bind → gate → routing).
    # refresh_block_data parametreli bloklarda AYNI çekirdeği kullanır.
    new_ds, err = _execute_manual_block_sql(
        session, manifest, bid, block, query, var_models, overrides)
    if err is not None:
        return err

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
    from .cache.block_cache import (
        BlockCache, cache_key as _cache_key,
        concept_filters_digest as _concept_filters_digest,
    )
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

    # All DuckDB work below holds the per-session execution lock (reentrant)
    # via session.duck_conn(); the connection is shared and not thread-safe.
    with session.duck_conn() as conn:
        cache = BlockCache(conn)
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return Response(
            json.dumps({"error": "DATA_CLIENT yapılandırılmamış.", "kind": "config"},
                       ensure_ascii=False),
            status=500, mimetype="application/json",
        )

    # Faz B — register the dashboard scope's materialised cached datasets as
    # DuckDB views (read from S3 parquet, NEVER Oracle). Dataset-bound blocks in
    # the loop below project their columns from these views locally.
    from .routes_scope import load_scope_for_manifest
    _scope = load_scope_for_manifest(manifest)
    if _scope is not None:
        try:
            from .scope.materialize import load_into_duck
            with session.duck_conn() as conn:
                load_into_duck(dc, conn, _scope)
        except Exception:
            current_app.logger.warning(
                "apply-filters: load_into_duck failed", exc_info=True)

    # M6/K8 — Bir bloğun (resolve+inject edilmiş) SQL'i bir Hazırlık basket VIEW'ını
    # referans ediyorsa ASLA Oracle'a gitme (view yalnız DuckDB'de → ORA-00942):
    # SESSION DuckDB'de koş. Değişkenli (Phase 6.5) bloklar bind çözümünü Phase 6.5
    # yolunda yaptıktan sonra buraya gelir; Oracle ':name' → DuckDB '$name' çevrilir.
    # View referansı yoksa (saf katalog-tablosu bloğu) Oracle'da kalır.
    _all_basket_aliases = [it.get("alias") for it in (manifest.get("basket") or [])
                           if it.get("alias")]

    # ── Oturum tablo-önbelleği (küçük basket tabloları) ─────────────────
    # Importer basket'e `duck_cache: true` yazar: bu tablolar İLK kullanımda
    # bir kez Oracle'dan oturum DuckDB'sine çekilir (TTL 15 dk), blok SQL'leri
    # Oracle→DuckDB çevirisiyle LOKALDE koşar. Filtre değişimi = sıfır Oracle
    # turu. Çeviri/koşum hatasında sessizce Oracle'a düşülür (yalnız hızlanma
    # kaybolur, davranış bozulmaz).
    _duck_cache_tables = {
        str(it.get("table", "")).upper(): it.get("alias")
        for it in (manifest.get("basket") or [])
        if it.get("duck_cache") and it.get("table") and it.get("alias")
    }
    _TCACHE_TTL = 900.0   # sn — kaynak tablolar pipeline koşusuyla değişir

    def _ensure_table_cached(conn, table, alias):
        import time as _time
        meta = getattr(session, "_table_cache_meta", None)
        if meta is None:
            meta = {}
            session._table_cache_meta = meta
        now = _time.time()
        ts = meta.get(table)
        if ts is not None and now - ts < _TCACHE_TTL:
            return
        df = dc.get_data(base_prefix=None, dataset=f"block::tcache/{alias}",
                         query=f"SELECT * FROM {table}", query_params={})
        src_name = f"_tc_src_{alias}"
        conn.register(src_name, df)
        conn.execute(
            f'CREATE OR REPLACE TABLE "tcache_{alias}" AS SELECT * FROM "{src_name}"')
        try:
            conn.unregister(src_name)
        except Exception:
            pass
        meta[table] = now
        _req_logger.info(
            "table_cache: %s -> tcache_%s (%d satir yuklendi)",
            table, alias, len(df))

    def _try_table_cache(sql, params, block_id):
        if not _duck_cache_tables or not sql:
            return None
        from .sql.oracle_duck import oracle_sql_to_duckdb, find_oracle_table_refs
        t_refs = find_oracle_table_refs(sql)
        if not t_refs or any(r not in _duck_cache_tables for r in t_refs):
            return None
        try:
            with session.duck_conn() as conn:
                duck_sql = sql
                for r in t_refs:
                    _ensure_table_cached(conn, r, _duck_cache_tables[r])
                    duck_sql = re.sub(re.escape(r),
                                      f'tcache_{_duck_cache_tables[r]}',
                                      duck_sql, flags=re.IGNORECASE)
                duck_sql = _oracle_binds_to_duckdb(oracle_sql_to_duckdb(duck_sql))
                used = {k: v for k, v in (params or {}).items()
                        if f"${k}" in duck_sql}
                df = (conn.execute(duck_sql, used).fetchdf()
                      if used else conn.execute(duck_sql).fetchdf())
            return df
        except Exception:
            _req_logger.warning(
                "table_cache: %s DuckDB yolu basarisiz - Oracle'a dusuluyor",
                block_id, exc_info=True)
            return None

    def _run_block_query(sql, params, block_id):
        refs = duck.find_view_refs(sql, _all_basket_aliases) if sql else []
        if not refs:
            cached_df = _try_table_cache(sql, params, block_id)
            if cached_df is not None:
                return cached_df
            return dc.get_data(base_prefix=None, dataset=f"block::{block_id}",
                               query=sql, query_params=params)
        from .routes_scope import hydrate_block_datasets
        from .aggregation_gate import validate_and_wrap
        duck_sql = _oracle_binds_to_duckdb(sql)
        with session.duck_conn() as conn:
            if _scope is not None:
                hydrate_block_datasets(dc, conn, _scope, sql)
            gate = validate_and_wrap(duck_sql, dialect="duckdb")
            return (conn.execute(gate.sql, params).fetchdf()
                    if params else conn.execute(gate.sql).fetchdf())

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

    # #4 end-to-end — dataset (türetilmiş) node'lara concept filtresi uygulamak için:
    # (a) aktif concept filtrelerinin sade hali, (b) alias → column_concepts haritası.
    _concept_filter_dicts = [
        {"concept": f.concept, "operator": f.operator, "values": list(f.values)}
        for f in resolved_concept_filters
    ]
    # C2b: aktif concept-filtre durumunu cache anahtarına kat → bir filtre
    # değişince sentinel+değişken taşıyan bloklar bayat cache yerine yeniden
    # yürür. Boşken digest "" → anahtar pre-Phase-7 ile birebir (regresyon yok).
    _concept_digest = _concept_filters_digest(_concept_filter_dicts)
    _alias_column_concepts = {
        item.get("alias"): (item.get("column_concepts") or {})
        for item in (manifest.get("basket") or [])
        if item.get("alias")
    }
    # Base→türev binding mirası için: alias → kaynak SCHEMA.TABLE referansı.
    # Türetilmiş node'larda ref tablo formatında değildir → miras devreye girmez.
    _alias_table_ref = {
        item.get("alias"): (item.get("table") or "")
        for item in (manifest.get("basket") or [])
        if item.get("alias")
    }

    # Auto-binding fallback'i için dashboard filtre modelleri (bozuk kayıt
    # tek tek atlanır — bir filtre şeması bütün apply'ı düşürmesin).
    _dash_filter_models: list[DashboardFilter] = []
    for _f in (manifest.get("filters") or []):
        try:
            _dash_filter_models.append(DashboardFilter.model_validate(_f))
        except Exception:
            continue

    # ── Paralel blok yürütme ────────────────────────────────────────────
    # Her blok kendi Oracle bağlantısını açıp kapatıyor (DataClient.get_data)
    # → 18 blokluk bir dashboard'da seri döngü ~10-12 sn sürüyordu. Blok
    # işleme thread havuzunda koşar; DuckDB erişimi session.duck_conn()
    # içindeki reentrant kilitle zaten serileşir, Oracle fetch'leri örtüşür.
    # Flask request-context thread'lere taşınmaz → logger/sicil/config
    # önden yakalanır.
    _req_logger = current_app.logger
    _req_sicil = current_user.sicil
    _lib_cache_cfg = current_app.config.get("LIBRARY_BLOCK_CACHE")
    _lib_dispatcher_cfg = current_app.config.get("LIBRARY_REFRESH_DISPATCHER")

    def _process_block(block) -> list[dict]:
        """Tek bloğu çöz/koş; sonuç kayıtlarını döndürür. Gövde eski seri
        döngünün birebir taşınmışı: `continue` → `return results`."""
        results: list[dict] = []
        if block.get("type") == "section_header":
            return results

        # ── Faz B: dataset-bound block ────────────────────────────────────
        # Reads from the materialised DuckDB view (S3 parquet) — no Oracle, no
        # cache key, no bind expansion. N charts sharing one dataset alias all
        # project from the single parquet. This is the viewer-read-only path.
        binding = block.get("dataset_binding")
        if isinstance(binding, dict) and binding.get("alias"):
            from .scope.materialize import project_block_from_dataset
            with session.duck_conn() as conn:
                df = project_block_from_dataset(
                    conn, binding, filter_state,
                    concept_filters=_concept_filter_dicts,
                    column_concepts=_alias_column_concepts.get(binding.get("alias")),
                    source_ref=_alias_table_ref.get(binding.get("alias")),
                    binding_catalog=cat_snapshot,
                )
            if df is None:
                results.append({
                    "id": block["id"], "status": "empty",
                    "reason": f"dataset '{binding.get('alias')}' not materialised yet",
                })
            else:
                _apply_df_to_block(
                    block, df, engine="dataset",
                    query=f"SELECT * FROM {binding.get('alias')}",
                )
                results.append({
                    "id": block["id"], "status": "dataset",
                    "row_count": int(len(df)), "alias": binding.get("alias"),
                })
                results[-1]["_t"] = 1
            return results

        # ── Produced / scope-alias SQL block (aggregation on a Hazırlık view) ─
        # A block whose SQL reads a Hazırlık view (produced derivation or cached
        # dataset) runs in the SESSION DuckDB, not Oracle — so the catalog-table
        # concept compiler (which needs a documented SCHEMA.TABLE) can't reach it.
        # But the user may have bound a concept to a produced column in Hazırlık,
        # carried here as basket[].column_concepts. When such a block embeds the
        # {{concept_filters}} sentinel, replace it with DuckDB predicates from
        # those column_concepts + the active filters — the aggregation counterpart
        # of the projection-only dataset_binding path. This is what makes an
        # AVG/SUM KPI on a produced table interactively filterable.
        # N1/A1 — Bir bloğun SQL'i bir Hazırlık/scope VIEW'ını (üretilmiş türetme ya
        # da cached dataset) referans ediyorsa o blok SESSION DuckDB'de koşar, ASLA
        # Oracle'a gitmez. Eskiden bu yola yalnız {{concept_filters}} sentinel'i VE
        # column_concepts olan bloklar giriyordu; sentinelsiz/binding'siz bir
        # türetilmiş-view bloğu aşağıdaki Oracle concept-injection yoluna düşüp
        # `ORA-00942: table or view does not exist` alıyordu (view Oracle'da yok).
        # Artık SQL herhangi bir basket alias'ı referans ettiği anda (find_view_refs
        # — FROM/JOIN-farkında) DuckDB'de koşar. Değişken (:bind) blokları hariç
        # tutulur (onlar Phase 6.5 cache/bind yoluna gider).
        _sa_sql = block.get("query") or (block.get("data_source") or {}).get("original_sql") or ""
        _all_basket_aliases = [it.get("alias") for it in (manifest.get("basket") or [])
                               if it.get("alias")]
        _refs = (duck.find_view_refs(_sa_sql, _all_basket_aliases)
                 if (_sa_sql and not block.get("variables")) else [])
        if _refs:
            from .routes_scope import hydrate_block_datasets, load_scope_for_manifest
            from .aggregation_gate import validate_and_wrap
            from .concepts.integration import inject_where_predicate
            from .scope.materialize import _concept_predicates, inherit_source_bindings
            merged_cc: dict = {}
            for _a in _refs:
                merged_cc.update(_alias_column_concepts.get(_a) or {})
            _has_sentinel = "{{concept_filters}}" in _sa_sql
            try:
                with session.duck_conn() as conn:
                    _sc = load_scope_for_manifest(manifest)
                    if _sc is not None:
                        hydrate_block_datasets(dc, conn, _sc, _sa_sql)

                    # Predicate kaynakları (öncelik sırasıyla):
                    # 1) column_concepts — Hazırlık'ta kullanıcının view
                    #    kolonuna bağladığı concept (identity semantiği).
                    # 2) Base→türev miras — kaynak katalog tablosunun
                    #    human_verified binding'i, aynı adla view'a taşınmış
                    #    kolona uygulanır (map değerleri çevrilir). Eskiden
                    #    yalnız (1) + sentinel varken filtre uygulanıyordu;
                    #    sohbetle üretilen sentinel'siz bloklar hep blind
                    #    kalıyordu: "filtre seçtim ama grafik değişmiyor".
                    inj_params: dict = {}
                    _clauses: list[str] = []
                    _bound: set = set()
                    if _concept_filter_dicts:
                        if merged_cc:
                            _cl = _concept_predicates(
                                merged_cc, _concept_filter_dicts, inj_params)
                            if _cl:
                                _clauses += _cl
                                _bound |= set(merged_cc.values())
                        for _ri, _a in enumerate(_refs):
                            _extra_cc, _translated = inherit_source_bindings(
                                conn, _a, _alias_table_ref.get(_a),
                                _concept_filter_dicts, cat_snapshot,
                                skip_concepts=_bound,
                            )
                            if _extra_cc:
                                _clauses += _concept_predicates(
                                    _extra_cc, _translated, inj_params,
                                    prefix=f"ih{_ri}_")
                                _bound |= set(_extra_cc.values())

                    _applied = [{"concept": f["concept"]} for f in _concept_filter_dicts
                                if f.get("concept") in _bound]
                    _blind = [f["concept"] for f in _concept_filter_dicts
                              if f.get("concept") not in _bound]
                    _predicate = " AND ".join(_clauses)
                    if _has_sentinel:
                        inj_sql = _sa_sql.replace("{{concept_filters}}",
                                                  _predicate or "1 = 1")
                    elif _predicate:
                        inj_sql = inject_where_predicate(_sa_sql, _predicate)
                    else:
                        inj_sql = _sa_sql

                    gate = validate_and_wrap(inj_sql, dialect="duckdb")
                    df = (conn.execute(gate.sql, inj_params).fetchdf()
                          if inj_params else conn.execute(gate.sql).fetchdf())
                _apply_df_to_block(block, df, engine="dataset_sql", query=_sa_sql,
                                   executed_sql=inj_sql, executed_params=inj_params)
                results.append({
                    "id": block["id"], "status": "dataset_sql",
                    "row_count": int(len(df)), "aliases": _refs,
                    "applied_predicates": _applied,
                    "blind_filters": _blind,
                    "concept_injected": bool(_applied),
                })
                results[-1]["_t"] = 1
            except Exception as exc:
                msg = str(exc).strip().splitlines()[0][:240]
                results.append({"id": block["id"], "status": "error",
                                 "kind": "duckdb", "error": msg})
            return results

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
        # C2a/c: bir blok {{concept_filters}} sentinel'i taşıyor ve aktif concept
        # filtresi varsa, değişkeni/source_tables'ı olmasa bile (ör. türetilmiş bir
        # view ya da kendi CTE'si üstünde sorgu) DÜŞÜRME — aşağıda sentinel 1=1'e
        # inip blok render olur ve blind raporlanır (sessiz kaybolma değil görünür).
        sentinel_present = bool(resolved_concept_filters) and ("{{concept_filters}}" in query)
        if not query or (not variables_raw and not concept_eligible and not sentinel_present):
            return results

        # Hydrate Pydantic Block stand-in for resolver / binder / cache.
        try:
            var_models = [Variable.model_validate(v) for v in variables_raw]
            stand_in = Block.model_validate({
                "id": block["id"] if len(block["id"]) >= 3 else f"blk_{block['id']}",
                "version": int(block.get("version", 1)),
                "title": block.get("title") or "block",
                "team": "in_presentation",
                "owner": _req_sicil,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "variables": [v.model_dump() for v in var_models],
                "visualization": {"type": _standin_viz_type(block.get("type")), "config": {}},
            })
        except Exception as exc:
            results.append({"id": block["id"], "status": "error",
                             "kind": "block_schema", "error": str(exc)})
            return results

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
            return results

        # Spec §3.5 fallback — explicit binding'i OLMAYAN değişkenler için
        # TEK eşleşmeli semantic_tag auto-binding'i uygula. Aksi hâlde blok
        # "refetched" görünür ama DEFAULT değerleriyle koşar: filtre bar'daki
        # seçim sessizce YOK SAYILIR (run-manual ile üretilen bloklarda
        # variable_bindings hiç yazılmıyor — tam bu yüzden). Explicit binding
        # her zaman kazanır (setdefault); çok-eşleşme/tip-uyumsuzluğu
        # propose_auto_bindings zaten bağlamaz.
        if _dash_filter_models:
            try:
                from .dashboards.binding import propose_auto_bindings
                for name, vb in propose_auto_bindings(
                        var_models, _dash_filter_models).items():
                    bindings.setdefault(name, vb)
            except Exception:
                _req_logger.warning(
                    "apply-filters: auto-binding fallback failed (%s)",
                    block.get("id"), exc_info=True)

        # Resolve.
        try:
            resolver_cb = build_binding_resolver(bindings, filter_state)
            resolved = resolve_variables(stand_in, binding_resolver=resolver_cb)
        except ResolutionError as exc:
            results.append({"id": block["id"], "status": "error",
                             "kind": "resolution",
                             "error": "; ".join(exc.errors)})
            return results

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
            results[-1]["_t"] = 1
            return results

        # ── Phase B — shared library-block cache (lazy TTL + serve-stale) ─
        # Eligible blocks (imported_from + refresh_policy.kind=lazy_ttl) try
        # the cross-user cache BEFORE concept injection / Oracle fetch. On
        # stale hit the cache returns the old result AND enqueues a background
        # refetch via the dispatcher — so a 2-minute query incurred once per
        # 10-min window, even if 10 users open the dashboard simultaneously.
        from .cache.library_block_integration import (
            try_serve_from_library_cache as _lib_try_serve,
            maybe_write_library_cache as _lib_maybe_write,
        )
        lib_cache = _lib_cache_cfg
        lib_dispatcher = _lib_dispatcher_cfg
        if lib_cache is not None and lib_dispatcher is not None:
            try:
                _bound_for_lib = expand_binds(stand_in, resolved)
                _lib_sql = _bound_for_lib.sql
                _lib_params = _bound_for_lib.params
            except Exception:
                _lib_sql = None
                _lib_params = None

            def _refetch_for_lib():
                # Closure executed by the dispatcher in a worker thread.
                import pandas as _pd
                if _lib_sql is None:
                    return _pd.DataFrame()
                _df = dc.get_data(
                    base_prefix=None,
                    dataset=f"block::{block['id']}",
                    query=_lib_sql,
                    query_params=_lib_params,
                )
                return _df if _df is not None else _pd.DataFrame()

            cache_result = _lib_try_serve(
                block=block,
                resolved_vars=resolved,
                apply_df_to_block=_apply_df_to_block,
                cache=lib_cache,
                dispatcher=lib_dispatcher,
                fetch_fn=_refetch_for_lib,
                sql=_lib_sql or query,
            )
            if cache_result is not None:
                cache_result["id"] = block["id"]
                results.append(cache_result)
                results[-1]["_t"] = 1
                return results

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
                _req_logger.exception(
                    "concept injection failed for %s; falling back", block["id"])
                inj = None

            if inj is not None and inj.injected:
                try:
                    if inj.empty:
                        import pandas as _pd
                        df = _pd.DataFrame()
                        engine = "empty"
                    else:
                        # M6/K8 — Hazırlık-view referanslıysa DuckDB, değilse Oracle.
                        df = _run_block_query(inj.sql, inj.params, block["id"])
                        if df is None:
                            import pandas as _pd
                            df = _pd.DataFrame()
                        engine = "refetched"
                    _apply_df_to_block(block, df, engine=engine, query=query,
                                       executed_sql=inj.sql, executed_params=inj.params)
                    results.append({
                        "id": block["id"],
                        "status": "empty" if inj.empty else "refetched",
                        "row_count": int(len(df)),
                        "blind_filters": inj.blind,
                        "applied_predicates": inj.applied,
                        "concept_injected": True,
                    })
                    results[-1]["_t"] = 1
                    return results
                except Exception as exc:
                    msg = str(exc).strip().splitlines()[0][:240]
                    results.append({"id": block["id"], "status": "error",
                                     "kind": "oracle", "error": msg,
                                     "blind_filters": inj.blind})
                    return results
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
        # SQL is folded into the key so an in-place SQL edit (same id/version)
        # can't serve the previous query's stale rows.
        # C2a/c: bu satıra gelen sentinel'li blok path 2/3 enjeksiyonuna girmedi
        # (onlar `continue` eder) → sentinel aşağıda 1=1'e inecek. Aktif filtreleri
        # "blind" işaretle ki post-loop merge sonuca taşısın ve UI "filtre
        # uygulanmadı" göstersin (türetilmiş/CTE view'da base-tablo binding'i yok;
        # base→türev otomatik taşıma backlog). Sessiz no-op yerine görünür.
        if sentinel_present:
            concept_info.setdefault(block["id"], {
                "blind_filters": [f["concept"] for f in _concept_filter_dicts],
                "applied_predicates": [],
                "concept_injected": False,
            })
        ck = _cache_key(stand_in.id, stand_in.version, resolved, stand_in.query,
                        concept_digest=_concept_digest)
        with session.duck_conn() as conn:
            hit = cache.find_exact(ck)
        if hit is not None:
            # Pull cached rows back into block.data_source for renderer.
            with session.duck_conn() as conn:
                df = conn.execute(f'SELECT * FROM "{hit.view_name}"').fetchdf()
            _apply_df_to_block(block, df, engine="cache_hit", query=query)
            results.append({
                "id": block["id"], "status": "cache_hit",
                "row_count": hit.row_count, "cache_key": ck.short,
            })
            results[-1]["_t"] = 1
            return results

        with session.duck_conn() as conn:
            parent = cache.find_subset_parent(stand_in, resolved)
        subset_df = None
        if parent is not None:
            with session.duck_conn() as conn:
                subset_df = _derive_from_parent(conn, parent, stand_in, resolved)
        # _derive_from_parent returns None when it can't safely narrow the
        # parent (filter column absent from the cached result, or no mappable
        # narrowing clause) — fall through to a fresh Oracle fetch rather than
        # serving wrong/wider rows.
        if subset_df is not None:
            with session.duck_conn():
                cache.write(stand_in, resolved, subset_df)
            _apply_df_to_block(block, subset_df, engine="subset", query=query)
            results.append({
                "id": block["id"], "status": "subset",
                "row_count": int(len(subset_df)), "parent_key": parent.key.short,
            })
            results[-1]["_t"] = 1
            return results

        # Cache miss — fetch from Oracle.
        # NOT: expand_binds BURADA yeniden import EDİLMEZ — nested fonksiyon
        # içindeki geç `from ... import expand_binds`, adı fonksiyon-yereli
        # yapıp yukarıdaki (library-cache / concept-injection) kullanımları
        # UnboundLocalError'a düşürüyordu. Route kapsamındaki import geçerli.
        try:
            from .concepts.integration import strip_concept_sentinel
            bound = expand_binds(stand_in, resolved)
            # This block carries the sentinel but reached the non-concept path
            # (no source_tables or no active concept filter) — neutralize it.
            # M6/K8 — Hazırlık-view referanslıysa DuckDB, değilse Oracle.
            df = _run_block_query(
                strip_concept_sentinel(bound.sql), bound.params, block["id"])
            if df is None:
                import pandas as _pd
                df = _pd.DataFrame()
            with session.duck_conn():
                cache.write(stand_in, resolved, df)
            _apply_df_to_block(block, df, engine="refetched", query=query)
            # Phase B — warm the shared library cache too, so the next viewer
            # is a hit. Eligibility is gated inside the helper.
            if lib_cache is not None:
                _lib_maybe_write(
                    block=block, resolved_vars=resolved, df=df,
                    sql=bound.sql, cache=lib_cache,
                )
            results.append({
                "id": block["id"], "status": "refetched",
                "row_count": int(len(df)),
            })
            results[-1]["_t"] = 1
        except Exception as exc:
            msg = str(exc).strip().splitlines()[0][:240]
            results.append({"id": block["id"], "status": "error",
                             "kind": "oracle", "error": msg})

        return results

    blocks_to_run = [blk for blk in iter_all_blocks(manifest)
                     if blk.get("type") != "section_header"]
    # Sayfa-kapsamlı uygulama: frontend yalnız aktif sayfanın bloklarını
    # gönderirse sadece onlar koşar (Page özelliği + yarı yarıya hızlanma).
    _only_ids = body.get("block_ids")
    if isinstance(_only_ids, list) and _only_ids:
        _wanted = {str(x) for x in _only_ids}
        blocks_to_run = [blk for blk in blocks_to_run
                         if blk.get("id") in _wanted]

    results: list[dict] = []
    touched = 0
    if blocks_to_run:
        if len(blocks_to_run) == 1:
            outs = [_process_block(blocks_to_run[0])]
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(
                    max_workers=min(6, len(blocks_to_run))) as _pool:
                outs = list(_pool.map(_process_block, blocks_to_run))
        for _out in outs:
            for _r in _out:
                touched += _r.pop("_t", 0)
                results.append(_r)

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


def _apply_df_to_block(block: dict, df, *, engine: str, query: str,
                       executed_sql: str | None = None,
                       executed_params: dict | None = None) -> None:
    """Push a result DataFrame back into the manifest block (data_source +
    config), so the renderer picks it up unchanged.

    ``executed_sql``: concept-injected SQL fiilen çalıştırıldıysa buraya ver —
    kullanıcı Kaynakça'da hangi filtre predicate'leriyle koşulduğunu görsün.
    ``query`` yeniden-çalıştırılabilir şablon olarak kalır (çifte enjeksiyon
    olmaması için sql/original_sql'e ASLA yazılmaz)."""
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
    if executed_sql and executed_sql != query:
        block["data_source"]["executed_sql"] = executed_sql
        if executed_params:
            block["data_source"]["executed_params"] = {
                str(k): duck._jsonable(v) for k, v in executed_params.items()
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
    or ``IN (:<var>)`` to infer the column.

    Returns ``None`` when the subset can't be derived *correctly* — no
    narrowing clause could be built, or an inferred column is absent from the
    cached result (projected away / qualified name) — so the caller refetches
    from Oracle rather than serving wider or wrong rows. ``find_subset_parent``
    has already gated out aggregated / windowed / grouped / row-capped SQL,
    whose cached result a row-level WHERE could never narrow correctly.
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
    used_cols: set[str] = set()
    for var in block.variables:
        col = col_for.get(var.name)
        if col is None:
            continue
        val = resolved.get(var.name)
        if val is None:
            continue
        used_cols.add(col.upper())
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

    # find_exact already handled the equal case, so reaching here means a strict
    # subset that MUST be narrowed. If nothing mapped to a narrowing clause, or a
    # mapped column isn't present in the cached result (projected away, or the
    # regex captured a qualified/aliased name), we cannot derive correctly —
    # signal the caller to refetch from Oracle instead of serving parent rows.
    if not clauses:
        return None
    try:
        present_cols = {
            str(d[0]).upper()
            for d in conn.execute(
                f'SELECT * FROM "{parent.view_name}" LIMIT 0'
            ).description
        }
    except Exception:
        return None
    if not used_cols.issubset(present_cols):
        return None

    sql = f'SELECT * FROM "{parent.view_name}" WHERE ' + " AND ".join(clauses)
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

    # Walk blocks' source tables (concept-native flow).
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

    # Hazırlık öncesi blok yoksa basket tablolarından da concept öner —
    # böylece kullanıcı henüz blok eklemeden filtre seçeneklerini görür.
    for item in (manifest.get("basket") or []):
        ref = (item.get("table") or "").strip()
        if not ref or "." not in ref:
            continue
        schema, _, table = ref.partition(".")
        for b in cat.get_bindings(schema.strip(), table.strip()):
            cid = b.concept
            block_count[cid] = block_count.get(cid, 0) + 1
            if cid in existing or cid in seen:
                continue
            concept = eff.get(cid)
            if concept is None:
                continue
            seen.add(cid)
            suggestions.append(_filter_proposal_from_concept(concept))

    # #4 — Hazırlık'ta kullanıcının KOLONLARA bağladığı concept'ler (manifest
    # basket'teki column_concepts). Katalog doc-binding'i olmayan üretilmiş
    # kolonlar da bir concept'e tekabül edebilir → Sunum'da filtre olarak görünsün.
    for item in (manifest.get("basket") or []):
        for _col, cid in (item.get("column_concepts") or {}).items():
            if not cid:
                continue
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
    """Build a dashboard-filter proposal dict from a concept definition.

    For enum concepts without ``canonical_values`` (e.g. ``branch`` —
    ~400 rows in DIM_BRANCH, intentionally not enumerated in YAML), we
    try to lazy-fetch distinct values from the lookup binding. Returns
    a typeahead-flavoured proposal so the UI can render an autocomplete
    instead of a useless empty multi-select.
    """
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

    if not codes:
        # No canonical values authored — try fetching distinct values from
        # one of the concept's bound lookup tables (capped). Falls back to
        # an empty typeahead the user can free-text into.
        sampled = _sample_concept_distinct_values(concept, limit=50)
        return {
            "id": "f_" + concept.id,
            "semantic_tag": concept.id,
            "type": "enum_typeahead",
            "label": concept.name,
            "allowed_values": sampled,
            "default": [],
            "source": "concept_lookup" if sampled else "concept",
            "hint": (
                f"{concept.name} için canonical değer listesi yok — "
                f"{len(sampled)} örnek dimension tablosundan çekildi. "
                f"Aradığını yaz, eşleşeni seç."
            ) if sampled else (
                f"{concept.name} için tanımlı değer listesi yok. "
                f"Aradığını yaz, sistem birebir eşleşme arar."
            ),
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


def _sample_concept_distinct_values(concept, limit: int = 50) -> list[str]:
    """Pull up to ``limit`` distinct values from a concept's lookup table.

    Walks the active binding catalog for the first lookup binding tied to
    this concept and issues a tiny ``SELECT DISTINCT ... LIMIT N`` via the
    configured DataClient. Best-effort — exceptions are swallowed so the
    UI never breaks because a sample fetch failed.
    """
    catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")
    if catalog is None:
        return []
    snap = catalog.snapshot if hasattr(catalog, "snapshot") else catalog
    # Find any human_verified lookup binding for this concept.
    lookup_schema = lookup_table = lookup_col = None
    try:
        for table_doc in snap.iter_all_tables():
            for b in snap.get_bindings(table_doc.schema, table_doc.table):
                if b.concept != concept.id:
                    continue
                lk = getattr(b, "lookup", None)
                if lk and lk.get("display"):
                    lookup_schema = lk.get("schema") or table_doc.schema
                    lookup_table  = lk["table"]
                    lookup_col    = lk["display"]
                    break
            if lookup_table:
                break
    except Exception:
        log.warning("sample distinct: catalog walk failed", exc_info=True)
        return []
    if not lookup_table:
        return []

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return []
    try:
        sql = (
            f"SELECT DISTINCT {lookup_col} AS v "
            f"FROM {lookup_schema}.{lookup_table} "
            f"WHERE {lookup_col} IS NOT NULL "
            f"ORDER BY 1 FETCH FIRST {int(limit)} ROWS ONLY"
        )
        df = dc.get_data(query=sql)
        if df is None or len(df) == 0:
            return []
        return [str(v) for v in df["V"].tolist() if v is not None][:limit]
    except Exception:
        log.warning(
            "sample distinct: query failed for concept=%s lookup=%s.%s",
            concept.id, lookup_schema, lookup_table, exc_info=True,
        )
        return []


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
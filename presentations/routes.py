import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import Response, render_template, current_app, request, url_for
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.graph import GraphState, run_pipeline
from presentations import duck

# Seed manifest used the first time p_demo is opened.
_DEMO_MANIFEST = Path(__file__).parent.parent / "examples" / "sample_manifest.json"
_CATALOG_PATH = Path(__file__).parent.parent / "examples" / "sample_catalog.json"

# Ephemeral chat job table: token → job dict. Consumed by SSE stream.
_CHAT_JOBS: dict[str, dict] = {}


# ── Pages ────────────────────────────────────────────────────────────────────

@presentations_bp.route("/")
@login_required
def list_presentations():
    """Scan the user's session dir for persisted manifests, plus the demo seed."""
    items = _list_user_presentations(current_user.sicil)

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

    return render_template("presentations/list.html", presentations=items)


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
    return render_template(
        "presentations/editor.html",
        presentation_id=pid,
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
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
    """Return the catalog of available tables grouped by domain."""
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
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
    """Remove a user's presentation (manifest + DuckDB session dir)."""
    if pid == "p_demo":
        return Response(
            json.dumps({"error": "Örnek sunum silinemez."}, ensure_ascii=False),
            status=400, mimetype="application/json",
        )

    import shutil
    registry = current_app.config["SESSION_REGISTRY"]
    user_dir = Path(registry.base_dir) / current_user.sicil / pid
    if not user_dir.exists():
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    # Drop the live session if any
    key = (current_user.sicil, pid)
    if key in registry._sessions:
        registry._sessions[key].close()
        del registry._sessions[key]

    shutil.rmtree(user_dir)
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

    new_manifest["version"] = manifest.get("version", 0) + 1
    new_manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    session.set_manifest(new_manifest)

    return Response(
        json.dumps({"ok": True, "version": new_manifest["version"]}, ensure_ascii=False),
        mimetype="application/json",
    )


@presentations_bp.route("/<pid>/snapshot", methods=["POST"])
@login_required
def create_snapshot(pid: str):
    """Freeze the current manifest into a shareable snapshot."""
    session = _get_session(pid)
    manifest = session.get_manifest(fallback=_seed_manifest(pid))
    if manifest is None:
        return Response(
            json.dumps({"error": "Sunum bulunamadı."}, ensure_ascii=False),
            status=404, mimetype="application/json",
        )

    store = current_app.config["SNAPSHOT_STORE"]
    meta = store.save(manifest, owner_id=current_user.sicil)

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

    state = GraphState(
        presentation_id=pid,
        manifest=manifest,
        user_message=job["user_message"],
        selected_block_id=job["selected_block_id"],
        session=session,
    )

    app = current_app._get_current_object()

    def generate():
        with app.app_context():
            try:
                for event in run_pipeline(state):
                    payload = json.dumps(event["data"], ensure_ascii=False, default=str)
                    yield f"event: {event['event']}\ndata: {payload}\n\n"
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_session(pid: str):
    """Resolve the per-(user, presentation) session via the app's registry."""
    registry = current_app.config["SESSION_REGISTRY"]
    return registry.get_or_create(current_user.sicil, pid)


def _seed_manifest(pid: str) -> dict | None:
    """Seed for the demo presentation only; new sessions start empty."""
    if pid == "p_demo":
        try:
            return json.loads(_DEMO_MANIFEST.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
    return None


def _list_user_presentations(user_id: str) -> list[dict]:
    """Scan the SessionRegistry's filesystem for this user's persisted manifests."""
    registry = current_app.config["SESSION_REGISTRY"]
    user_dir = Path(registry.base_dir) / user_id
    if not user_dir.exists():
        return []

    items = []
    for pid_dir in user_dir.iterdir():
        if not pid_dir.is_dir():
            continue
        manifest_path = pid_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta = m.get("meta", {})
        items.append({
            "id": m.get("id", pid_dir.name),
            "title": meta.get("title") or "Adsız",
            "date": meta.get("date", ""),
            "blocks_count": len(m.get("blocks", [])),
            "updated_at": m.get("updated_at", ""),
            "is_demo": m.get("id") == "p_demo",
        })

    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items


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

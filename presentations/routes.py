import json
import secrets
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
    """Return the catalog of available tables grouped by domain.
    Includes a synthetic 'Yüklenenler' domain if the manifest has uploads."""
    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
 
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

    state = GraphState(
        presentation_id=pid,
        manifest=manifest,
        user_message=job["user_message"],
        selected_block_id=job["selected_block_id"],
        session=session,
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
"""HTTP endpoints for Phase 6.5.a block authoring.

These routes are registered on the same ``presentations_bp`` blueprint as the
rest of the editor, under the ``/blocks/...`` sub-path:

- ``GET  /blocks/``                              — library listing (HTML)
- ``GET  /blocks/new``                           — new block editor (HTML)
- ``GET  /blocks/edit/<team>/<id>``              — edit latest version (HTML)
- ``GET  /blocks/edit/<team>/<id>/<version>``    — edit specific version (HTML)
- ``GET  /blocks/api/semantic_tags``             — semantic tag allow-list (JSON)
- ``GET  /blocks/api/<team>/<id>/versions``      — list versions (JSON)
- ``GET  /blocks/api/<team>/<id>/<version>``     — load block YAML (JSON)
- ``POST /blocks/api/validate``                  — validate without saving (JSON)
- ``POST /blocks/api/save``                      — save a new block (JSON)
- ``POST /blocks/api/save_new_version``          — bump version (JSON)
- ``POST /blocks/<team>/<id>/<version>/run``     — execute block (JSON)

All endpoints require auth via the existing ``@login_required`` decorator.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from flask import Response, current_app, render_template, request
from flask_login import current_user, login_required
from pydantic import ValidationError

from presentations import presentations_bp
from presentations.blocks.schema import (
    Block,
    block_to_dict,
    load_block_from_dict,
)
from presentations.blocks.store import (
    BlockAlreadyExistsError,
    BlockNotFoundError,
    BlockStoreError,
    _normalize_team_token,
)
from presentations.sql.binder import expand_binds
from presentations.sql.validator import validate_sql
from presentations.variables.resolver import (
    ResolutionError,
    normalize_for_cache_key,
    resolve_variables,
)
from presentations.variables.semantic_tags import all_tags


log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────

def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        mimetype="application/json",
    )


def _json_default(o: Any) -> Any:
    """Handle date/datetime objects in JSON payloads."""
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"object of type {type(o).__name__} is not JSON serializable")


def _store():
    """Resolve the BLOCK_STORE from app config; raises 500-style payload."""
    store = current_app.config.get("BLOCK_STORE")
    if store is None:
        raise RuntimeError(
            "BLOCK_STORE not configured. Add a LocalBlockStore / S3BlockStore "
            "to app.config in your factory."
        )
    return store


def _normalise_block_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept either ``{block: ...}`` or a bare block dict; stamp timestamps."""
    if not isinstance(payload, dict):
        raise ValueError("block payload must be an object")
    if "block" in payload and isinstance(payload["block"], dict):
        wrapped = payload
        block = wrapped["block"]
    else:
        block = payload
        wrapped = {"block": block}

    now = datetime.now(timezone.utc).isoformat()
    block.setdefault("created_at", now)
    block["updated_at"] = now
    # Owner is always the authenticated caller — never trust a payload-supplied
    # owner (prevents authorship spoofing). Falls back to whatever was sent only
    # when there is no logged-in sicil (e.g. offline runner).
    sicil = getattr(current_user, "sicil", None)
    if sicil:
        block["owner"] = sicil
    return wrapped


def _user_team_slug() -> str:
    """The team namespace this user may author blocks under — their department
    normalized the same way the create-block UI slugifies it."""
    dept = getattr(current_user, "department", None) or ""
    return _normalize_team_token(dept)


def _block_write_denied(team: str) -> Response | None:
    """Authorization gate for block writes, mirroring uzman_save's edit-scope
    check. A block's ``team`` is its edit scope; a user may only write under the
    team matching their own department slug. Returns a 403 Response when the
    write must be refused, else None."""
    user_team = _user_team_slug()
    if not user_team or _normalize_team_token(team) != user_team:
        return _json(
            {
                "ok": False,
                "phase": "auth",
                "errors": ["Bu ekip altına blok kaydetme/güncelleme yetkin yok."],
                "warnings": [],
            },
            status=403,
        )
    return None


# ── Pages ────────────────────────────────────────────────────────────────

@presentations_bp.route("/blocks/")
@login_required
def block_library():
    """Legacy alias — the block library now lives under Kütüphane > Bloklar.
    External bookmarks to /presentations/blocks/ land on /atolye/bloklar.
    """
    from flask import redirect, url_for
    return redirect(url_for("presentations.atolye_bloklar"))


@presentations_bp.route("/blocks/new")
@login_required
def block_new():
    """Dedicated new-block authoring page (Phase 6.5).

    Renders the same React bundle as :func:`block_edit`, in ``template-edit``
    mode, but with an *empty* synthetic block and a ``template_new`` flag (no
    ``template_ref``). The user writes SQL, runs preview, then saves via the
    "Şablon olarak kaydet" modal → POST /blocks/api/save (creates v1). No
    throwaway presentation is created — this replaces the old flow where
    "Yeni Blok" spun up a 'Yeni Şablon' presentation.
    """
    new_block_id = "b_new"
    synthetic_manifest = {
        "id": "tmpl_new",
        "version": 1,
        "owner_id": getattr(current_user, "sicil", "") or "",
        "meta": {
            "title": "Yeni Blok",
            "eyebrow": "Yeni Şablon",
            "date": "",
            "author_label": getattr(current_user, "sicil", "") or "",
        },
        "template_new": True,
        "blocks": [{
            "id": "sec_new",
            "type": "section_header",
            "title": "",
            "children": [{
                "id": new_block_id,
                "type": "bar_chart",
                "title": "Yeni Blok",
                "locked": False,
                "manual_sql": True,
                "query": "",
                "variables": [],
                "config": _seed_config_for("bar_chart"),
                "data_source": {"original_sql": ""},
            }],
        }],
    }
    return render_template(
        "presentations/block_template_edit.html",
        manifest_json=json.dumps(
            synthetic_manifest, ensure_ascii=False, default=_json_default,
        ),
        # Phase 12.dark — no template_ref for new-block flow; the template
        # falls back to the "Yeni Blok" header.
        template_ref=None,
    )


@presentations_bp.route("/blocks/edit/<team>/<block_id>")
@presentations_bp.route("/blocks/edit/<team>/<block_id>/<int:version>")
@login_required
def block_edit(team: str, block_id: str, version: int | None = None):
    """Phase 6.5 template editor.

    Renders the same React bundle used by the presentation editor, but in
    ``template-edit`` mode: synthetic single-block manifest with the
    template's query/variables, and a top-of-page mini-canvas that re-renders
    after every Çalıştır via /blocks/api/preview.
    """
    store = _store()
    try:
        if version is None:
            block = store.load_latest(team, block_id)
        else:
            block = store.load(team, block_id, version)
    except BlockNotFoundError:
        return _json({"error": f"block {team}/{block_id} not found"}, status=404)

    if block.kind == "composite":
        # Container template (carousel/canvas): render the container with its
        # children so the editor shows it rather than a broken single chart.
        # Per-slide SQL editing happens once the block is inserted into a real
        # presentation, not in the template editor (graceful, read-only-ish).
        synthetic_manifest = {
            "id": f"tmpl_{team}_{block.id}_v{block.version}",
            "version": 1,
            "owner_id": getattr(current_user, "sicil", "") or "",
            "meta": {
                "title": block.title,
                "eyebrow": f"Şablon: {team}/{block.id}",
                "date": block.created_at.strftime("%Y-%m-%d"),
                "author_label": block.owner,
            },
            "template_ref": {
                "team": block.team, "id": block.id, "version": block.version,
                "owner": block.owner, "description": block.description,
                "tags": list(block.tags),
                "documentation": block.documentation.model_dump() if block.documentation else None,
                "composite": True,
            },
            "blocks": [{
                "id": f"sec_{block.id}",
                "type": "section_header",
                "title": "",
                "children": [{
                    "id": f"b_{block.id}",
                    "type": block.visualization.type,   # carousel / canvas
                    "title": block.title,
                    "locked": False,
                    "children": json.loads(json.dumps(block.children or [])),
                }],
            }],
        }
        return render_template(
            "presentations/block_template_edit.html",
            manifest_json=json.dumps(
                synthetic_manifest, ensure_ascii=False, default=_json_default,
            ),
            template_ref={"team": team, "id": block.id, "version": block.version},
        )

    # Synthesize a 1-section, 1-block manifest with manual_sql=true so the
    # editor's ManualSqlEditor takes over the Properties form. Empty config
    # seed gets filled by the first preview call.
    canvas_block_type = block.visualization.type
    if canvas_block_type not in {
        "kpi", "bar_chart", "line_chart", "area_chart",
        "pie_chart", "heatmap", "radial_bar", "data_table",
    }:
        # Phase 6.5 sample types ("bar" vs "bar_chart" etc.) — normalise to the
        # closest Phase 6 chart type the renderer knows.
        canvas_block_type = {
            "bar":   "bar_chart",
            "line":  "line_chart",
            "table": "data_table",
            "pie":   "pie_chart",
            "kpi_grid": "kpi",
        }.get(canvas_block_type, "bar_chart")

    synthetic_manifest = {
        "id": f"tmpl_{team}_{block.id}_v{block.version}",
        "version": 1,
        "owner_id": getattr(current_user, "sicil", "") or "",
        "meta": {
            "title": block.title,
            "eyebrow": f"Şablon: {team}/{block.id}",
            "date":  block.created_at.strftime("%Y-%m-%d"),
            "author_label": block.owner,
        },
        "template_ref": {
            "team": block.team,
            "id":   block.id,
            "version": block.version,
            "owner": block.owner,
            "description": block.description,
            "tags": list(block.tags),
            "documentation": block.documentation.model_dump() if block.documentation else None,
        },
        "blocks": [{
            "id": f"sec_{block.id}",
            "type": "section_header",
            "title": "",
            "children": [{
                "id": f"b_{block.id}",
                "type": canvas_block_type,
                "title": block.title,
                "locked": False,
                "manual_sql": True,
                "query": block.query,
                "variables": [
                    v.model_dump(mode="json", exclude_none=True) for v in block.variables
                ],
                "config": _seed_config_for(canvas_block_type),
                "data_source": {"original_sql": block.query},
            }],
        }],
    }

    return render_template(
        "presentations/block_template_edit.html",
        manifest_json=json.dumps(
            synthetic_manifest, ensure_ascii=False, default=_json_default,
        ),
        # Phase 12.dark — surface template provenance to the Prisma shell so
        # the breadcrumb + title render properly.
        template_ref={
            "team": team,
            "id": block.id,
            "version": block.version,
        },
    )


# ── API: metadata ────────────────────────────────────────────────────────

@presentations_bp.route("/blocks/api/semantic_tags")
@login_required
def api_semantic_tags():
    return _json({"tags": all_tags()})


@presentations_bp.route("/blocks/api/list")
@login_required
def api_list_blocks():
    """JSON BlockStore listing — consumed by the 'Bloklar' tab on /presentations/.

    Query string filters (all optional):
        ?team=<team>
        ?tag=<tag>
        ?viz_type=<kpi|bar_chart|...>
        ?q=<search>
        ?include_deprecated=1
    """
    store = _store()
    items = store.list_blocks(
        team=request.args.get("team") or None,
        tag=request.args.get("tag") or None,
        viz_type=request.args.get("viz_type") or None,
        search=request.args.get("q") or None,
        include_deprecated=(request.args.get("include_deprecated") == "1"),
    )
    return _json([s.to_dict() for s in items])


@presentations_bp.route("/blocks/api/<team>/<block_id>", methods=["DELETE"])
@login_required
def blok_delete(team: str, block_id: str):
    """Soft-delete a block (sets ``deprecated: true`` on the latest version).
    Edit-scope gated like block save — a user may only delete under their own
    team. Deprecated blocks drop out of the default listing."""
    denied = _block_write_denied(team)
    if denied is not None:
        return denied
    store = _store()
    try:
        block = store.soft_delete(team, block_id)
    except BlockNotFoundError:
        return _json({"ok": False, "error": f"Blok bulunamadı: {team}/{block_id}"}, status=404)
    except Exception as exc:
        log.exception("blok_delete failed for %s/%s", team, block_id)
        return _json({"ok": False, "error": f"Silinemedi: {exc}"}, status=500)
    return _json({"ok": True, "team": team, "id": block_id, "version": block.version})


@presentations_bp.route("/blocks/api/<team>/<block_id>/versions")
@login_required
def api_block_versions(team: str, block_id: str):
    store = _store()
    versions = store.list_versions(team, block_id)
    return _json({"team": team, "id": block_id, "versions": versions})


@presentations_bp.route("/blocks/api/<team>/<block_id>/<int:version>")
@login_required
def api_get_block(team: str, block_id: str, version: int):
    store = _store()
    try:
        block = store.load(team, block_id, version)
    except BlockNotFoundError as exc:
        return _json({"error": str(exc)}, status=404)
    return _json({"block": block_to_dict(block)["block"]})


# ── API: validate / save ─────────────────────────────────────────────────

def _validate_block_payload(payload: dict[str, Any]) -> tuple[Block | None, dict[str, Any]]:
    """Parse a block payload and run schema + SQL validation.

    Returns ``(block, result_dict)``. ``block`` is None if parsing failed.
    ``result_dict`` is the JSON body to return on validation errors.
    """
    try:
        wrapped = _normalise_block_payload(payload)
        block = load_block_from_dict(wrapped)
    except (ValidationError, ValueError) as exc:
        return None, {
            "ok": False,
            "phase": "schema",
            "errors": _flatten_pydantic_errors(exc),
            "warnings": [],
        }

    if block.kind == "composite":
        # Container blocks (carousel/canvas) carry no SQL of their own — schema
        # validation is sufficient; their children re-run when inserted.
        return block, {"ok": True, "phase": "schema", "errors": [], "warnings": []}

    sql_result = validate_sql(
        block.query,
        declared_variables=[v.name for v in block.variables],
        range_variables=[v.name for v in block.variables
                         if v.type in ("date_range", "number_range")],
    )
    return block, {
        "ok": sql_result.ok,
        "phase": "sql",
        "errors": sql_result.errors,
        "warnings": sql_result.warnings,
    }


def _flatten_pydantic_errors(exc: ValidationError | ValueError) -> list[str]:
    if isinstance(exc, ValidationError):
        out: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "validation error")
            out.append(f"{loc}: {msg}" if loc else msg)
        return out
    return [str(exc)]


@presentations_bp.route("/blocks/api/validate", methods=["POST"])
@login_required
def api_validate():
    payload = request.get_json(silent=True) or {}
    block, result = _validate_block_payload(payload)
    status = 200 if (block is not None and result["ok"]) else 400
    return _json(result, status=status)


@presentations_bp.route("/blocks/api/save", methods=["POST"])
@login_required
def api_save():
    payload = request.get_json(silent=True) or {}
    block, result = _validate_block_payload(payload)
    if block is None or not result["ok"]:
        return _json(result, status=400)

    denied = _block_write_denied(block.team)
    if denied is not None:
        return denied

    store = _store()
    try:
        saved = store.save(block)
    except BlockAlreadyExistsError as exc:
        return _json(
            {
                "ok": False,
                "phase": "store",
                "errors": [str(exc), "Hint: use /blocks/api/save_new_version to bump."],
                "warnings": result["warnings"],
            },
            status=409,
        )
    except BlockStoreError as exc:
        return _json(
            {"ok": False, "phase": "store", "errors": [str(exc)], "warnings": []},
            status=400,
        )

    return _json({
        "ok": True,
        "team": saved.team,
        "id": saved.id,
        "version": saved.version,
        "warnings": result["warnings"],
    })


@presentations_bp.route("/blocks/api/save_new_version", methods=["POST"])
@login_required
def api_save_new_version():
    payload = request.get_json(silent=True) or {}
    block, result = _validate_block_payload(payload)
    if block is None or not result["ok"]:
        return _json(result, status=400)

    denied = _block_write_denied(block.team)
    if denied is not None:
        return denied

    store = _store()
    try:
        saved = store.save_new_version(block)
    except BlockStoreError as exc:
        return _json(
            {"ok": False, "phase": "store", "errors": [str(exc)], "warnings": []},
            status=400,
        )

    return _json({
        "ok": True,
        "team": saved.team,
        "id": saved.id,
        "version": saved.version,
        "warnings": result["warnings"],
    })


# ── API: run block ───────────────────────────────────────────────────────

def _run_block(block: Block, overrides: dict[str, Any] | None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Resolve, validate, expand binds, and execute the block.

    Returns ``(dataframe, metadata)``. Caller is responsible for shaping the
    HTTP response.
    """
    sql_check = validate_sql(
        block.query,
        declared_variables=[v.name for v in block.variables],
        range_variables=[v.name for v in block.variables
                         if v.type in ("date_range", "number_range")],
    )
    if not sql_check.ok:
        raise ValueError(f"SQL validation failed: {'; '.join(sql_check.errors)}")

    resolved = resolve_variables(block, overrides or {})
    bound = expand_binds(block, resolved)

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        raise RuntimeError("DATA_CLIENT not configured")

    # Preview / library runs have no dashboard concept filters; neutralize an
    # un-injected {{concept_filters}} sentinel to a no-op so the SQL is valid.
    from presentations.concepts.integration import strip_concept_sentinel
    exec_sql = strip_concept_sentinel(bound.sql)

    started = time.perf_counter()
    df = dc.get_data(
        base_prefix=None,
        dataset=f"block::{block.team}/{block.id}/v{block.version}",
        query=exec_sql,
        query_params=bound.params,
    )
    if df is None:
        df = pd.DataFrame()
    duration_ms = int((time.perf_counter() - started) * 1000)

    meta = {
        "rewritten_sql": exec_sql,
        "bind_params": _stringify_params(bound.params),
        "resolved_variables": normalize_for_cache_key(resolved),
        "row_count": int(len(df)),
        "duration_ms": duration_ms,
        "warnings": sql_check.warnings,
    }
    return df, meta


def _stringify_params(params: dict[str, Any]) -> dict[str, Any]:
    """Convert bind params to JSON-safe primitives."""
    return {k: _json_default(v) if hasattr(v, "isoformat") else v for k, v in params.items()}


@presentations_bp.route("/blocks/<team>/<block_id>/<int:version>/run", methods=["POST"])
@login_required
def run_block(team: str, block_id: str, version: int):
    """Execute a saved block against Oracle.

    Body (optional):
        ``{"variable_overrides": {"<name>": <value>, ...}}``
    Returns:
        ``df.to_json(orient="records")`` payload (per CLAUDE.md rule).
        Headers ``X-Row-Count`` and ``X-Query-Duration-Ms`` carry execution metadata.
    """
    store = _store()
    try:
        block = store.load(team, block_id, version)
    except BlockNotFoundError as exc:
        return _json({"error": str(exc)}, status=404)

    payload = request.get_json(silent=True) or {}
    overrides = payload.get("variable_overrides") or {}
    if not isinstance(overrides, dict):
        return _json({"error": "variable_overrides must be an object"}, status=400)

    try:
        df, meta = _run_block(block, overrides)
    except ResolutionError as exc:
        return _json(
            {"ok": False, "phase": "resolution", "errors": exc.errors, "warnings": []},
            status=400,
        )
    except ValueError as exc:
        return _json(
            {"ok": False, "phase": "sql", "errors": [str(exc)], "warnings": []},
            status=400,
        )

    # DataFrame → JSON via df.to_json(orient="records") per CLAUDE.md.
    body_json = df.to_json(orient="records", date_format="iso") or "[]"
    body = json.dumps(
        {
            "ok": True,
            "rows": json.loads(body_json),
            "columns": list(df.columns),
            "meta": meta,
        },
        ensure_ascii=False,
        default=_json_default,
    )
    resp = Response(body, mimetype="application/json")
    resp.headers["X-Row-Count"] = str(meta["row_count"])
    resp.headers["X-Query-Duration-Ms"] = str(meta["duration_ms"])
    return resp


@presentations_bp.route("/blocks/api/preview", methods=["POST"])
@login_required
def api_preview():
    """Run a (possibly unsaved) block payload and return a render-ready block.

    Body: ``{"block": {...}, "variable_overrides": {...}, "render_type": "<viz>"}``.
        - ``block`` is a Phase 6.5 block payload (schema § blocks/schema.py).
        - ``render_type`` is the Phase 6 chart type the canvas will render
          with (bar_chart / line_chart / kpi / ...). When present, the
          response includes a synthetic manifest-style block dict with
          ``data_source`` + ``config`` populated so the mini-canvas in the
          template editor can re-render without re-resolving.

    Returns:
        {ok, rows, columns, meta, block?}   on success
        {ok: false, errors: [...]}          on validation / execution failure
    """
    from .nodes.execute_block_sqls import apply_data_to_config
    import datetime as _dt

    payload = request.get_json(silent=True) or {}
    block_payload = payload.get("block") if isinstance(payload, dict) else None
    if not isinstance(block_payload, dict):
        return _json({"error": "missing 'block' field"}, status=400)

    overrides = payload.get("variable_overrides") or {}
    if not isinstance(overrides, dict):
        return _json({"error": "variable_overrides must be an object"}, status=400)

    render_type = payload.get("render_type")

    block, result = _validate_block_payload({"block": block_payload})
    if block is None or not result["ok"]:
        return _json(result, status=400)

    if block.kind == "composite":
        # No single SQL to run — container preview is a no-op with a note.
        return _json({
            "ok": True, "composite": True, "rows": [], "columns": [],
            "meta": {"warnings": [
                "Bu blok bir container (carousel/canvas) — birim SQL önizlemesi yok. "
                "Sunuma eklediğinde içindeki bloklar çalışır."
            ]},
            "block": None,
        })

    try:
        df, meta = _run_block(block, overrides)
    except ResolutionError as exc:
        return _json(
            {"ok": False, "phase": "resolution", "errors": exc.errors, "warnings": result["warnings"]},
            status=400,
        )
    except ValueError as exc:
        return _json(
            {"ok": False, "phase": "sql", "errors": [str(exc)], "warnings": result["warnings"]},
            status=400,
        )

    # Tek serileştirme: rows_list zaten _jsonable'dan geçiyor; records görünümü
    # ondan türetilir (eski df.to_json → json.loads turu CPU'yu iki kez yakıyordu).
    _cols = [str(c) for c in df.columns]
    rows_list = [
        [duck._jsonable(v) for v in row]
        for row in df.itertuples(index=False, name=None)
    ]
    records = [dict(zip(_cols, r)) for r in rows_list]

    # Build a Phase-6-shaped block dict for the mini-canvas renderer.
    # The synthetic block ID keeps the Phase 6.5 slug — the canvas uses it
    # for keying, but no manifest is persisted from preview.
    canvas_block = None
    if render_type:
        canvas_block = {
            "id":          f"preview_{block.id}",
            "type":        render_type,
            "title":       block.title,
            "locked":      False,
            "manual_sql":  True,
            "query":       block.query,
            "variables":   [v.model_dump(mode="json", exclude_none=True) for v in block.variables],
            "config":      _seed_config_for(render_type),
            "data_source": {
                "sql":           meta["rewritten_sql"],
                "original_sql":  block.query,
                "rewritten":     False,
                "truncated":     False,
                "cap":           meta["row_count"],
                "reason":        "preview",
                "executed_at":   _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "row_count":     meta["row_count"],
                "columns":       list(df.columns),
                "column_types":  duck.infer_column_kinds(df),
                "preview_rows":  rows_list[:5],
                "rows":          rows_list,
                "view_name":     f"v_preview_{block.id}",
                "engine":        "preview",
            },
        }
        # Pivot rows → config (categories/series/etc) using the canvas mapper.
        apply_data_to_config(canvas_block, canvas_block["data_source"])

    body = json.dumps(
        {
            "ok": True,
            "rows": records,
            "columns": list(df.columns),
            "meta": {**meta, "warnings": [*meta.get("warnings", []), *result["warnings"]]},
            "block": canvas_block,
        },
        ensure_ascii=False,
        default=_json_default,
    )
    resp = Response(body, mimetype="application/json")
    resp.headers["X-Row-Count"] = str(meta["row_count"])
    resp.headers["X-Query-Duration-Ms"] = str(meta["duration_ms"])
    return resp


def _seed_config_for(render_type: str) -> dict:
    """Empty-shell config so apply_data_to_config has the right keys to fill."""
    seeds = {
        "kpi":        {"value": 0, "unit": "", "delta": 0, "delta_label": "", "period": ""},
        "bar_chart":  {"categories": [], "series": [{"name": "Seri 1", "values": []}]},
        "line_chart": {"x_axis": [], "series": [{"name": "Seri 1", "values": []}]},
        "area_chart": {"x_axis": [], "series": [{"name": "Seri 1", "values": []}]},
        "pie_chart":  {"labels": [], "values": []},
        "heatmap":    {"x_axis": [], "series": []},
        "radial_bar": {"value": 0, "max": 100},
        "data_table": {"columns": [], "rows": []},
    }
    return seeds.get(render_type, {})


# Import duck lazily — only the preview path needs _jsonable, and importing
# at module top creates a circular import warning in some environments.
from presentations import duck  # noqa: E402


# ════════════════════════════════════════════════════════════════════════
# Phase 6.5.d — Library MVP: list-side preview + insert flow
# ════════════════════════════════════════════════════════════════════════

@presentations_bp.route("/blocks/preview/<team>/<block_id>")
@presentations_bp.route("/blocks/preview/<team>/<block_id>/<int:version>")
@login_required
def block_library_preview(team: str, block_id: str, version: int | None = None):
    """Read-only HTML preview of a Phase 6.5 BlockStore block.

    Renders the same React bundle the editor uses, but in ``block-preview``
    mode (no toolbar, no chat, no properties panel). Synthesises a 1-section
    manifest and runs the SQL once with the block's defaults so the result
    is already in config when the page mounts. Loaded into an iframe from
    the Bloklar tab's preview modal.
    """
    store = _store()
    try:
        if version is None:
            block = store.load_latest(team, block_id)
        else:
            block = store.load(team, block_id, version)
    except BlockNotFoundError:
        return Response(f"Blok bulunamadı: {team}/{block_id}", status=404)

    # Map Phase 6.5 viz types to Phase 6 chart types the renderer knows.
    canvas_type = block.visualization.type
    if canvas_type not in {
        "kpi", "bar_chart", "line_chart", "area_chart",
        "pie_chart", "heatmap", "radial_bar", "data_table",
    }:
        canvas_type = {
            "bar":   "bar_chart",
            "line":  "line_chart",
            "table": "data_table",
            "pie":   "pie_chart",
            "kpi_grid": "kpi",
        }.get(canvas_type, "bar_chart")

    preview_block_dict = {
        "id":      f"prv_{block.id}",
        "type":    canvas_type,
        "title":   block.title,
        "locked":  False,
        "query":   block.query,
        "variables": [
            v.model_dump(mode="json", exclude_none=True) for v in block.variables
        ],
        "config":  _seed_config_for(canvas_type),
        "data_source": {"original_sql": block.query},
    }

    # Run SQL using the Phase 6.5 _run_block path — resolves variables with
    # their declared defaults, expands binds, executes through DataClient.
    # Legacy /library/<bid>/preview's _execute_preview_sql doesn't know
    # about :param resolution and would crash on any block with binds.
    try:
        from presentations.nodes.execute_block_sqls import apply_data_to_config
        df, meta = _run_block(block, overrides={})
        rows = [
            [duck._jsonable(v) for v in row]
            for row in df.itertuples(index=False, name=None)
        ]
        new_ds = {
            "sql":           meta["rewritten_sql"],
            "original_sql":  block.query,
            "rewritten":     False,
            "truncated":     False,
            "cap":           meta["row_count"],
            "reason":        "library_preview",
            "executed_at":   datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "row_count":     meta["row_count"],
            "columns":       list(df.columns),
            "preview_rows":  rows[:5],
            "rows":          rows,
            "view_name":     f"v_preview_{block.id}",
            "engine":        "library_preview",
        }
        preview_block_dict["data_source"] = new_ds
        apply_data_to_config(preview_block_dict, new_ds)
    except Exception as exc:
        log.warning("block library preview: SQL exec failed for %s/%s: %s",
                    team, block_id, exc)

    manifest = {
        "id":      f"preview_{team}_{block.id}",
        "version": 1,
        "meta": {
            "title":   block.title,
            "eyebrow": f"Şablon: {team}/{block.id}",
            "date":    block.created_at.strftime("%Y-%m-%d"),
            "author_label": block.owner,
        },
        "blocks": [{
            "id":       f"sec_{block.id}",
            "type":     "section_header",
            "title":    "",
            "locked":   False,
            "config":   {},
            "children": [preview_block_dict],
        }],
    }

    return render_template(
        "presentations/block_preview.html",
        meta={"title": block.title},
        manifest=manifest,
        manifest_json=json.dumps(manifest, ensure_ascii=False, default=_json_default),
    )


def _freshen_ids(nodes: list) -> list:
    """Assign fresh ids to every node (and descendant) in a block subtree so a
    cloned container can't collide with ids already in the target manifest."""
    import secrets as _sec
    for n in nodes:
        if isinstance(n, dict):
            n["id"] = "b_" + _sec.token_urlsafe(6)
            kids = n.get("children")
            if isinstance(kids, list):
                _freshen_ids(kids)
    return nodes


@presentations_bp.route("/<pid>/blocks/insert-from-library", methods=["POST"])
@login_required
def insert_block_from_library(pid: str):
    """Clone a BlockStore block into the given presentation.

    Body: ``{"team": "<team>", "id": "<block_id>", "version": <int|None>}``

    Adds the block under the last section of the manifest. If no section
    exists, creates one first. Returns the new in-manifest block id so the
    caller can redirect to ``/<pid>?focus_block=<bid>``.
    """
    from presentations.routes import _get_session
    from presentations.patch import apply_patches
    import secrets as _sec

    body = request.get_json(silent=True) or {}
    team = (body.get("team") or "").strip()
    bid_template = (body.get("id") or "").strip()
    version = body.get("version")
    if not team or not bid_template:
        return _json({"error": "team ve id zorunlu."}, status=400)

    store = _store()
    try:
        block = store.load_latest(team, bid_template) if version is None \
            else store.load(team, bid_template, int(version))
    except BlockNotFoundError as exc:
        return _json({"error": str(exc)}, status=404)

    session = _get_session(pid)
    manifest = session.get_manifest()
    if not manifest:
        return _json({"error": "Sunum bulunamadı."}, status=404)

    new_bid = "b_" + _sec.token_urlsafe(6)
    imported_from = {"team": block.team, "id": block.id, "version": block.version}

    if block.kind == "composite":
        # Container (carousel/canvas): re-insert the whole subtree, freshening
        # every id so it can't collide with blocks already in the manifest.
        new_block = {
            "id":       new_bid,
            "type":     block.visualization.type,   # carousel / canvas
            "title":    block.title,
            "locked":   False,
            "children": _freshen_ids(json.loads(json.dumps(block.children or []))),
            "imported_from": imported_from,
        }
    else:
        # Map viz type as in block_edit (same normalization).
        canvas_type = block.visualization.type
        if canvas_type not in {
            "kpi", "bar_chart", "line_chart", "area_chart",
            "pie_chart", "heatmap", "radial_bar", "data_table",
        }:
            canvas_type = {
                "bar":   "bar_chart",
                "line":  "line_chart",
                "table": "data_table",
                "pie":   "pie_chart",
                "kpi_grid": "kpi",
            }.get(canvas_type, "bar_chart")

        new_block = {
            "id":      new_bid,
            "type":    canvas_type,
            "title":   block.title,
            "locked":  False,
            "manual_sql": True,
            "query":   block.query,
            "variables": [
                v.model_dump(mode="json", exclude_none=True) for v in block.variables
            ],
            "config":      _seed_config_for(canvas_type),
            "data_source": {"original_sql": block.query},
            # Track origin so future polish (e.g. "imported from library" badge)
            # can be added without re-deriving from blocks.
            "imported_from": imported_from,
        }

    # Append to the last existing section. If no section, create one.
    blocks = manifest.get("blocks") or []
    if blocks and isinstance(blocks[-1], dict) and blocks[-1].get("type") == "section_header":
        target_idx = len(blocks) - 1
        children = blocks[target_idx].get("children") or []
        # Append at the end of the section's children list.
        patches = [{
            "op":    "add",
            "path":  f"/blocks/{target_idx}/children/-",
            "value": new_block,
        }]
    else:
        # No section yet — create one with the block inside.
        new_sid = "s_" + _sec.token_urlsafe(6)
        patches = [{
            "op":    "add",
            "path":  "/blocks/-",
            "value": {
                "id":     new_sid,
                "type":   "section_header",
                "title":  "Yeni Bölüm",
                "locked": False,
                "children": [new_block],
            },
        }]

    try:
        new_manifest = apply_patches(manifest, patches)
    except Exception as exc:
        return _json({"error": f"apply error: {exc}"}, status=400)

    new_manifest["version"] = manifest.get("version", 0) + 1
    session.set_manifest(new_manifest)

    return _json({
        "ok":           True,
        "version":      new_manifest["version"],
        "block_id":     new_bid,
        "imported_from": {
            "team":    block.team,
            "id":      block.id,
            "version": block.version,
        },
    })


@presentations_bp.route("/api/list")
@login_required
def api_user_presentations():
    """Minimal listing of the current user's presentations.

    Used by the "Sunuma ekle" modal on the Bloklar tab: needs just enough
    info to populate a select dropdown.
    """
    registry = current_app.config.get("SESSION_REGISTRY")
    if registry is None:
        return _json([])
    items = registry.list_user_presentations(current_user.sicil) or []
    out = [{"id": it.get("id"), "title": it.get("title") or it.get("id")}
           for it in items if it.get("id")]
    return _json(out)

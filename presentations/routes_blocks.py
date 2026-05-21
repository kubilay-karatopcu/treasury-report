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
    if not block.get("owner") and getattr(current_user, "sicil", None):
        block["owner"] = current_user.sicil
    return wrapped


# ── Pages ────────────────────────────────────────────────────────────────

@presentations_bp.route("/blocks/")
@login_required
def block_library():
    """List blocks (Phase 6.5.a stub library listing)."""
    store = _store()
    blocks = store.list_blocks(
        team=request.args.get("team") or None,
        tag=request.args.get("tag") or None,
        viz_type=request.args.get("viz_type") or None,
        search=request.args.get("q") or None,
    )
    blocks_json = json.dumps(
        [s.to_dict() for s in blocks],
        ensure_ascii=False,
        default=_json_default,
    )
    return render_template(
        "presentations/block_library.html",
        blocks=blocks,
        blocks_json=blocks_json,
        active_filters={
            "team": request.args.get("team", ""),
            "tag": request.args.get("tag", ""),
            "viz_type": request.args.get("viz_type", ""),
            "q": request.args.get("q", ""),
        },
    )


@presentations_bp.route("/blocks/new")
@login_required
def block_new():
    """Deprecated. Phase 6.5's new authoring loop happens inside the
    presentation editor: add a block, fill Properties, then "Şablon olarak
    kaydet" promotes it to the BlockStore. We redirect to the library so
    legacy bookmarks still land somewhere useful.
    """
    from flask import redirect, url_for
    return redirect(url_for("presentations.block_library"))


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
    )


# ── API: metadata ────────────────────────────────────────────────────────

@presentations_bp.route("/blocks/api/semantic_tags")
@login_required
def api_semantic_tags():
    return _json({"tags": all_tags()})


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

    started = time.perf_counter()
    df = dc.get_data(
        base_prefix=None,
        dataset=f"block::{block.team}/{block.id}/v{block.version}",
        query=bound.sql,
        query_params=bound.params,
    )
    if df is None:
        df = pd.DataFrame()
    duration_ms = int((time.perf_counter() - started) * 1000)

    meta = {
        "rewritten_sql": bound.sql,
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

    body_json = df.to_json(orient="records", date_format="iso") or "[]"
    rows_list = [
        [duck._jsonable(v) for v in row]
        for row in df.itertuples(index=False, name=None)
    ]

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
            "rows": json.loads(body_json),
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

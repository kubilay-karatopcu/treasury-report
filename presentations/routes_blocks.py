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
    """Editor page for a brand-new block (no existing version)."""
    seed_block = {
        "block": {
            "id": "",
            "version": 1,
            "title": "",
            "description": "",
            "team": "",
            "owner": getattr(current_user, "sicil", "") or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tags": [],
            "documentation": {
                "purpose": "",
                "business_context": "",
                "decision_support": "",
                "known_limitations": "",
            },
            "query": "SELECT 1 AS sample\nFROM dual\nWHERE :sample_var IS NOT NULL\n",
            "variables": [],
            "visualization": {
                "type": "bar_chart",
                "config": {},
            },
        }
    }
    return render_template(
        "presentations/block_editor.html",
        mode="new",
        block_json=json.dumps(seed_block, ensure_ascii=False, default=_json_default),
        semantic_tags_json=json.dumps(all_tags(), ensure_ascii=False),
    )


@presentations_bp.route("/blocks/edit/<team>/<block_id>")
@presentations_bp.route("/blocks/edit/<team>/<block_id>/<int:version>")
@login_required
def block_edit(team: str, block_id: str, version: int | None = None):
    """Editor page for an existing block."""
    store = _store()
    try:
        if version is None:
            block = store.load_latest(team, block_id)
        else:
            block = store.load(team, block_id, version)
    except BlockNotFoundError:
        return _json({"error": f"block {team}/{block_id} not found"}, status=404)
    return render_template(
        "presentations/block_editor.html",
        mode="edit",
        block_json=json.dumps(
            block_to_dict(block), ensure_ascii=False, default=_json_default,
        ),
        semantic_tags_json=json.dumps(all_tags(), ensure_ascii=False),
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
    """Run a (possibly unsaved) block payload from the editor preview pane.

    Body: ``{"block": {...}, "variable_overrides": {...}}``. Performs the same
    validation + execution as :func:`run_block` without persisting anything.
    """
    payload = request.get_json(silent=True) or {}
    block_payload = payload.get("block") if isinstance(payload, dict) else None
    if not isinstance(block_payload, dict):
        return _json({"error": "missing 'block' field"}, status=400)

    overrides = payload.get("variable_overrides") or {}
    if not isinstance(overrides, dict):
        return _json({"error": "variable_overrides must be an object"}, status=400)

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
    body = json.dumps(
        {
            "ok": True,
            "rows": json.loads(body_json),
            "columns": list(df.columns),
            "meta": {**meta, "warnings": [*meta.get("warnings", []), *result["warnings"]]},
        },
        ensure_ascii=False,
        default=_json_default,
    )
    resp = Response(body, mimetype="application/json")
    resp.headers["X-Row-Count"] = str(meta["row_count"])
    resp.headers["X-Query-Duration-Ms"] = str(meta["duration_ms"])
    return resp

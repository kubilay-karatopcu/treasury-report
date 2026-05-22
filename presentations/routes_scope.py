"""TEMPORARY scope-contract HTTP endpoints (Phase 8.a).

These exist only so scope contracts can be created and inspected before the
Hazırlık UI lands in **8.b** — at which point the UI owns scope authoring and
these routes are expected to be removed / folded into the Hazırlık endpoints.
Do not build new UI against them.

- ``POST /presentations/<pid>/scope``            — validate + save, returns the new version.
- ``GET  /presentations/<pid>/scope``            — latest scope contract.
- ``GET  /presentations/<pid>/scope/<version>``  — a specific version.

Auth via the existing ``@login_required``; the owning user is
``current_user.sicil`` (the S3 key is ``presentations/<sicil>/<pid>/scope_v<N>.yaml``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from flask import Response, current_app, request
from flask_login import current_user, login_required
from pydantic import ValidationError

from presentations import presentations_bp
from presentations.scope.catalog import AppCatalog
from presentations.scope.schema import (
    ScopeContract,
    load_scope_from_dict,
    scope_to_dict,
)
from presentations.scope.store import ScopeNotFoundError, ScopeStoreError
from presentations.scope.validators import validate_scope

log = logging.getLogger(__name__)


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        mimetype="application/json",
    )


def _json_default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"object of type {type(o).__name__} is not JSON serializable")


def _scope_store():
    store = current_app.config.get("SCOPE_STORE")
    if store is None:
        raise RuntimeError(
            "SCOPE_STORE not configured. Add a LocalScopeStore / S3ScopeStore "
            "to app.config in your factory."
        )
    return store


def _catalog() -> AppCatalog:
    return AppCatalog(
        table_doc_store=current_app.config.get("TABLE_DOC_STORE"),
        concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
        binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
    )


@presentations_bp.route("/<pid>/scope", methods=["POST"])
@login_required
def save_scope(pid: str):
    """Validate and persist a scope contract for ``pid`` (temporary; see module
    docstring). The URL ``pid`` and the current user are authoritative — they
    overwrite any ``presentation_id`` / ``created_by`` in the body."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "errors": ["body must be a JSON object"]}, status=400)

    try:
        scope: ScopeContract = load_scope_from_dict(body)
    except (ValidationError, ValueError) as exc:
        return _json(
            {"ok": False, "phase": "schema", "errors": _flatten(exc), "warnings": []},
            status=400,
        )

    # URL + auth win over the body.
    scope.presentation_id = pid
    scope.created_by = getattr(current_user, "sicil", None) or scope.created_by
    if scope.created_at is None:  # pragma: no cover — created_at is required by schema
        scope.created_at = datetime.now(timezone.utc)

    result = validate_scope(scope, _catalog())
    if not result.ok:
        return _json(
            {"ok": False, "phase": "validation",
             "errors": result.errors, "warnings": result.warnings},
            status=400,
        )

    try:
        version = _scope_store().save(scope)
    except ScopeStoreError as exc:
        return _json({"ok": False, "phase": "store", "errors": [str(exc)]}, status=409)

    return _json({"ok": True, "presentation_id": pid, "version": version,
                  "warnings": result.warnings})


@presentations_bp.route("/<pid>/scope", methods=["GET"])
@login_required
def get_latest_scope(pid: str):
    scope = _scope_store().load_latest(pid)
    if scope is None:
        return _json({"error": f"no scope contract for {pid}"}, status=404)
    return _json(scope_to_dict(scope))


@presentations_bp.route("/<pid>/scope/<int:version>", methods=["GET"])
@login_required
def get_scope_version(pid: str, version: int):
    try:
        scope = _scope_store().load(pid, version)
    except ScopeNotFoundError as exc:
        return _json({"error": str(exc)}, status=404)
    return _json(scope_to_dict(scope))


def _flatten(exc: ValidationError | ValueError) -> list[str]:
    if isinstance(exc, ValidationError):
        out: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            out.append(f"{loc}: {err.get('msg', 'validation error')}" if loc
                       else err.get("msg", "validation error"))
        return out
    return [str(exc)]

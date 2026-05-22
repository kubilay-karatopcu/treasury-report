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
from pathlib import Path
from typing import Any

from flask import Response, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from pydantic import ValidationError

from presentations import presentations_bp
from presentations.scope.catalog import AppCatalog
from presentations.scope.fetch import fetch_cached_tables
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


# ════════════════════════════════════════════════════════════════════════
# Phase 8.b — Hazırlık page + "Sunum'a geç" build flow
# ════════════════════════════════════════════════════════════════════════

def _registry():
    return current_app.config["SESSION_REGISTRY"]


def _concepts_payload() -> list[dict[str, Any]]:
    """Concepts the filter editor offers, with per-type op lists + canonical
    values. Read from the live registry."""
    reg = current_app.config.get("CONCEPT_REGISTRY")
    out: list[dict[str, Any]] = []
    if reg is None:
        return out
    for c in reg.all_concepts():
        t = getattr(c, "type", "scalar")
        if t == "time":
            ops = ["between", "last_n_days", "eq"]
        elif t in ("enum", "bucket"):
            ops = ["in", "not_in", "eq"]
        else:
            ops = ["eq", "between"]
        codes = c.canonical_codes() if hasattr(c, "canonical_codes") else []
        out.append({"id": c.id, "label": getattr(c, "name", c.id),
                    "type": t, "ops": ops, "canonical_values": codes})
    out.sort(key=lambda x: x["id"])
    return out


def _distributions_payload(scope: ScopeContract) -> dict[str, list[Any]]:
    """concept_id → distinct value sample, gathered across basket tables
    (Phase 6.5.b distinct_values_sample)."""
    store = current_app.config.get("TABLE_DOC_STORE")
    dist: dict[str, list[Any]] = {}
    if store is None:
        return dist
    for item in scope.basket:
        try:
            doc = store.load(item.table_ref.schema_name, item.table_ref.name)
        except Exception:
            continue
        for _col, cd in (getattr(doc, "columns", {}) or {}).items():
            concept = getattr(cd, "suggested_semantic_tag", None)
            sample = getattr(cd, "distinct_values_sample", None)
            if not concept or not sample:
                continue
            bucket = dist.setdefault(concept, [])
            for v in sample:
                if v not in bucket:
                    bucket.append(v)
    return dist


def _default_draft_scope(pid: str) -> ScopeContract:
    return ScopeContract.model_validate({
        "presentation_id": pid, "version": 1,
        "created_by": getattr(current_user, "sicil", "") or "unknown",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _load_latest_scope_or_draft(pid: str) -> ScopeContract:
    store = current_app.config.get("SCOPE_STORE")
    if store is not None:
        try:
            sc = store.load_latest(pid)
            if sc is not None:
                return sc
        except Exception:
            log.warning("hazirlik: load_latest failed for %s", pid, exc_info=True)
    return _default_draft_scope(pid)


def _catalog_json() -> dict[str, Any]:
    from presentations.routes import _catalog_path
    try:
        return json.loads(_catalog_path().read_text(encoding="utf-8"))
    except Exception:
        return {"domains": []}


@presentations_bp.route("/hazirlik/<pid>")
@login_required
def hazirlik(pid: str):
    """The Hazırlık (Stage 2 / Prepare) screen. Renders the React bundle with
    the current scope contract, the table catalog, available concepts, and
    concept value distributions embedded as JSON."""
    scope = _load_latest_scope_or_draft(pid)
    title = pid
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        m = sess.get_manifest()
        if m:
            title = m.get("meta", {}).get("title") or pid
    except Exception:
        pass

    payload = {
        "presentation_id": pid,
        "title": title,
        "scope": scope_to_dict(scope)["scope"],
        "catalog": _catalog_json(),
        "concepts": _concepts_payload(),
        "distributions": _distributions_payload(scope),
    }
    return render_template(
        "presentations/hazirlik.html",
        presentation_id=pid,
        title=title,
        hazirlik_json=json.dumps(payload, ensure_ascii=False, default=_json_default),
    )


@presentations_bp.route("/<pid>/scope/build", methods=["POST"])
@login_required
def build_scope(pid: str):
    """'Sunum'a geç': validate → fetch cached tables into DuckDB → persist scope
    (version bump) → write the manifest's scope_ref → return a redirect URL.
    Lazy tables are recorded in status but not fetched (8.d)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "errors": ["body must be a JSON object"]}, status=400)
    try:
        scope = load_scope_from_dict(body)
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "phase": "schema", "errors": _flatten(exc)}, status=400)

    scope.presentation_id = pid
    scope.created_by = getattr(current_user, "sicil", None) or scope.created_by

    catalog = _catalog()
    result = validate_scope(scope, catalog)
    if not result.ok:
        return _json({"ok": False, "phase": "validation",
                      "errors": result.errors, "warnings": result.warnings}, status=400)

    dc = current_app.config.get("DATA_CLIENT")
    session = _registry().get_or_create(current_user.sicil, pid)
    conn = session.get_duck_conn()
    lazy = [b.alias for b in scope.basket if b.routing.decision == "lazy"]

    try:
        loaded = fetch_cached_tables(dc, conn, scope, catalog=catalog)
    except Exception as exc:
        scope.status.state = "failed"
        scope.status.errors = [str(exc)]
        try:
            current_app.config["SCOPE_STORE"].save(scope)
        except Exception:
            log.warning("build_scope: persist of failed scope failed", exc_info=True)
        return _json({"ok": False, "phase": "fetch", "errors": [str(exc)]}, status=502)

    scope.status.state = "ready"
    scope.status.cached_tables = list(loaded.keys())
    scope.status.lazy_tables = lazy
    scope.status.fetched_at = datetime.now(timezone.utc)

    version = current_app.config["SCOPE_STORE"].save(scope)

    manifest = session.get_manifest() or {
        "id": pid, "version": 0, "owner_id": current_user.sicil,
        "meta": {"title": pid, "eyebrow": "", "date": "", "author_label": current_user.sicil},
        "blocks": [],
    }
    manifest["scope_ref"] = {"presentation_id": pid, "scope_version": version}
    manifest["version"] = int(manifest.get("version", 0)) + 1
    session.set_manifest(manifest)

    return _json({
        "ok": True,
        "scope_version": version,
        "cached_tables": scope.status.cached_tables,
        "lazy_tables": scope.status.lazy_tables,
        "redirect": url_for("presentations.editor", pid=pid),
    })


# ── Sunum scope banner (§6.3) ────────────────────────────────────────────────

def load_scope_for_manifest(manifest: dict | None):
    """Load the ScopeContract referenced by a manifest's scope_ref, or None."""
    ref = (manifest or {}).get("scope_ref")
    if not isinstance(ref, dict):
        return None
    store = current_app.config.get("SCOPE_STORE")
    if store is None:
        return None
    try:
        return store.load(ref.get("presentation_id"), int(ref.get("scope_version")))
    except Exception:
        return None


def scope_banner(scope) -> dict | None:
    """Compact read-only banner data for the Sunum scope chip (§6.3)."""
    if scope is None:
        return None
    pinned: list[str] = []
    for f in scope.filters.pinned:
        if f.op == "between":
            pinned.append(f"{f.from_} – {f.to}")
        elif f.values:
            pinned.append(", ".join(str(v) for v in f.values))
        elif f.value is not None:
            pinned.append(str(f.value))
    return {
        "scope_version": scope.version,
        "pinned": pinned,
        "edit_url": url_for("presentations.hazirlik", pid=scope.presentation_id),
    }

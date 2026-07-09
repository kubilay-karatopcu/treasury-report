"""HTTP endpoints for the Phase 7 concept registry (spec §8).

Registered on the shared ``presentations_bp`` blueprint under ``/concepts/...``:

- ``GET /concepts/api/list``        — all concepts in scope (JSON); optional
                                      ``?scope=global|dept:treasury|user`` filter.
- ``GET /concepts/api/<concept_id>``— one concept's full definition (JSON).

The review UI, inference triggers, and approve/reject endpoints (spec §8) land
in sub-phase 7.c. 7.a ships the read-only surface only.

The registry is read from ``current_app.config["CONCEPT_REGISTRY"]`` — a
:class:`presentations.concepts.registry.CachedConceptRegistry`. When unset
(older deployments mid-rollout) the endpoints degrade to an empty list rather
than 500.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flask import Response, current_app, render_template, request
from flask_login import current_user, login_required

from presentations import presentations_bp


log = logging.getLogger(__name__)


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        mimetype="application/json",
    )


def _registry():
    """Resolve the concept registry from app config; None if unconfigured."""
    return current_app.config.get("CONCEPT_REGISTRY")


def _concept_to_dict(concept) -> dict[str, Any]:
    """Serialize a Concept to the API JSON shape (drops None fields)."""
    return concept.model_dump(mode="json", exclude_none=True)


@presentations_bp.route("/concepts/api/list")
@login_required
def api_list_concepts():
    """List concepts. Optional ``?scope=`` exact-match filter.

    Returns ``{"concepts": [...], "count": N}``. Each concept is the full
    serialized definition (id, name, type, canonical_values, scope, ...).
    """
    registry = _registry()
    if registry is None:
        return _json({"concepts": [], "count": 0})

    scope = (request.args.get("scope") or "").strip()
    concepts = registry.all_concepts()
    if scope:
        concepts = [c for c in concepts if c.scope == scope]

    # Stable order: scope precedence (global → dept → user), then id.
    def _rank(c) -> tuple[int, str]:
        s = c.scope or ""
        r = 0 if s == "global" else 1 if s.startswith("dept:") else 2 if s == "user" else 9
        return (r, c.id)

    concepts = sorted(concepts, key=_rank)
    return _json({
        "concepts": [_concept_to_dict(c) for c in concepts],
        "count": len(concepts),
    })


@presentations_bp.route("/concepts/api/<concept_id>")
@login_required
def api_get_concept(concept_id: str):
    """Return one concept's full definition, or 404."""
    registry = _registry()
    if registry is None:
        return _json({"error": "concept registry not configured"}, status=404)
    concept = registry.get(concept_id)
    if concept is None:
        return _json({"error": f"concept {concept_id!r} not found"}, status=404)
    return _json({"concept": _concept_to_dict(concept)})


@presentations_bp.route("/concepts/api/table-columns")
@login_required
def api_table_columns():
    """Per-column concept status for a table — drives the table-docs view.

    ``?schema=&table=``. Returns, per column:
      - ``filterable`` / ``filter_role`` (from the Phase 6.5.b table doc)
      - ``suggested_concept`` (table doc's suggested_semantic_tag — a hint)
      - ``bound_concept`` (human_verified binding → usable by the compiler)
      - ``transform`` (binding transform kind)

    So the user (and, via the prompt, the LLM) can see which columns are
    concept-filterable before authoring.
    """
    schema = (request.args.get("schema") or "").strip().upper()
    table = (request.args.get("table") or "").strip().upper()
    if not schema or not table:
        return _json({"error": "schema ve table zorunlu"}, status=400)

    bound: dict[str, dict] = {}
    catalog = _catalog()
    if catalog is not None:
        cat = catalog.snapshot if hasattr(catalog, "snapshot") else catalog
        for b in cat.get_bindings(schema, table):     # human_verified only
            bound[b.column.upper()] = {"concept": b.concept, "transform": b.transform.kind}

    suggested: dict[str, dict] = {}
    store = current_app.config.get("TABLE_DOC_STORE")
    if store is not None:
        try:
            doc = store.load(schema, table)
            for name, col in (getattr(doc, "columns", {}) or {}).items():
                suggested[name.upper()] = {
                    "filterable": bool(getattr(col, "filterable", False)),
                    "filter_role": getattr(col, "filter_role", None),
                    "suggested_concept": getattr(col, "suggested_semantic_tag", None),
                }
        except Exception:
            log.debug("table doc load failed for %s.%s", schema, table)

    columns: dict[str, dict] = {}
    for col in set(bound) | set(suggested):
        info = dict(suggested.get(col, {}))
        b = bound.get(col)
        info["bound_concept"] = b["concept"] if b else None
        info["transform"] = b["transform"] if b else None
        columns[col] = info

    return _json({"schema": schema, "table": table, "columns": columns})


# ════════════════════════════════════════════════════════════════════════
# Phase 7.c — binding inference review (queue / approve / reject)
# ════════════════════════════════════════════════════════════════════════

def _catalog():
    return current_app.config.get("CONCEPT_BINDING_CATALOG")


def _catalog_root() -> Path:
    """Filesystem root of the hand-authored catalog (concepts/ + tables/)."""
    override = current_app.config.get("CONCEPT_CATALOG_ROOT")
    if override:
        return Path(override)
    import presentations
    return Path(presentations.__file__).parent / "catalog"


def _complete_fn():
    """LLM completion callable from the configured client, or None."""
    client = current_app.config.get("LLM_CLIENT")
    if client is not None and hasattr(client, "complete"):
        return client.complete
    return None


def _build_queue_for(schema: str, table: str) -> list[dict]:
    """Shared queue computation used by /inference/run and /review/api/queue."""
    from presentations.concepts.review import build_queue
    from presentations.concepts.inference.profiles import profiles_from_table_doc

    registry = _registry()
    catalog = _catalog()
    if registry is None or catalog is None:
        return []

    store = current_app.config.get("TABLE_DOC_STORE")
    if store is None:
        return []
    doc = store.load(schema, table)
    profiles = profiles_from_table_doc(doc)

    reg_snap = registry.snapshot if hasattr(registry, "snapshot") else registry
    cat_snap = catalog.snapshot if hasattr(catalog, "snapshot") else catalog
    return build_queue(
        schema, table, profiles, reg_snap, cat_snap,
        complete_fn=_complete_fn(), catalog_root=_catalog_root(),
    )


@presentations_bp.route("/concepts/inference/run", methods=["POST"])
@login_required
def api_inference_run():
    """Run inference for a table → return the review queue.

    Body: ``{"schema": "...", "table": "..."}``.
    """
    body = request.get_json(silent=True) or {}
    schema = (body.get("schema") or "").strip()
    table = (body.get("table") or "").strip()
    if not schema or not table:
        return _json({"error": "schema ve table zorunlu"}, status=400)
    try:
        queue = _build_queue_for(schema, table)
    except Exception as exc:
        log.exception("inference run failed for %s.%s", schema, table)
        return _json({"error": str(exc)}, status=500)
    return _json({"schema": schema, "table": table, "queue": queue, "count": len(queue)})


@presentations_bp.route("/concepts/review/api/queue")
@login_required
def api_review_queue():
    """Review queue for ``?schema=&table=`` (same compute as /inference/run)."""
    schema = (request.args.get("schema") or "").strip()
    table = (request.args.get("table") or "").strip()
    if not schema or not table:
        return _json({"error": "schema ve table zorunlu"}, status=400)
    try:
        queue = _build_queue_for(schema, table)
    except Exception as exc:
        log.exception("review queue failed for %s.%s", schema, table)
        return _json({"error": str(exc)}, status=500)
    return _json({"schema": schema, "table": table, "queue": queue, "count": len(queue)})


@presentations_bp.route("/concepts/review/api/approve", methods=["POST"])
@login_required
def api_review_approve():
    """Approve proposals → write human_verified bindings to the table YAML.

    Body: ``{"schema", "table", "bindings": [{column, concept, transform}, ...]}``.
    Triggers a catalog reload so the compiler sees the new bindings without a
    restart.
    """
    from presentations.concepts.review import approve_bindings

    body = request.get_json(silent=True) or {}
    schema = (body.get("schema") or "").strip()
    table = (body.get("table") or "").strip()
    bindings = body.get("bindings")
    if not schema or not table or not isinstance(bindings, list):
        return _json({"error": "schema, table ve bindings[] zorunlu"}, status=400)

    # Aktif katalog üzerinden yaz (PROD'da S3) — pod-lokal dosyaya yazmak
    # onayı compiler'a hiç ulaştırmıyordu (S3 kataloğu dosyayı okumaz,
    # restart'ta da silinir). Katalog yapılandırılmamışsa dosya-sistemi
    # davranışı korunur.
    catalog = _catalog()
    try:
        n = approve_bindings(
            _catalog_root(), schema, table, bindings,
            verified_by=getattr(current_user, "sicil", "unknown"),
            catalog=catalog if (catalog is not None and hasattr(catalog, "save_doc")) else None,
        )
    except Exception as exc:
        log.exception("approve_bindings failed for %s.%s", schema, table)
        return _json({"error": str(exc), "kind": "validation"}, status=400)

    # Force the cached catalog to pick up the freshly written YAML now.
    if catalog is not None and hasattr(catalog, "reload"):
        try:
            catalog.reload()
        except Exception:
            log.exception("catalog reload after approve failed")

    return _json({"ok": True, "written": n, "schema": schema, "table": table})


@presentations_bp.route("/concepts/review/api/reject", methods=["POST"])
@login_required
def api_review_reject():
    """Persist (column, concept) rejections so they don't resurface.

    Body: ``{"schema", "table", "items": [{column, concept}, ...]}``.
    """
    from presentations.concepts.review import reject_items

    body = request.get_json(silent=True) or {}
    schema = (body.get("schema") or "").strip()
    table = (body.get("table") or "").strip()
    items = body.get("items")
    if not schema or not table or not isinstance(items, list):
        return _json({"error": "schema, table ve items[] zorunlu"}, status=400)
    try:
        n = reject_items(_catalog_root(), schema, table, items)
    except Exception as exc:
        log.exception("reject_items failed")
        return _json({"error": str(exc)}, status=500)
    return _json({"ok": True, "rejected": n})


@presentations_bp.route("/concepts/review")
@login_required
def review_page():
    """Binding review UI (HTML). Lists candidate tables; the page fetches the
    per-table queue and posts approvals. Implemented in 7.c.4."""
    store = current_app.config.get("TABLE_DOC_STORE")
    tables = []
    if store is not None:
        try:
            tables = [{"schema": s, "table": t} for (s, t) in store.list_tables()]
        except Exception:
            log.exception("list_tables failed for review page")
    return Response(
        render_template("concepts/review.html", tables=tables),
        mimetype="text/html",
    )

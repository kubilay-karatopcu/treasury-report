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
from typing import Any

from flask import Response, current_app, request
from flask_login import login_required

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

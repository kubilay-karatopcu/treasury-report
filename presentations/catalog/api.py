"""Phase 9.a — Flask endpoints for the unified catalog read API.

Three endpoints (all per spec §2.2 / §2.4):

- ``GET /presentations/catalog``                 → list of tables + facets
- ``GET /presentations/catalog/<schema>/<table>``→ single table detail
- ``GET /presentations/catalog/graph``           → graph payload

The blueprint registration happens in ``presentations/__init__.py``; this
module attaches the routes via the shared ``presentations_bp``.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from flask import Response, current_app, request
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.catalog.edges import compute_clusters, compute_edges
from presentations.catalog.loader import (
    CatalogLoader,
    _is_user_schema,
    make_loader_from_app,
)
from presentations.catalog.models import (
    CatalogFacets,
    CatalogListResponse,
    GraphCluster,
    GraphEdge,
    GraphNode,
    GraphPayload,
    TableEntry,
)


log = logging.getLogger(__name__)


_LOADER_KEY = "_PHASE9_CATALOG_LOADER"


def _get_loader() -> CatalogLoader:
    """Cache the loader on the current Flask app config. One loader per
    process keeps the TTL cache effective."""
    loader = current_app.config.get(_LOADER_KEY)
    if loader is None:
        loader = make_loader_from_app(current_app)
        current_app.config[_LOADER_KEY] = loader
    return loader


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        mimetype="application/json",
    )


def _json_default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(by_alias=True, mode="json", exclude_none=True)
    raise TypeError(f"object of type {type(o).__name__} is not JSON serializable")


def _entry_to_list_dict(entry: TableEntry) -> dict[str, Any]:
    """Serialize a TableEntry for the list endpoint.

    Strips detail-only fields (``columns``, ``lookups``, ``related_tables``)
    so the payload stays compact at 200-table scale.
    """
    d = entry.model_dump(by_alias=True, mode="json", exclude_none=True)
    for k in ("columns", "lookups", "related_tables"):
        d.pop(k, None)
    return d


def _entry_to_detail_dict(entry: TableEntry) -> dict[str, Any]:
    return entry.model_dump(by_alias=True, mode="json", exclude_none=True)


# ── /catalog (list) ──────────────────────────────────────────────────────


@presentations_bp.route("/catalog")
@login_required
def list_catalog():
    """List catalog tables. Query params (spec §2.2):

    - ``scope`` ∈ {corporate, user, all}  (default: all)
    - ``q``     — substring match over name + description (case-insensitive)
    - ``dept``  — exact department match (comma-separated for multi-select)
    - ``concept`` — table binds the given concept (comma-separated)
    - ``refresh=1`` — bypass the 30s TTL cache for this request

    Returns ``{tables: [...], total: N, facets: {departments, concepts, sources}}``.
    The facets are computed over the *pre-filter* set so the UI can show
    available filter values even when none match.
    """
    sicil = getattr(current_user, "sicil", None)
    loader = _get_loader()
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    try:
        entries = loader.load(user_sicil=sicil, refresh=refresh)
    except Exception:
        log.exception("catalog: loader.load failed")
        return _json({"error": "catalog loader failed"}, status=500)

    scope = (request.args.get("scope") or "all").strip().lower()
    q = (request.args.get("q") or "").strip().lower()
    dept_param = (request.args.get("dept") or "").strip()
    concept_param = (request.args.get("concept") or "").strip()

    selected_depts = {p.strip() for p in dept_param.split(",") if p.strip()} if dept_param else None
    selected_concepts = {p.strip() for p in concept_param.split(",") if p.strip()} if concept_param else None

    # Facets (computed over the scope-filtered set — the search/dept/concept
    # filters do NOT shrink the facet bucket; this matches typical e-commerce
    # filter UX where the user can broaden again).
    scope_set = _apply_scope_filter(entries, scope)
    facets = _build_facets(scope_set)

    filtered = scope_set
    if q:
        filtered = [e for e in filtered if _matches_q(e, q)]
    if selected_depts is not None:
        filtered = [e for e in filtered if (e.department or "") in selected_depts]
    if selected_concepts is not None:
        filtered = [
            e for e in filtered
            if any(c in selected_concepts for c in e.concepts_bound)
        ]

    payload = CatalogListResponse(
        tables=[_entry_to_list_dict(e) for e in filtered],
        total=len(filtered),
        facets=facets,
    )
    return _json(payload.model_dump(mode="json"))


def _apply_scope_filter(entries: list[TableEntry], scope: str) -> list[TableEntry]:
    if scope == "corporate":
        return [e for e in entries if e.source == "corporate"]
    if scope == "user":
        return [e for e in entries if e.source == "user_upload"]
    return list(entries)


def _matches_q(entry: TableEntry, q: str) -> bool:
    needle = q
    haystack = " ".join(filter(None, [
        entry.name,
        entry.schema_name,
        entry.description or "",
        entry.original_filename or "",
    ])).lower()
    return needle in haystack


def _build_facets(entries: list[TableEntry]) -> CatalogFacets:
    dept_counts: Counter[str] = Counter()
    concept_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    for entry in entries:
        if entry.department:
            dept_counts[entry.department] += 1
        for c in entry.concepts_bound:
            concept_counts[c] += 1
        source_counts[entry.source] += 1
    return CatalogFacets(
        departments=dict(sorted(dept_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        concepts=dict(sorted(concept_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        sources=dict(sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
    )


# ── /catalog/<schema>/<table> (detail) ───────────────────────────────────


@presentations_bp.route("/catalog/<schema>/<table>")
@login_required
def table_detail(schema: str, table: str):
    """Return the full :class:`TableEntry` for one table, with columns,
    lookups, and related_tables populated (spec §4.4)."""
    sicil = getattr(current_user, "sicil", None)
    loader = _get_loader()

    # User-schema reads are auth-gated to the owning user. Foreign user
    # uploads are not exposed.
    if _is_user_schema(schema) and (not sicil or schema != f"__user_{sicil}__"):
        return _json({"error": "Bu yüklemeye erişiminiz yok."}, status=403)

    try:
        entry = loader.get(schema, table, user_sicil=sicil)
    except Exception:
        log.exception("catalog: detail load failed for %s.%s", schema, table)
        return _json({"error": "load failed"}, status=500)

    if entry is None:
        return _json({"error": f"{schema}.{table} bulunamadı."}, status=404)
    return _json(_entry_to_detail_dict(entry))


# ── /catalog/graph (network payload) ─────────────────────────────────────


@presentations_bp.route("/catalog/graph")
@login_required
def catalog_graph():
    """Return the §2.4 graph payload.

    Phase 9.a plumbing — the renderer comes in 9.b but the endpoint already
    has the correct shape so the UI can build against the real contract.

    Performance note: edge computation walks the full catalog; for 200
    tables this is well under 100ms even cold. If we hit 1000+ we'll need
    to cache the payload too — see spec §11.
    """
    sicil = getattr(current_user, "sicil", None)
    loader = _get_loader()
    try:
        # Load list entries (cheap) and then hydrate details for the edge
        # computation — lookup edges need the per-column data.
        list_entries = loader.load(user_sicil=sicil)
        detail_entries: list[TableEntry] = []
        for e in list_entries:
            detail = loader.get(e.schema_name, e.name, user_sicil=sicil)
            detail_entries.append(detail or e)
    except Exception:
        log.exception("catalog: graph payload build failed")
        return _json({"error": "graph build failed"}, status=500)

    edges = compute_edges(detail_entries)
    clusters_raw = compute_clusters(detail_entries)

    nodes = [
        GraphNode(
            id=e.table_id,
            label=e.name,
            department=e.department,
            source=e.source,
            concepts=e.concepts_bound,
            usage_score=0.0,  # Phase 11 supersedes; spec §2.4 keeps the field
        )
        for e in detail_entries
    ]
    graph_edges = [
        GraphEdge(
            source=ed.source,
            target=ed.target,
            kind=ed.kind,
            label=ed.label,
            concepts=ed.concepts,
            strength=ed.strength,
        )
        for ed in edges
    ]
    clusters = [GraphCluster(**c) for c in clusters_raw]

    payload = GraphPayload(nodes=nodes, edges=graph_edges, clusters=clusters)
    return _json(payload.model_dump(mode="json"))

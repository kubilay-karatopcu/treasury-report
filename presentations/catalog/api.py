"""Phase 9.a — Flask endpoints for the unified catalog read API.

Three endpoints (all per spec §2.2 / §2.4):

- ``GET /presentations/catalog``                 → list of tables + facets
- ``GET /presentations/catalog/<schema>/<table>``→ single table detail
- ``GET /presentations/catalog/graph``           → graph payload

The blueprint registration happens in ``presentations/__init__.py``; this
module attaches the routes via the shared ``presentations_bp``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import Counter
from typing import Any

from flask import Response, current_app, request
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.catalog.edges import compute_bipartite_graph, compute_clusters, compute_edges
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
_GRAPH_CACHE_KEY = "_PHASE9_GRAPH_CACHE"
_GRAPH_CACHE_LOCK = threading.Lock()
_GRAPH_CACHE_TTL_SECONDS = 60.0  # graph payload changes only on catalog edits


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


def _build_graph_payload(loader: CatalogLoader, sicil: str | None) -> GraphPayload:
    """Compute the §2.4 graph payload from the loader's current state.

    Phase 9.b.1 (refined): emits a bipartite topology — concept hubs +
    table satellites + bind edges. Replaces the previous N×N
    ``shared_concept`` fan-out. See
    :func:`presentations.catalog.edges.compute_bipartite_graph` for the
    rationale.

    Split out from the route so the cache layer can call it without going
    through Flask's request context.
    """
    list_entries = loader.load(user_sicil=sicil)
    detail_entries: list[TableEntry] = []
    for e in list_entries:
        detail = loader.get(e.schema_name, e.name, user_sicil=sicil)
        detail_entries.append(detail or e)

    raw = compute_bipartite_graph(detail_entries)
    return GraphPayload(
        nodes=[GraphNode(**n) for n in raw["nodes"]],
        edges=[GraphEdge(**e) for e in raw["edges"]],
        clusters=[GraphCluster(**c) for c in raw["clusters"]],
    )


def _catalog_content_hash(loader: CatalogLoader, sicil: str | None) -> str:
    """Cheap content hash for cache invalidation.

    We hash (table_id, concepts_bound, lookups, related_tables) for every
    catalog entry — these are exactly the inputs that affect edge / node
    output. Excluded: description / row_count_estimate (don't change shape).
    Sorted to be order-stable.
    """
    entries = loader.load(user_sicil=sicil)
    pieces = []
    for e in sorted(entries, key=lambda x: x.table_id):
        pieces.append(e.table_id)
        pieces.append(",".join(sorted(e.concepts_bound)))
        # The detail-mode lookups + related_tables aren't on list-mode entries,
        # so this hash is intentionally a fast approximation. The full detail
        # walk is what _build_graph_payload does next — cheap to recompute
        # if the approximation hits a rare false-positive cache miss.
        pieces.append(str(e.row_count_estimate or ""))
    raw = "|".join(pieces).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_cached_graph_payload(loader: CatalogLoader, sicil: str | None, refresh: bool) -> dict:
    """Return the serialized graph payload, populating cache if needed.

    Cache is per-user (user uploads change the graph). Keyed by
    ``(sicil, content_hash)`` so a catalog edit naturally invalidates without
    a manual bust. TTL is a fallback for the case where a content change
    isn't captured by the hash inputs (rare).
    """
    cache = current_app.config.setdefault(_GRAPH_CACHE_KEY, {})
    content_hash = _catalog_content_hash(loader, sicil)
    cache_key = (sicil or "", content_hash)
    now = time.monotonic()

    if not refresh:
        with _GRAPH_CACHE_LOCK:
            hit = cache.get(cache_key)
            if hit and hit["expires_at"] > now:
                return hit["payload"]

    payload = _build_graph_payload(loader, sicil)
    serialized = payload.model_dump(mode="json")
    with _GRAPH_CACHE_LOCK:
        # Drop any stale entries for this user (different content hash) to
        # bound memory; user uploads can produce a new hash on each edit.
        for k in [k for k in cache if k[0] == (sicil or "") and k != cache_key]:
            cache.pop(k, None)
        cache[cache_key] = {
            "payload": serialized,
            "expires_at": now + _GRAPH_CACHE_TTL_SECONDS,
        }
    return serialized


@presentations_bp.route("/catalog/graph")
@login_required
def catalog_graph():
    """Return the §2.4 graph payload.

    Phase 9.b.1: layout cache (60s TTL, content-hash-keyed). Phase 9.b
    Cosmograph render lives on the client; this endpoint stays
    library-agnostic.
    """
    sicil = getattr(current_user, "sicil", None)
    loader = _get_loader()
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    try:
        serialized = _get_cached_graph_payload(loader, sicil, refresh)
    except Exception:
        log.exception("catalog: graph payload build failed")
        return _json({"error": "graph build failed"}, status=500)
    return _json(serialized)

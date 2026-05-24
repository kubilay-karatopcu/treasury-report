"""Phase 9.a — Unified catalog read layer.

This package is intentionally a thin Python overlay on top of the data
directories ``catalog/concepts/`` and ``catalog/tables/`` (Phase 6.5.b /
Phase 7.b hand-authored YAML). It produces a uniform :class:`TableEntry`
shape for both corporate tables (read from ``TABLE_DOC_STORE``) and
user-uploaded tables (read from S3 ``uploads/<sicil>/<upload_id>/``), with
edge computation for the network graph and a 30-second TTL cache.

Public surface:

- ``models``  — Pydantic models (TableEntry, Edge, GraphPayload, ...).
- ``loader`` — :class:`CatalogLoader` with TTL caching.
- ``edges``  — :func:`compute_edges` (lookup / shared_concept / manual).
- ``api``    — Flask endpoints (``/catalog``, ``/catalog/<schema>/<table>``,
  ``/catalog/graph``); registered on import via ``presentations/__init__.py``.

Phase 9.b builds on this without changing the shape — the graph endpoint
already returns the §2.4 payload that Cytoscape.js will consume.
"""
from presentations.catalog.models import (
    Edge,
    GraphCluster,
    GraphEdge,
    GraphNode,
    GraphPayload,
    TableEntry,
)

__all__ = [
    "Edge",
    "GraphCluster",
    "GraphEdge",
    "GraphNode",
    "GraphPayload",
    "TableEntry",
]

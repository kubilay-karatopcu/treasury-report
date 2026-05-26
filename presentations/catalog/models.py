"""Pydantic models for the unified catalog read API (Phase 9.a, spec §2.2 / §2.4).

A single :class:`TableEntry` shape describes both corporate Oracle tables
(``source: corporate``) and user-uploaded CSV/XLSX tables (``source:
user_upload``) — consumers don't need to know which is which.

The graph payload models (:class:`GraphNode`, :class:`GraphEdge`,
:class:`GraphCluster`, :class:`GraphPayload`) describe the §2.4 shape that
Phase 9.b's Cytoscape.js renderer will consume. The endpoint exists in 9.a
so the UI can build against the real contract from day one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CatalogSource = Literal["corporate", "user_upload"]
EdgeKind = Literal["lookup", "shared_concept", "manual", "binds"]
GraphNodeType = Literal["table", "concept"]


class ColumnSummary(BaseModel):
    """Per-column summary used inside the detail card (spec §4.4)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    type: str | None = None
    description: str | None = None
    filterable: bool = False
    filter_role: str | None = None
    concept: str | None = None
    lookup: dict[str, str] | None = None
    aggregatable: bool = False


class LookupSummary(BaseModel):
    """A foreign-key style lookup declared by a column. Drives the "Lookups"
    block on the detail card and the ``lookup`` edges in the graph."""

    model_config = ConfigDict(extra="ignore")

    from_column: str
    to_table: str
    to_key: str
    to_display: str | None = None


class TableEntry(BaseModel):
    """Unified shape exposed by ``GET /catalog`` (per spec §2.2)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_name: str = Field(alias="schema")
    name: str
    source: CatalogSource
    department: str | None = None
    description: str | None = None
    concepts_bound: list[str] = Field(default_factory=list)
    concepts_unbound: list[str] = Field(default_factory=list)
    row_count_estimate: int | None = None
    row_count_basis: Literal["daily", "total"] | None = None
    partition_column: str | None = None
    doc_url: str | None = None

    # Detail-card-only fields. Excluded from the list response so the payload
    # stays compact; populated by ``GET /catalog/<schema>/<table>``.
    columns: list[ColumnSummary] | None = None
    lookups: list[LookupSummary] | None = None
    related_tables: list[str] | None = None

    # User-upload-only fields. Optional on corporate tables.
    original_filename: str | None = None
    uploaded_at: datetime | None = None
    inference_status: Literal["proposed", "confirmed", "partial"] | None = None

    @property
    def table_id(self) -> str:
        """Catalog identifier used as graph node id + lookup key."""
        return f"{self.schema_name}.{self.name}"


class Edge(BaseModel):
    """A computed edge between two tables (catalog-internal representation)."""

    model_config = ConfigDict(extra="ignore")

    source: str  # "<schema>.<table>"
    target: str  # "<schema>.<table>"
    kind: EdgeKind
    label: str | None = None  # e.g., "BRANCH_ID" for lookup; "via region" for manual
    concepts: list[str] = Field(default_factory=list)
    strength: float = 1.0


# ── Graph payload (spec §2.4) ─────────────────────────────────────────────


class GraphNode(BaseModel):
    """Two flavours under one shape — `type` discriminates.

    - ``type: "table"`` carries the existing per-table metadata
      (department, source, concepts bound by this table).
    - ``type: "concept"`` represents a concept hub; ``usage_count`` is
      the number of tables that bind this concept. ``department`` and
      ``source`` are null for concept hubs.
    """

    model_config = ConfigDict(extra="ignore")

    id: str  # "<schema>.<table>" for tables, "concept:<name>" for concepts
    type: GraphNodeType = "table"
    label: str
    department: str | None = None
    source: CatalogSource | None = None
    concepts: list[str] = Field(default_factory=list)  # table → its bindings
    usage_count: int = 0  # concept → how many tables bind it
    usage_score: float = 0.0


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    kind: EdgeKind
    label: str | None = None
    concepts: list[str] = Field(default_factory=list)
    strength: float = 1.0


class GraphCluster(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    node_ids: list[str]


class GraphPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    clusters: list[GraphCluster] = Field(default_factory=list)


# ── List-endpoint response ────────────────────────────────────────────────


class CatalogFacets(BaseModel):
    """Facet counts driving the left-rail filter UI (spec §2.2)."""

    model_config = ConfigDict(extra="ignore")

    departments: dict[str, int] = Field(default_factory=dict)
    concepts: dict[str, int] = Field(default_factory=dict)
    sources: dict[str, int] = Field(default_factory=dict)


class CatalogListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tables: list[dict[str, Any]]  # serialized TableEntry dicts (list-mode)
    total: int
    facets: CatalogFacets

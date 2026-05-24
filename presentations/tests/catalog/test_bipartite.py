"""Phase 9.b.1 bipartite topology: concept hubs + bind edges.

The unit tests in test_edges.py still cover the legacy compute_edges
(used by tests and the older table-only payload). This file exercises
the new compute_bipartite_graph which the /catalog/graph endpoint now
serves.
"""
from __future__ import annotations

from presentations.catalog.edges import (
    CONCEPT_NODE_PREFIX,
    compute_bipartite_graph,
    concept_node_id,
)
from presentations.catalog.models import LookupSummary, TableEntry


def _entry(schema, name, *, concepts=None, lookups=None, department="treasury", source="corporate"):
    return TableEntry(
        schema=schema,
        name=name,
        source=source,
        department=department,
        concepts_bound=list(concepts or []),
        columns=[],
        lookups=lookups or [],
    )


def test_emits_concept_hub_per_distinct_concept():
    entries = [
        _entry("EDW", "T1", concepts=["branch", "currency"]),
        _entry("EDW", "T2", concepts=["branch", "as_of_time"]),
    ]
    g = compute_bipartite_graph(entries)
    concept_ids = sorted(n["id"] for n in g["nodes"] if n["type"] == "concept")
    assert concept_ids == [
        concept_node_id("as_of_time"),
        concept_node_id("branch"),
        concept_node_id("currency"),
    ]


def test_concept_usage_count_matches_binding_count():
    entries = [
        _entry("EDW", "T1", concepts=["branch", "currency"]),
        _entry("EDW", "T2", concepts=["branch"]),
        _entry("EDW", "T3", concepts=["branch", "as_of_time"]),
    ]
    g = compute_bipartite_graph(entries)
    concept_nodes = {n["label"]: n for n in g["nodes"] if n["type"] == "concept"}
    assert concept_nodes["branch"]["usage_count"] == 3
    assert concept_nodes["currency"]["usage_count"] == 1
    assert concept_nodes["as_of_time"]["usage_count"] == 1


def test_bind_edges_table_to_concept_one_per_binding():
    entries = [
        _entry("EDW", "T1", concepts=["branch", "currency"]),
        _entry("EDW", "T2", concepts=["branch"]),
    ]
    g = compute_bipartite_graph(entries)
    bind_edges = [e for e in g["edges"] if e["kind"] == "binds"]
    pairs = sorted([(e["source"], e["target"]) for e in bind_edges])
    assert pairs == [
        ("EDW.T1", concept_node_id("branch")),
        ("EDW.T1", concept_node_id("currency")),
        ("EDW.T2", concept_node_id("branch")),
    ]


def test_lookup_edges_kept_table_to_table():
    """Bipartite swap doesn't touch FK lookup edges — those stay
    table→table because they represent join semantics, not concept
    binding."""
    entries = [
        _entry("EDW", "T1", concepts=["branch"], lookups=[
            LookupSummary(from_column="BRANCH_CODE", to_table="T2", to_key="BRANCH_CODE"),
        ]),
        _entry("EDW", "T2", concepts=["branch"]),
    ]
    g = compute_bipartite_graph(entries)
    lookup_edges = [e for e in g["edges"] if e["kind"] == "lookup"]
    assert len(lookup_edges) == 1
    assert lookup_edges[0]["source"] == "EDW.T1"
    assert lookup_edges[0]["target"] == "EDW.T2"


def test_no_shared_concept_edges_emitted():
    """Information that previously came from shared_concept edges is now
    structural (two tables bound to the same hub)."""
    entries = [
        _entry("EDW", "T1", concepts=["x"]),
        _entry("EDW", "T2", concepts=["x"]),
        _entry("EDW", "T3", concepts=["x"]),
    ]
    g = compute_bipartite_graph(entries)
    assert not any(e["kind"] == "shared_concept" for e in g["edges"])


def test_concept_hub_has_no_department_or_source():
    """Concept hubs are catalog-wide, not departmental."""
    entries = [_entry("EDW", "T1", concepts=["branch"])]
    g = compute_bipartite_graph(entries)
    concept = next(n for n in g["nodes"] if n["type"] == "concept")
    assert concept["department"] is None
    assert concept["source"] is None


def test_clusters_cover_table_nodes_only():
    """Concept hubs are orthogonal to department clusters — they
    shouldn't pollute the cluster membership lists."""
    entries = [
        _entry("EDW", "T1", concepts=["branch"], department="treasury"),
        _entry("ODS_RISK", "T2", concepts=["branch"], department="risk"),
    ]
    g = compute_bipartite_graph(entries)
    for cluster in g["clusters"]:
        for node_id in cluster["node_ids"]:
            assert not node_id.startswith(CONCEPT_NODE_PREFIX)


def test_deterministic_concept_order():
    """Same input → identical concept node order (helps cache hashing)."""
    entries = [
        _entry("EDW", "T1", concepts=["zebra", "apple", "mango"]),
        _entry("EDW", "T2", concepts=["apple"]),
    ]
    g1 = compute_bipartite_graph(entries)
    g2 = compute_bipartite_graph(entries)
    ids1 = [n["id"] for n in g1["nodes"]]
    ids2 = [n["id"] for n in g2["nodes"]]
    assert ids1 == ids2

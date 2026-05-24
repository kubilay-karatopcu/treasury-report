"""Phase 9.a — Edge computation: lookup, shared_concept, manual, collapse."""
from __future__ import annotations

from presentations.catalog.edges import compute_clusters, compute_edges
from presentations.catalog.models import LookupSummary, TableEntry


def _entry(schema, name, *, concepts=None, lookups=None, related=None, source="corporate", department="treasury"):
    return TableEntry(
        schema=schema,
        name=name,
        source=source,
        department=department,
        concepts_bound=list(concepts or []),
        columns=[],
        lookups=lookups or [],
        related_tables=related,
    )


# ── Lookup edges ──────────────────────────────────────────────────────────


def test_declared_lookup_produces_directed_edge():
    entries = [
        _entry("EDW", "DEPOSITS_DAILY", concepts=["branch"], lookups=[
            LookupSummary(from_column="BRANCH_CODE", to_table="DIM_BRANCH",
                          to_key="BRANCH_CODE", to_display="BRANCH_NAME"),
        ]),
        _entry("EDW", "DIM_BRANCH", concepts=["branch"]),
    ]
    edges = compute_edges(entries)
    lookups = [e for e in edges if e.kind == "lookup"]
    assert len(lookups) == 1
    assert lookups[0].source == "EDW.DEPOSITS_DAILY"
    assert lookups[0].target == "EDW.DIM_BRANCH"
    assert lookups[0].label == "BRANCH_CODE"
    assert lookups[0].strength == 1.0


def test_lookup_to_missing_table_is_dropped():
    entries = [
        _entry("EDW", "DEPOSITS_DAILY", lookups=[
            LookupSummary(from_column="BRANCH_CODE", to_table="MISSING_TABLE",
                          to_key="BRANCH_CODE"),
        ]),
    ]
    edges = compute_edges(entries)
    assert all(e.kind != "lookup" for e in edges)


def test_lookup_prefers_same_schema_when_ambiguous():
    """When two tables share a name across schemas, the lookup resolver
    picks the source's own schema."""
    entries = [
        _entry("EDW", "FACT_A", lookups=[
            LookupSummary(from_column="K", to_table="DIM_BRANCH", to_key="K"),
        ]),
        _entry("EDW", "DIM_BRANCH"),
        _entry("ODS", "DIM_BRANCH"),
    ]
    edges = compute_edges(entries)
    lookups = [e for e in edges if e.kind == "lookup"]
    assert lookups and lookups[0].target == "EDW.DIM_BRANCH"


# ── Shared-concept edges ──────────────────────────────────────────────────


def test_shared_concept_edges_dedupe_undirected():
    entries = [
        _entry("EDW", "T1", concepts=["currency"]),
        _entry("EDW", "T2", concepts=["currency"]),
    ]
    edges = compute_edges(entries)
    shared = [e for e in edges if e.kind == "shared_concept"]
    assert len(shared) == 1
    pair = sorted([shared[0].source, shared[0].target])
    assert pair == ["EDW.T1", "EDW.T2"]
    assert shared[0].concepts == ["currency"]


def test_multiple_shared_concepts_collapsed_with_higher_strength():
    entries = [
        _entry("EDW", "T1", concepts=["currency", "as_of_time"]),
        _entry("EDW", "T2", concepts=["currency", "as_of_time"]),
    ]
    edges = compute_edges(entries)
    shared = [e for e in edges if e.kind == "shared_concept"]
    assert len(shared) == 1
    assert set(shared[0].concepts) == {"currency", "as_of_time"}
    # 2 shared concepts → strength 0.6 by current formula
    assert shared[0].strength > 0.4


def test_concept_with_single_member_yields_no_edge():
    entries = [
        _entry("EDW", "T1", concepts=["foo"]),
        _entry("EDW", "T2", concepts=["bar"]),
    ]
    edges = compute_edges(entries)
    assert all(e.kind != "shared_concept" for e in edges)


# ── Manual edges ──────────────────────────────────────────────────────────


def test_manual_edge_from_related_tables_array():
    entries = [
        _entry("EDW", "T1", related=["T2"]),
        _entry("EDW", "T2"),
    ]
    edges = compute_edges(entries)
    manual = [e for e in edges if e.kind == "manual"]
    assert len(manual) == 1
    assert manual[0].source == "EDW.T1"
    assert manual[0].target == "EDW.T2"


def test_manual_edge_to_missing_table_is_dropped():
    entries = [_entry("EDW", "T1", related=["NOTHERE"])]
    edges = compute_edges(entries)
    assert all(e.kind != "manual" for e in edges)


# ── Collapse: strongest kind wins ─────────────────────────────────────────


def test_lookup_beats_shared_concept_on_same_pair():
    entries = [
        _entry("EDW", "T1", concepts=["k"], lookups=[
            LookupSummary(from_column="K", to_table="T2", to_key="K"),
        ]),
        _entry("EDW", "T2", concepts=["k"]),
    ]
    edges = compute_edges(entries)
    assert len(edges) == 1
    assert edges[0].kind == "lookup"


def test_shared_concept_beats_manual_on_same_pair():
    entries = [
        _entry("EDW", "T1", concepts=["k"], related=["T2"]),
        _entry("EDW", "T2", concepts=["k"]),
    ]
    edges = compute_edges(entries)
    assert len(edges) == 1
    assert edges[0].kind == "shared_concept"


# ── Determinism ───────────────────────────────────────────────────────────


def test_edges_sorted_for_determinism():
    entries = [
        _entry("EDW", "T2", concepts=["c1"]),
        _entry("EDW", "T1", concepts=["c1"]),
        _entry("EDW", "T3", concepts=["c1"]),
    ]
    e1 = compute_edges(entries)
    e2 = compute_edges(entries)
    assert [(e.source, e.target, e.kind) for e in e1] == \
           [(e.source, e.target, e.kind) for e in e2]


# ── Clusters ──────────────────────────────────────────────────────────────


def test_clusters_grouped_by_department():
    entries = [
        _entry("EDW", "T1", department="treasury"),
        _entry("EDW", "T2", department="treasury"),
        _entry("ODS_RISK", "T3", department="risk"),
    ]
    clusters = compute_clusters(entries)
    by_label = {c["label"]: c for c in clusters}
    assert "Treasury" in by_label
    assert "Risk" in by_label
    assert sorted(by_label["Treasury"]["node_ids"]) == ["EDW.T1", "EDW.T2"]


def test_uploads_form_their_own_cluster():
    entries = [
        _entry("EDW", "T1", department="treasury"),
        _entry("__user_A__", "u_x", source="user_upload", department=None),
    ]
    clusters = compute_clusters(entries)
    labels = {c["label"] for c in clusters}
    assert "Yüklemelerim" in labels
    upload_cluster = next(c for c in clusters if c["label"] == "Yüklemelerim")
    assert "__user_A__.u_x" in upload_cluster["node_ids"]

"""Routing decision tests (spec §3.1, §10.a)."""
from __future__ import annotations

import pytest

from presentations.scope.catalog import DictCatalog
from presentations.scope.routing import (
    DEFAULT_HARD_CEILING_BYTES,
    RoutingCeilingError,
    RoutingDecision,
    apply_user_override,
    decide_routing,
    estimate_post_scope_size,
)
from presentations.scope.schema import PinnedFilter, Projection, TableRef


def _catalog(daily_rows=12000, bytes_each=40, ncols=5):
    cols = {"D": {"type": "DATE", "avg_bytes": bytes_each, "concept": "as_of_time"}}
    for i in range(ncols - 1):
        cols[f"C{i}"] = {"avg_bytes": bytes_each}
    return DictCatalog.from_excerpt({
        "tables": {"BIG": {
            "schema": "ODS_TREASURY", "partition_column": "D",
            "estimated_daily_rows": daily_rows, "columns": cols,
        }},
        "concepts": {"as_of_time": {"type": "date"}},
    })


def _proj(ncols=5):
    return Projection(columns=["D", *[f"C{i}" for i in range(ncols - 1)]])


def _tref():
    return TableRef.model_validate({"schema": "ODS_TREASURY", "name": "BIG"})


def _between(frm, to):
    return PinnedFilter.model_validate(
        {"id": "pf_d", "concept": "as_of_time", "op": "between", "from": frm, "to": to}
    )


# ── §10.a acceptance scenarios ──────────────────────────────────────────────

def test_30_day_projection_is_cached():
    # 30 days * 12000 rows/day * 200 bytes/row = 72 MB <= 500 MB threshold.
    cat = _catalog()
    d = decide_routing(_tref(), _proj(), [_between("2025-10-01", "2025-10-30")], catalog=cat)
    assert d.decision == "cached"
    assert d.estimated_bytes == 72_000_000
    assert d.decided_by == "system"
    assert d.estimate_source == "catalog"   # D2 — gerçek katalog tahmini


def test_5_year_projection_is_lazy():
    # ~1826 days * 12000 * 200 ≈ 4.38 GB > 500 MB threshold.
    cat = _catalog()
    d = decide_routing(_tref(), _proj(), [_between("2021-01-01", "2025-12-31")], catalog=cat)
    assert d.decision == "lazy"
    assert d.estimated_bytes > 500_000_000


def test_override_to_cached_valid():
    dec = RoutingDecision(decision="lazy", estimated_bytes=3_000_000_000)
    out = apply_user_override(dec, "cached")
    assert out.decision == "cached"
    assert out.decided_by == "user"


def test_override_to_cached_exceeds_ceiling():
    dec = RoutingDecision(decision="lazy", estimated_bytes=15_000_000_000)
    with pytest.raises(RoutingCeilingError):
        apply_user_override(dec, "cached")


def test_override_to_lazy_always_ok():
    dec = RoutingDecision(decision="cached", estimated_bytes=15_000_000_000)
    out = apply_user_override(dec, "lazy")
    assert out.decision == "lazy"
    assert out.decided_by == "user"


# ── Estimator detail ────────────────────────────────────────────────────────

def test_estimate_uses_total_rows_for_dimension():
    cat = DictCatalog.from_excerpt({
        "tables": {"DIM": {
            "schema": "S", "partition_column": None,
            "estimated_daily_rows": 0, "estimated_total_rows": 1200,
            "columns": {"A": {"avg_bytes": 10}, "B": {"avg_bytes": 10}},
        }},
        "concepts": {},
    })
    tm = cat.table_meta("S", "DIM")
    size = estimate_post_scope_size(tm, Projection(columns=["A", "B"]), [])
    assert size == 1200 * 20


def test_estimate_falls_back_to_horizon_without_pinned_range():
    cat = _catalog()
    tm = cat.table_meta("ODS_TREASURY", "BIG")
    size = estimate_post_scope_size(tm, _proj(), [], default_horizon_days=10)
    assert size == 10 * 12000 * 200


def test_unknown_table_routes_lazy():
    # A table the catalog can't size must NOT be assumed tiny and eagerly cached
    # (that path materialised possibly-huge un-onboarded tables → OOM, #27).
    # Unknown size is treated as over-threshold → lazy (fetched on demand, capped).
    cat = _catalog()
    tref = TableRef.model_validate({"schema": "ODS_TREASURY", "name": "NOT_IN_CATALOG"})
    d = decide_routing(tref, _proj(), [], catalog=cat)
    assert d.decision == "lazy"
    assert d.estimated_bytes > d.threshold_bytes
    # D2 — kataloglanmamış → boyut sentinel'i; UI sahte "500 MB" değil "?" göstersin.
    assert d.estimate_source == "unknown"


def test_default_hard_ceiling_is_10gb():
    assert DEFAULT_HARD_CEILING_BYTES == 10_000_000_000


def test_documented_but_unsized_table_routes_lazy():
    # Doc exists but carries NO row stats (neither daily nor total). The size
    # formula would yield 0 bytes → cached → uncapped full pull at build (the
    # "Sunum'a geç çok yavaş" bug: resolve-plan mains flipped to cached this
    # way). Unknown size must route lazy, same as a missing table.
    cat = DictCatalog.from_excerpt({
        "tables": {"NOSTATS": {
            "schema": "S", "partition_column": None,
            "columns": {"A": {"avg_bytes": 10}},
        }},
        "concepts": {},
    })
    tref = TableRef.model_validate({"schema": "S", "name": "NOSTATS"})
    d = decide_routing(tref, Projection(columns=["A"]), [], catalog=cat)
    assert d.decision == "lazy"
    assert d.estimated_bytes > d.threshold_bytes
    assert d.estimate_source == "unknown"   # D2 — satır istatistiği yok → sentinel


def test_override_preserves_unknown_estimate_source():
    # D2 — kullanıcı lazy→cached zorlasa bile altta yatan "unknown" sentinel'i
    # korunur (UI override'lı node'da da "?" gösterir, sahte sayı değil).
    dec = RoutingDecision(decision="lazy", estimated_bytes=600_000_000,
                          estimate_source="unknown")
    out = apply_user_override(dec, "cached")
    assert out.estimate_source == "unknown" and out.decided_by == "user"


def test_app_catalog_reads_estimated_total_rows_from_doc():
    # AppCatalog used to hardcode estimated_total_rows=None — a doc'd table
    # without daily rows was unsizable. The new TableDoc.estimated_total_rows
    # must flow through so dimension tables size (and route cached) correctly.
    from types import SimpleNamespace
    from presentations.scope.catalog import AppCatalog

    doc = SimpleNamespace(
        schema_name="S", table="DIM", partition_column=None,
        estimated_daily_rows=None, estimated_total_rows=5000,
        columns={"A": SimpleNamespace(type="NUMBER", suggested_semantic_tag=None)},
    )

    class _Store:
        def load(self, schema, name):
            return doc

    cat = AppCatalog(_Store(), concept_registry=None, binding_catalog=None)
    tm = cat.table_meta("S", "DIM")
    assert tm.estimated_total_rows == 5000
    d = decide_routing(
        TableRef.model_validate({"schema": "S", "name": "DIM"}),
        Projection(columns=["A"]), [], catalog=cat,
    )
    assert d.decision == "cached"

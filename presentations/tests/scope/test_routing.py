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


def test_unknown_table_estimates_zero_and_caches():
    cat = _catalog()
    tref = TableRef.model_validate({"schema": "ODS_TREASURY", "name": "NOT_IN_CATALOG"})
    d = decide_routing(tref, _proj(), [], catalog=cat)
    assert d.estimated_bytes == 0
    assert d.decision == "cached"


def test_default_hard_ceiling_is_10gb():
    assert DEFAULT_HARD_CEILING_BYTES == 10_000_000_000

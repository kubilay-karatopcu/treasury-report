"""F1 — Filtreleme tab backend foundation.

Raw filters gained numeric comparison ops (gt/gte/lt/lte) and the scope fetch +
size estimate now resolve the relative-date grammar (today, today - 30d, …) so
a relative range pushes down to Oracle and shrinks the routing estimate exactly
like an absolute one — re-resolved to the run date each time (dynamic).
"""
from __future__ import annotations

from datetime import date, timedelta

from presentations.scope.catalog import DictCatalog
from presentations.scope.fetch import _raw_predicates
from presentations.scope.routing import _as_date, _days_in_range, decide_routing
from presentations.scope.schema import (
    PinnedFilter,
    Projection,
    TableRef,
    load_scope_from_dict,
)


def _scope_with_raw():
    return load_scope_from_dict({"scope": {
        "presentation_id": "p", "version": 1, "created_by": "A16438",
        "created_at": "2025-01-01T00:00:00Z",
        "basket": [{
            "alias": "tbl",
            "table_ref": {"schema": "EDW", "name": "T"},
            "projection": {"columns": ["AMT", "SEG", "D"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000},
        }],
        "filters": {"pinned": [], "interactive": [], "raw": [
            {"id": "rf_amt", "alias": "tbl", "column": "AMT", "op": "gt", "value": 100},
            {"id": "rf_seg", "alias": "tbl", "column": "SEG", "op": "in",
             "values": ["RETAIL", "SME"]},
            {"id": "rf_d", "alias": "tbl", "column": "D", "op": "between",
             "from": "today - 30d", "to": "today"},
        ]},
        "joins": [],
    }})


def test_raw_predicates_numeric_and_in_ops():
    scope = _scope_with_raw()
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    joined = " | ".join(clauses)
    assert "AMT > :" in joined
    assert "SEG IN (" in joined
    # gt value bound as-is (not concatenated)
    assert 100 in binds.values()


def test_raw_between_resolves_relative_dates():
    scope = _scope_with_raw()
    _, binds = _raw_predicates(scope, scope.basket[0])
    date_binds = {v for v in binds.values() if isinstance(v, date)}
    assert (date.today() - timedelta(days=30)) in date_binds
    assert date.today() in date_binds


def test_raw_between_numeric():
    # Filtreleme numeric "aralık" (between/AND) → raw between with numeric binds.
    scope = load_scope_from_dict({"scope": {
        "presentation_id": "p", "version": 1, "created_by": "A16438",
        "created_at": "2025-01-01T00:00:00Z",
        "basket": [{
            "alias": "tbl", "table_ref": {"schema": "EDW", "name": "T"},
            "projection": {"columns": ["AMT"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000},
        }],
        "filters": {"pinned": [], "interactive": [], "raw": [
            {"id": "rf_amt", "alias": "tbl", "column": "AMT", "op": "between", "from": 10, "to": 20},
        ]},
        "joins": [],
    }})
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert any("AMT BETWEEN" in c for c in clauses)
    assert 10 in binds.values() and 20 in binds.values()


def test_as_date_grammar():
    assert _as_date("today") == date.today()
    assert _as_date("today - 7d") == date.today() - timedelta(days=7)
    assert _as_date("2026-01-15") == date(2026, 1, 15)
    assert _as_date("not a date") is None
    assert _as_date(42) is None


def _partition_catalog():
    cols = {"D": {"type": "DATE", "avg_bytes": 40, "concept": "as_of_time"}}
    for i in range(4):
        cols[f"C{i}"] = {"avg_bytes": 40}
    return DictCatalog.from_excerpt({
        "tables": {"BIG": {"schema": "ODS_TREASURY", "partition_column": "D",
                           "estimated_daily_rows": 12000, "columns": cols}},
        "concepts": {"as_of_time": {"type": "date"}},
    })


def test_relative_range_shrinks_estimate():
    # today-9d .. today = 10 days inclusive → 10 * 12000 * 200 bytes = 24 MB,
    # well under the 500 MB threshold → cached. Proves the relative range feeds
    # the partition estimate (it used to read as None → horizon fallback → lazy).
    cat = _partition_catalog()
    proj = Projection(columns=["D", "C0", "C1", "C2", "C3"])
    tref = TableRef.model_validate({"schema": "ODS_TREASURY", "name": "BIG"})
    pf = PinnedFilter.model_validate(
        {"id": "pf_d", "concept": "as_of_time", "op": "between",
         "from": "today - 9d", "to": "today"})
    d = decide_routing(tref, proj, [pf], catalog=cat)
    assert d.decision == "cached"
    assert d.estimated_bytes == 10 * 12000 * 200
    assert _days_in_range(pf) == 10

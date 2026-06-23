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

import duckdb
import pandas as pd
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


# ── #4/#5 — empty IN + relative/ISO date resolution in eq / gt-lte ──────────

def _scope_with(raw):
    return load_scope_from_dict({"scope": {
        "presentation_id": "p", "version": 1, "created_by": "A16438",
        "created_at": "2025-01-01T00:00:00Z",
        "basket": [{
            "alias": "tbl",
            "table_ref": {"schema": "EDW", "name": "T"},
            "projection": {"columns": ["SEG", "D"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system",
                        "estimated_bytes": 1000},
        }],
        "filters": {"pinned": [], "interactive": [], "raw": raw},
        "joins": [],
    }})


def _run_duck(clauses, binds):
    # Mirror compile_filter_sql's derived-source path: Oracle ':' binds become
    # DuckDB '$' binds. Materialise a tiny table and count surviving rows.
    con = duckdb.connect()
    df = pd.DataFrame({
        "SEG": ["RETAIL", "SME", "CORP"],
        "D": [date.today(), date.today() - timedelta(days=10),
              date.today() - timedelta(days=40)],
    })
    con.register("t_raw", df)
    con.execute('CREATE TABLE t AS SELECT * FROM t_raw')
    where = (" WHERE " + " AND ".join(c.replace(":", "$") for c in clauses)) if clauses else ""
    sql = "SELECT * FROM t" + where
    return len(con.execute(sql, binds).fetchdf() if binds else con.execute(sql).fetchdf())


def test_empty_in_matches_zero_rows():
    # Boş `in` → predicate DÜŞMEMELİ (yoksa WHERE yok → TÜM satırlar). 1 = 0 yay.
    scope = _scope_with([{"id": "rf_e", "alias": "tbl", "column": "SEG",
                          "op": "in", "values": []}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert clauses == ["1 = 0"]
    assert binds == {}
    assert _run_duck(clauses, binds) == 0


def test_empty_not_in_is_noop_all_rows():
    # Boş `not_in` mantıken match-all → güvenli no-op (1 = 0 YAYMA).
    scope = _scope_with([{"id": "rf_n", "alias": "tbl", "column": "SEG",
                          "op": "not_in", "values": []}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert clauses == []
    assert _run_duck(clauses, binds) == 3


def test_gte_resolves_relative_date():
    # 'today - 7d' → date'e çözülmeli; ham string DuckDB DATE'inde ConversionException.
    scope = _scope_with([{"id": "rf_g", "alias": "tbl", "column": "D",
                          "op": "gte", "value": "today - 7d"}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert (date.today() - timedelta(days=7)) in binds.values()
    assert all(isinstance(v, date) for v in binds.values())
    assert _run_duck(clauses, binds) == 1   # yalnız today satırı


def test_eq_resolves_relative_date():
    scope = _scope_with([{"id": "rf_q", "alias": "tbl", "column": "D",
                          "op": "eq", "value": "today"}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert date.today() in binds.values()
    assert _run_duck(clauses, binds) == 1


def test_nonempty_in_still_positional_binds():
    # Pozisyonel placeholder + bind — değer SQL'e KONKATLANMAZ (injection).
    scope = _scope_with([{"id": "rf_i", "alias": "tbl", "column": "SEG",
                          "op": "in", "values": ["RETAIL", "SME"]}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert clauses == ["SEG IN (:tbl_rf0_0, :tbl_rf0_1)"]
    assert set(binds.values()) == {"RETAIL", "SME"}
    assert "RETAIL" not in clauses[0]   # değer string'e gömülmemiş
    assert _run_duck(clauses, binds) == 2


def test_eq_plain_value_unchanged():
    # Düz string eq: _as_date None döner → ham değere düşer (davranış değişmez).
    scope = _scope_with([{"id": "rf_s", "alias": "tbl", "column": "SEG",
                          "op": "eq", "value": "RETAIL"}])
    clauses, binds = _raw_predicates(scope, scope.basket[0])
    assert binds == {"tbl_rf0": "RETAIL"}
    assert _run_duck(clauses, binds) == 1


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


def test_parse_relative_optional_unit():
    # "today - 7" (no unit) defaults to days, same as "today - 7d".
    from presentations.variables.resolver import parse_date_expr
    assert parse_date_expr("today - 7") == date.today() - timedelta(days=7)
    assert parse_date_expr("today - 7d") == date.today() - timedelta(days=7)
    assert parse_date_expr("today") == date.today()


def _pinned_scope(frm, to):
    return load_scope_from_dict({"scope": {
        "presentation_id": "p", "version": 1, "created_by": "A16438",
        "created_at": "2025-01-01T00:00:00Z",
        "basket": [{
            "alias": "tbl", "table_ref": {"schema": "EDW", "name": "T"},
            "projection": {"columns": ["D"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1},
        }],
        "filters": {"pinned": [
            {"id": "pf_d", "concept": "as_of_time", "op": "between",
             "from": frm, "to": to, "applies_to": []},
        ], "interactive": [], "raw": []},
        "joins": [],
    }})


def test_pinned_between_relative_not_inverted():
    from presentations.scope.validators import rule_pinned_consistency
    cat = DictCatalog.from_excerpt({"tables": {}, "concepts": {}})
    errors, _ = rule_pinned_consistency(_pinned_scope("today - 7d", "today"), cat)
    assert errors == []
    # Bare-unit form must also pass.
    errors2, _ = rule_pinned_consistency(_pinned_scope("today - 7", "today"), cat)
    assert errors2 == []


def test_pinned_between_relative_inverted_flagged():
    from presentations.scope.validators import rule_pinned_consistency
    cat = DictCatalog.from_excerpt({"tables": {}, "concepts": {}})
    errors, _ = rule_pinned_consistency(_pinned_scope("today", "today - 7d"), cat)
    assert len(errors) == 1 and "from <= to" in errors[0]

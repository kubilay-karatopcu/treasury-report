"""Cached-table fetch tests (spec §3.2, §8.b)."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from presentations.scope.catalog import DictCatalog
from presentations.scope.fetch import compose_cached_sql, fetch_cached_tables
from presentations.scope.schema import load_scope_from_dict


class StubDC:
    def __init__(self, df):
        self.df = df
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.calls.append({"dataset": dataset, "query": query, "params": query_params})
        return self.df.copy()


def _catalog():
    return DictCatalog.from_excerpt({
        "tables": {"TRD_BRANCH_POSITION": {
            "schema": "ODS_TREASURY", "partition_column": "AS_OF_DATE",
            "estimated_daily_rows": 12000,
            "columns": {
                "AS_OF_DATE": {"type": "DATE", "avg_bytes": 8, "concept": "as_of_time"},
                "BRANCH_ID": {"type": "VARCHAR2(8)", "avg_bytes": 8, "concept": "branch"},
                "CCY": {"type": "CHAR(3)", "avg_bytes": 3, "concept": "currency"},
            },
        }},
        "concepts": {"as_of_time": {"type": "date"}, "currency": {"type": "enum"}},
    })


def _scope(basket, pinned=None):
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": basket, "filters": {"pinned": pinned or [], "interactive": []},
    })


# ── compose_cached_sql ──────────────────────────────────────────────────────

def test_compose_projection():
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["AS_OF_DATE", "CCY"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    sql, binds = compose_cached_sql(scope, scope.basket[0])
    assert sql == "SELECT AS_OF_DATE, CCY FROM ODS_TREASURY.TRD_BRANCH_POSITION"
    assert binds == {}


def test_compose_include_all():
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    sql, _ = compose_cached_sql(scope, scope.basket[0])
    assert sql == "SELECT * FROM ODS_TREASURY.TRD_BRANCH_POSITION"


def test_compose_partition_pushdown():
    scope = _scope(
        [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["AS_OF_DATE", "CCY"], "include_all": False},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        }],
        pinned=[{"id": "pf_q4", "concept": "as_of_time", "op": "between",
                 "from": "2025-10-01", "to": "2025-12-31", "applies_to": ["positions"]}],
    )
    sql, binds = compose_cached_sql(scope, scope.basket[0], _catalog())
    assert sql == ("SELECT AS_OF_DATE, CCY FROM ODS_TREASURY.TRD_BRANCH_POSITION "
                   "WHERE AS_OF_DATE BETWEEN :positions_from AND :positions_to")
    assert binds == {"positions_from": __import__("datetime").date(2025, 10, 1),
                     "positions_to": __import__("datetime").date(2025, 12, 31)}


def test_compose_no_pushdown_without_catalog():
    scope = _scope(
        [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["AS_OF_DATE"], "include_all": False},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        }],
        pinned=[{"id": "pf_q4", "concept": "as_of_time", "op": "between",
                 "from": "2025-10-01", "to": "2025-12-31", "applies_to": ["positions"]}],
    )
    sql, binds = compose_cached_sql(scope, scope.basket[0], catalog=None)
    assert "WHERE" not in sql and binds == {}


# ── fetch_cached_tables ──────────────────────────────────────────────────────

def test_fetch_materialises_cached_views_and_skips_lazy():
    df = pd.DataFrame({"AS_OF_DATE": ["2025-10-01"], "CCY": ["TRY"]})
    dc = StubDC(df)
    conn = duckdb.connect(":memory:")
    scope = _scope([
        {"table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
         "alias": "positions",
         "projection": {"columns": ["AS_OF_DATE", "CCY"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
        {"table_ref": {"schema": "ODS_TREASURY", "name": "FX_BIG"},
         "alias": "fx_big",
         "projection": {"columns": ["X"], "include_all": False},
         "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}},
    ])
    loaded = fetch_cached_tables(dc, conn, scope, catalog=_catalog())

    assert set(loaded.keys()) == {"positions"}     # lazy alias skipped
    assert loaded["positions"]["rows"] == 1
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1
    # Only one Oracle call (the cached table).
    assert len(dc.calls) == 1
    assert "FROM ODS_TREASURY.TRD_BRANCH_POSITION" in dc.calls[0]["query"]


def test_compose_with_raw_filters():
    scope = load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["CCY", "NET_POSITION"], "include_all": False},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        }],
        "filters": {"raw": [
            {"id": "rf_ccy", "alias": "positions", "column": "CCY", "op": "in", "values": ["TRY", "USD"]},
            {"id": "rf_net", "alias": "positions", "column": "NET_POSITION", "op": "eq", "value": 0},
        ]},
    })
    sql, binds = compose_cached_sql(scope, scope.basket[0])
    assert "CCY IN (:positions_rf0_0, :positions_rf0_1)" in sql
    assert "NET_POSITION = :positions_rf1" in sql
    assert binds["positions_rf0_0"] == "TRY" and binds["positions_rf0_1"] == "USD"
    assert binds["positions_rf1"] == 0


def test_fetch_empty_result_does_not_crash():
    dc = StubDC(pd.DataFrame())
    conn = duckdb.connect(":memory:")
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    loaded = fetch_cached_tables(dc, conn, scope, catalog=_catalog())
    assert loaded["positions"]["rows"] == 0

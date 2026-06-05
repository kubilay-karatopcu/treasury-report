"""Faz R1 — filter-derivation node: schema + Oracle SQL compiler.

A "filter" derivation is a cached sub-node derived from a main (lazy) table by
applying embedded filters. The compiler regenerates the Oracle source query
``SELECT <proj> FROM <source table> WHERE <filters>`` on demand, so relative
dates stay dynamic. These tests lock the SQL shape and schema rules.
"""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from presentations.scope.fetch import compile_filter_sql, fetch_cached_tables
from presentations.scope.schema import (
    BasketItem, Derivation, Filters, PinnedFilter, Projection, RawFilter,
    Routing, ScopeContract, TableRef,
)


class _StubDC:
    def __init__(self, df):
        self.df = df
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.calls.append({"dataset": dataset, "query": query, "params": query_params})
        return self.df.copy()


def _scope(basket):
    return ScopeContract(
        presentation_id="p_r1", version=1, created_by="A1",
        created_at="2026-06-04T00:00:00Z", basket=basket,
    )


def _main(alias="dep_main", cols=("BRANCH", "BALANCE", "AS_OF")):
    return BasketItem(
        alias=alias,
        table_ref=TableRef(schema="EDW", name="DEPOSITS"),
        projection=Projection(columns=list(cols), include_all=False),
        routing=Routing(decision="lazy", estimated_bytes=9_000_000_000),
    )


def _filter_node(alias, source_alias, *, pinned=None, raw=None, projection=None):
    return BasketItem(
        alias=alias,
        derivation=Derivation(kind="filter", source_alias=source_alias,
                              filters=Filters(pinned=pinned or [], raw=raw or [])),
        projection=projection or Projection(),
        routing=Routing(decision="cached", estimated_bytes=5000),
    )


# ── schema ───────────────────────────────────────────────────────────────

def test_filter_derivation_requires_source_and_filters():
    with pytest.raises(Exception):
        Derivation(kind="filter", source_alias="x")            # no filters
    with pytest.raises(Exception):
        Derivation(kind="filter", filters=Filters(raw=[]))     # no source/filters


def test_filter_derivation_forbids_aggregate_fields():
    with pytest.raises(Exception):
        Derivation(kind="filter", source_alias="x", group_by=["A"],
                   filters=Filters(raw=[RawFilter(id="rf_1", alias="x", column="A", op="eq", value=1)]))


# ── compiler: raw column filter ──────────────────────────────────────────

def test_compile_raw_eq_filter():
    raw = [RawFilter(id="rf_1", alias="dep_7d", column="STATUS", op="eq", value="ACTIVE")]
    scope = _scope([_main(), _filter_node("dep_7d", "dep_main", raw=raw)])
    item = scope.basket_item("dep_7d")
    sql, binds = compile_filter_sql(scope, item)
    assert sql.startswith("SELECT BRANCH, BALANCE, AS_OF FROM EDW.DEPOSITS WHERE")
    assert "STATUS = :" in sql
    assert list(binds.values()) == ["ACTIVE"]


def test_compile_raw_in_filter_uses_positional_binds():
    raw = [RawFilter(id="rf_1", alias="dep_x", column="SEGMENT", op="in",
                     values=["RETAIL", "SME"])]
    scope = _scope([_main(), _filter_node("dep_x", "dep_main", raw=raw)])
    sql, binds = compile_filter_sql(scope, scope.basket_item("dep_x"))
    assert "SEGMENT IN (:" in sql
    assert sorted(binds.values()) == ["RETAIL", "SME"]   # never concatenated


def test_compile_relative_between_resolves_to_dates():
    # A relative date range must resolve to concrete dates at compile time
    # (dynamic dataset — re-shifts each materialise).
    raw = [RawFilter(id="rf_1", alias="dep_7d", column="AS_OF", op="between",
                     **{"from": "today - 7d", "to": "today"})]
    scope = _scope([_main(), _filter_node("dep_7d", "dep_main", raw=raw)])
    sql, binds = compile_filter_sql(scope, scope.basket_item("dep_7d"))
    assert "AS_OF BETWEEN :" in sql
    import datetime as _dt
    assert all(isinstance(v, _dt.date) for v in binds.values())


def test_compile_inherits_source_projection_when_node_has_none():
    raw = [RawFilter(id="rf_1", alias="dep_y", column="STATUS", op="eq", value="X")]
    scope = _scope([_main(cols=("A", "B")), _filter_node("dep_y", "dep_main", raw=raw)])
    sql, _ = compile_filter_sql(scope, scope.basket_item("dep_y"))
    assert "SELECT A, B FROM EDW.DEPOSITS" in sql


def test_compile_raises_when_source_missing():
    raw = [RawFilter(id="rf_1", alias="orphan", column="A", op="eq", value=1)]
    scope = _scope([_filter_node("orphan", "nonexistent", raw=raw)])
    with pytest.raises(ValueError):
        compile_filter_sql(scope, scope.basket_item("orphan"))


# ── materialize: filter-node fetched from Oracle → DuckDB view ───────────

def test_fetch_materialises_filter_node_from_oracle():
    raw = [RawFilter(id="rf_1", alias="dep_active", column="STATUS", op="eq", value="ACTIVE")]
    # main = lazy (not materialised); filter-node = cached (materialised here).
    scope = _scope([_main(), _filter_node("dep_active", "dep_main", raw=raw)])
    dc = _StubDC(pd.DataFrame({"BRANCH": [1, 2], "BALANCE": [10, 20], "AS_OF": ["x", "y"]}))
    conn = duckdb.connect(":memory:")

    loaded = fetch_cached_tables(dc, conn, scope)

    # Oracle was queried with the filtered SELECT (not the bare table).
    assert len(dc.calls) == 1
    assert "WHERE" in dc.calls[0]["query"] and "STATUS = :" in dc.calls[0]["query"]
    # The filter-node is registered as a DuckDB view + reported as derived.
    assert loaded["dep_active"]["derived_from"] == "dep_main"
    assert loaded["dep_active"]["rows"] == 2
    n = conn.execute('SELECT COUNT(*) FROM "dep_active"').fetchone()[0]
    assert n == 2
    # The lazy main table was NOT fetched (no Oracle pull for it).
    assert "dep_main" not in loaded


def test_chain_filter_on_filter_runs_second_in_duckdb():
    # Faz A — main(lazy) → filter f1 (Oracle) → filter f2 (DuckDB over f1).
    f1 = _filter_node("dep_a", "dep_main",
                      raw=[RawFilter(id="rf_1", alias="dep_a", column="BRANCH", op="gte", value=2)])
    f2 = _filter_node("dep_b", "dep_a",   # source is the DERIVED node f1
                      raw=[RawFilter(id="rf_2", alias="dep_b", column="BRANCH", op="eq", value=2)])
    scope = _scope([_main(), f1, f2])
    dc = _StubDC(pd.DataFrame({"BRANCH": [1, 2, 3], "BALANCE": [10, 20, 30], "AS_OF": ["x", "y", "z"]}))
    conn = duckdb.connect(":memory:")

    loaded = fetch_cached_tables(dc, conn, scope)

    assert "dep_a" in loaded and "dep_b" in loaded
    # Only ONE Oracle pull (f1); f2 filtered f1 entirely in DuckDB.
    assert len(dc.calls) == 1
    # f2 = (stub 3 rows) → f1 is just the same stub rows (StubDC ignores WHERE) →
    # f2 keeps BRANCH=2 → 1 row.
    assert conn.execute('SELECT COUNT(*) FROM "dep_b"').fetchone()[0] == 1


# ── materialize: filter-node → parquet (Sunum dataset path, F4) ──────────

def test_materialize_computes_filter_node_from_oracle():
    from presentations.scope.materialize import _compute_dataset_df
    raw = [RawFilter(id="rf_1", alias="dep_7d", column="AS_OF", op="between",
                     **{"from": "today - 7d", "to": "today"})]
    scope = _scope([_main(), _filter_node("dep_7d", "dep_main", raw=raw)])
    dc = _StubDC(pd.DataFrame({"BRANCH": [1], "BALANCE": [5], "AS_OF": ["z"]}))
    df, sql = _compute_dataset_df(
        dc, scope, scope.basket_item("dep_7d"),
        catalog=None, concept_registry=None, binding_catalog=None,
        visited=frozenset(),
    )
    # Filter-node materialises by re-querying the SOURCE Oracle table (filtered).
    assert "FROM EDW.DEPOSITS WHERE" in sql and "AS_OF BETWEEN :" in sql
    assert len(df) == 1
    assert dc.calls and dc.calls[0]["query"] == sql

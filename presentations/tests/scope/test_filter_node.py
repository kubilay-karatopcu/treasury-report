"""Faz R1 — filter-derivation node: schema + Oracle SQL compiler.

A "filter" derivation is a cached sub-node derived from a main (lazy) table by
applying embedded filters. The compiler regenerates the Oracle source query
``SELECT <proj> FROM <source table> WHERE <filters>`` on demand, so relative
dates stay dynamic. These tests lock the SQL shape and schema rules.
"""
from __future__ import annotations

import pytest

from presentations.scope.fetch import compile_filter_sql
from presentations.scope.schema import (
    BasketItem, Derivation, Filters, PinnedFilter, Projection, RawFilter,
    Routing, ScopeContract, TableRef,
)


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

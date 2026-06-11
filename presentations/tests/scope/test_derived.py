"""Tests for derived (aggregate) tables — schema, SQL compile, fetch, validator."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest
from pydantic import ValidationError

from presentations.scope.fetch import compile_aggregate_sql, fetch_cached_tables
from presentations.scope.schema import BasketItem, Measure, load_scope_from_dict
from presentations.scope.validators import rule_derived_tables


class StubDC:
    def __init__(self, df): self.df = df
    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        return self.df.copy()


def _scope(extra_basket=None):
    basket = [{
        "table_ref": {"schema": "EDW", "name": "DEPOSITS"}, "alias": "deposits",
        "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }]
    basket += (extra_basket or [])
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z", "basket": basket,
    })


_AGG_ITEM = {
    "derivation": {"kind": "aggregate", "source_alias": "deposits",
                   "group_by": ["BRANCH_CODE"],
                   "measures": [{"column": "BALANCE_TRY", "fn": "sum", "as": "TOTAL"}]},
    "alias": "deposits_by_branch",
    "projection": {"columns": ["BRANCH_CODE", "TOTAL"], "include_all": False},
    "routing": {"decision": "cached", "estimated_bytes": 0},
}


# ── Schema ───────────────────────────────────────────────────────────────────

class TestSchema:
    def test_measure_alias(self):
        m = Measure.model_validate({"column": "BALANCE_TRY", "fn": "sum", "as": "TOTAL"})
        assert m.as_ == "TOTAL"
        assert m.model_dump(by_alias=True)["as"] == "TOTAL"

    def test_derived_item_parses(self):
        scope = _scope([_AGG_ITEM])
        d = scope.basket_item("deposits_by_branch")
        assert d.table_ref is None
        assert d.derivation.source_alias == "deposits"
        assert d.derivation.measures[0].fn == "sum"

    def test_both_sources_rejected(self):
        with pytest.raises(ValidationError):
            BasketItem.model_validate({
                "table_ref": {"schema": "S", "name": "T"},
                "derivation": {"kind": "aggregate", "source_alias": "deposits"},
                "alias": "x", "projection": {"columns": []},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            })

    def test_neither_source_rejected(self):
        with pytest.raises(ValidationError):
            BasketItem.model_validate({
                "alias": "x", "projection": {"columns": []},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            })

    def test_helpers_split_raw_and_derived(self):
        scope = _scope([_AGG_ITEM])
        assert [b.alias for b in scope.raw_items()] == ["deposits"]
        assert [b.alias for b in scope.derived_items()] == ["deposits_by_branch"]


# ── SQL compile ──────────────────────────────────────────────────────────────

def test_compile_aggregate_sql():
    # Kimlikler quote'lu: keyword kolon adları çalışır + DuckDB exact-case eşleşir.
    scope = _scope([_AGG_ITEM])
    sql = compile_aggregate_sql(scope.basket_item("deposits_by_branch"))
    assert sql == ('SELECT "BRANCH_CODE", SUM("BALANCE_TRY") AS "TOTAL" '
                   'FROM "deposits" GROUP BY "BRANCH_CODE"')


def test_compile_count_distinct():
    item = BasketItem.model_validate({
        "derivation": {"kind": "aggregate", "source_alias": "deposits", "group_by": ["SEGMENT"],
                       "measures": [{"column": "BRANCH_CODE", "fn": "count_distinct", "as": "N_BRANCH"}]},
        "alias": "agg2", "projection": {"columns": ["SEGMENT", "N_BRANCH"]},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    })
    assert compile_aggregate_sql(item) == \
        ('SELECT "SEGMENT", COUNT(DISTINCT "BRANCH_CODE") AS "N_BRANCH" '
         'FROM "deposits" GROUP BY "SEGMENT"')


# ── Fetch (raw source → DuckDB, then derived aggregate on DuckDB) ────────────

def test_fetch_materialises_derived_aggregate():
    dc = StubDC(pd.DataFrame({"BRANCH_CODE": ["A", "A", "B"], "BALANCE_TRY": [10, 20, 5]}))
    conn = duckdb.connect(":memory:")
    scope = _scope([_AGG_ITEM])
    loaded = fetch_cached_tables(dc, conn, scope, catalog=None)
    assert "deposits" in loaded and "deposits_by_branch" in loaded
    assert loaded["deposits_by_branch"]["derived_from"] == "deposits"
    res = conn.execute("SELECT BRANCH_CODE, TOTAL FROM deposits_by_branch ORDER BY BRANCH_CODE").fetchall()
    assert res == [("A", 30), ("B", 5)]


# ── Validator ────────────────────────────────────────────────────────────────

class TestDerivedValidator:
    def test_source_alias_missing(self):
        item = dict(_AGG_ITEM)
        item["derivation"] = {**_AGG_ITEM["derivation"], "source_alias": "ghost"}
        scope = load_scope_from_dict({
            "presentation_id": "p", "version": 1, "created_by": "A", "created_at": "2026-06-15T10:00:00Z",
            "basket": [item],
        })
        errors, _ = rule_derived_tables(scope)
        assert any("source alias 'ghost' not in basket" in e for e in errors)

    def test_valid_passes(self):
        errors, _ = rule_derived_tables(_scope([_AGG_ITEM]))
        assert errors == []


# ── Route helper: columns_by_alias with a table-less derivation ──────────────

class TestColumnsByAlias:
    def test_derivation_item_skipped(self):
        """``_columns_by_alias`` must not deref ``table_ref`` on derivation
        basket items (which carry ``table_ref=None``). Regression for the
        AttributeError raised when building the Hazırlık payload for a scope
        containing an aggregate table."""
        from flask import Flask

        from presentations.routes_scope import _columns_by_alias

        scope = _scope([_AGG_ITEM])
        app = Flask(__name__)  # no TABLE_DOC_STORE → _columns_for returns []
        with app.app_context():
            cols = _columns_by_alias(scope)

        assert "deposits_by_branch" not in cols  # derivation skipped, not crashed
        assert cols == {"deposits": []}

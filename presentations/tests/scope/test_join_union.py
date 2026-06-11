"""Hazırlık ER join + union derivation kinds — schema, compilers, DuckDB e2e.

Mirrors test_calculated.py. The join/union nodes are produced by dragging
between node columns (join) or node titles (union) in the Hazırlık canvas;
both materialise in DuckDB over the already-registered source views.
"""
from __future__ import annotations

import duckdb
import pytest

import pandas as pd

from presentations.scope.schema import (
    BasketItem,
    CalculatedJoinKey,
    Derivation,
    Projection,
    Routing,
    load_scope_from_dict,
)
from presentations.scope.fetch import (
    compile_join_sql, compile_union_sql, fetch_cached_tables,
)


# ── Schema validators ───────────────────────────────────────────────────────

class TestJoinUnionSchema:

    def test_join_valid(self):
        d = Derivation(
            kind="join",
            source_aliases=["deposits", "competitor"],
            join_keys=[CalculatedJoinKey(
                left_alias="deposits", left_column="BRANCH_CODE",
                right_alias="competitor", right_column="BRANCH_CODE")],
            join_type="left",
        )
        assert d.kind == "join" and d.join_type == "left"

    def test_join_requires_exactly_two_sources(self):
        with pytest.raises(ValueError, match="2 source_aliases"):
            Derivation(kind="join", source_aliases=["solo"], join_keys=[])

    def test_join_requires_a_key(self):
        with pytest.raises(ValueError, match="join_key gerekli"):
            Derivation(kind="join", source_aliases=["aaa", "bbb"], join_keys=[])

    def test_join_rejects_other_kinds_fields(self):
        with pytest.raises(ValueError, match="yalnız source_aliases"):
            Derivation(
                kind="join", source_aliases=["aaa", "bbb"],
                join_keys=[CalculatedJoinKey(
                    left_alias="aaa", left_column="X",
                    right_alias="bbb", right_column="Y")],
                group_by=["Z"],
            )

    def test_join_key_alias_must_be_a_source(self):
        with pytest.raises(ValueError, match="source_aliases içinde değil"):
            Derivation(
                kind="join", source_aliases=["aaa", "bbb"],
                join_keys=[CalculatedJoinKey(
                    left_alias="ghost", left_column="X",
                    right_alias="bbb", right_column="Y")],
            )

    def test_union_valid(self):
        d = Derivation(kind="union", source_aliases=["q1_data", "q2_data"], union_all=False)
        assert d.kind == "union" and d.union_all is False

    def test_union_requires_two_sources(self):
        with pytest.raises(ValueError, match="en az 2 source_aliases"):
            Derivation(kind="union", source_aliases=["solo"])

    def test_union_rejects_other_kinds_fields(self):
        with pytest.raises(ValueError, match="yalnız source_aliases"):
            Derivation(kind="union", source_aliases=["aaa", "bbb"], group_by=["X"])


# ── SQL compilers ───────────────────────────────────────────────────────────

def _join_item(join_type="inner") -> BasketItem:
    d = Derivation(
        kind="join",
        source_aliases=["deposits", "competitor"],
        join_keys=[CalculatedJoinKey(
            left_alias="deposits", left_column="BRANCH_CODE",
            right_alias="competitor", right_column="BRANCH_CODE")],
        join_type=join_type,
    )
    return BasketItem(alias="dep_comp_join", derivation=d,
                      projection=Projection(columns=[], include_all=True),
                      routing=Routing(decision="cached", estimated_bytes=0))


class TestCompileJoinSql:

    def test_prefixes_colliding_right_columns(self):
        sql = compile_join_sql(_join_item(), ["BRANCH_CODE", "BALANCE"], ["BRANCH_CODE", "RATE"])
        # left cols passthrough, right collision prefixed, non-collision kept
        assert 'AS "BRANCH_CODE"' in sql
        assert 'AS "competitor_BRANCH_CODE"' in sql
        assert 'AS "RATE"' in sql

    def test_inner_vs_left(self):
        assert "INNER JOIN" in compile_join_sql(_join_item("inner"), ["A"], ["B"])
        assert "LEFT JOIN" in compile_join_sql(_join_item("left"), ["A"], ["B"])

    def test_duckdb_end_to_end(self):
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE deposits AS SELECT 'B01' AS BRANCH_CODE, 100 AS BALANCE "
                     "UNION ALL SELECT 'B02', 200")
        conn.execute("CREATE TABLE competitor AS SELECT 'B01' AS BRANCH_CODE, 5.0 AS RATE "
                     "UNION ALL SELECT 'B02', 6.0")
        lc = list(conn.execute("SELECT * FROM deposits LIMIT 0").fetchdf().columns)
        rc = list(conn.execute("SELECT * FROM competitor LIMIT 0").fetchdf().columns)
        df = conn.execute(compile_join_sql(_join_item(), lc, rc)).fetchdf()
        assert list(df.columns) == ["BRANCH_CODE", "BALANCE", "competitor_BRANCH_CODE", "RATE"]
        assert len(df) == 2


def _union_item(union_all=True) -> BasketItem:
    d = Derivation(kind="union", source_aliases=["q1_data", "q2_data"], union_all=union_all)
    return BasketItem(alias="q_union", derivation=d,
                      projection=Projection(columns=[], include_all=True),
                      routing=Routing(decision="cached", estimated_bytes=0))


class TestCompileUnionSql:

    def test_union_all_vs_distinct(self):
        assert "UNION ALL" in compile_union_sql(_union_item(True))
        sql = compile_union_sql(_union_item(False))
        assert "UNION" in sql and "UNION ALL" not in sql

    def test_duckdb_end_to_end_all_keeps_dups(self):
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE q1_data AS SELECT 1 AS A, 2 AS B")
        conn.execute("CREATE TABLE q2_data AS SELECT 1 AS A, 2 AS B")  # identical row
        df = conn.execute(compile_union_sql(_union_item(True))).fetchdf()
        assert len(df) == 2  # UNION ALL keeps both
        df2 = conn.execute(compile_union_sql(_union_item(False))).fetchdf()
        assert len(df2) == 1  # UNION dedups

    def test_duckdb_column_count_mismatch_errors(self):
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE q1_data AS SELECT 1 AS A, 2 AS B")
        conn.execute("CREATE TABLE q2_data AS SELECT 1 AS A")  # one column
        with pytest.raises(Exception):
            conn.execute(compile_union_sql(_union_item(True))).fetchdf()


# ── Sunum basket hides passive sources re-added for materialisation ──────────
# Regression: a table the user disabled in Hazırlık but that an active derived
# node needs (so _finalisedScope re-adds it, marking it in inactive_aliases)
# used to still appear in Sunum's "Veri Kaynakları". It must stay in the scope
# (so the derived node materialises) but be hidden from the Sunum basket.

class TestManifestBasketHidesInactive:

    def test_disabled_source_hidden_but_node_kept(self):
        from presentations.scope.schema import load_scope_from_dict
        from presentations.routes_scope import _manifest_basket_from_scope
        scope = load_scope_from_dict({"scope": {
            "presentation_id": "p_x", "version": 1, "created_by": "A16438",
            "created_at": "2026-06-11T00:00:00Z",
            "basket": [
                {"alias": "keep_tbl", "table_ref": {"schema": "EDW", "name": "A"},
                 "projection": {"columns": ["X"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
                {"alias": "hidden_src", "table_ref": {"schema": "EDW", "name": "B"},
                 "projection": {"columns": ["Y"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
                {"alias": "hidden_src_f", "derivation": {
                    "kind": "filter", "source_alias": "hidden_src",
                    "filters": {"pinned": [], "raw": [
                        {"id": "rf_y", "alias": "hidden_src", "column": "Y", "op": "gt", "value": 0}]}},
                 "projection": {"columns": [], "include_all": True},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
            ],
            "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
            "inactive_aliases": ["hidden_src"],  # disabled, re-added as filter source
        }})
        aliases = [b["alias"] for b in _manifest_basket_from_scope(scope)]
        assert "keep_tbl" in aliases          # active table stays
        assert "hidden_src_f" in aliases       # active derived node stays
        assert "hidden_src" not in aliases     # disabled source hidden from Sunum


# ── Manual-SQL node materialisation in the build fetch pass ──────────────────
# Regression: fetch_cached_tables used to skip sql nodes entirely (Pass 1 needs
# table_ref, Pass 2 needs derivation), so a manual-SQL dataset reached SCOPE_STORE
# but had no DuckDB view → showed up empty after a Sunum round-trip, and any
# derived node sourced from it failed to materialise.

class _StubDC:
    def __init__(self, df):
        self.df = df
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.calls.append(query)
        return self.df.copy()


def _scope(basket):
    return load_scope_from_dict({
        "presentation_id": "p_sql", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-11T00:00:00Z",
        "basket": basket, "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    })


class TestSqlNodeMaterialisation:

    def test_sql_node_registers_duckdb_view(self):
        conn = duckdb.connect(":memory:")
        dc = _StubDC(pd.DataFrame({"BRANCH_CODE": ["B01", "B02"], "BAL": [100, 200]}))
        scope = _scope([{
            "alias": "my_sql", "sql": "SELECT BRANCH_CODE, BAL FROM EDW.X",
            "projection": {"columns": ["BRANCH_CODE", "BAL"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 0},
        }])
        loaded = fetch_cached_tables(dc, conn, scope, catalog=None)
        assert "my_sql" in loaded
        assert conn.execute('SELECT COUNT(*) FROM "my_sql"').fetchone()[0] == 2
        assert dc.calls and dc.calls[0].startswith("SELECT BRANCH_CODE")

    def test_join_sourced_from_sql_nodes_resolves(self):
        conn = duckdb.connect(":memory:")
        dc = _StubDC(pd.DataFrame({"BRANCH_CODE": ["B01", "B02"], "BAL": [100, 200]}))
        scope = _scope([
            {"alias": "sql_a", "sql": "SELECT BRANCH_CODE, BAL FROM EDW.A",
             "projection": {"columns": ["BRANCH_CODE", "BAL"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 0}},
            {"alias": "sql_b", "sql": "SELECT BRANCH_CODE, BAL FROM EDW.B",
             "projection": {"columns": ["BRANCH_CODE", "BAL"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 0}},
            {"alias": "joined", "derivation": {
                "kind": "join", "source_aliases": ["sql_a", "sql_b"],
                "join_keys": [{"left_alias": "sql_a", "left_column": "BRANCH_CODE",
                               "right_alias": "sql_b", "right_column": "BRANCH_CODE"}],
                "join_type": "inner"},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
        ])
        loaded = fetch_cached_tables(dc, conn, scope, catalog=None)
        assert {"sql_a", "sql_b", "joined"} <= set(loaded.keys())
        cols = [c[0] for c in conn.execute('SELECT * FROM "joined" LIMIT 0').description]
        assert "sql_b_BRANCH_CODE" in cols and "sql_b_BAL" in cols  # right collisions prefixed

"""Block-SQL engine routing: blocks that reference scope datasets (registered
in the session DuckDB as views named by their alias — cached tables, manual-SQL
nodes, and aggregate/filter/calculated derivations) must execute in DuckDB, not
Oracle. Blocks that reference only real Oracle tables still route to Oracle.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from presentations import duck


class OracleStub:
    """Records queries routed to Oracle; returns a 1-row frame."""

    def __init__(self):
        self.calls: list[str] = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.calls.append(query)
        return pd.DataFrame({"oracle_col": [1]})


# ── find_view_refs ───────────────────────────────────────────────────────────

class TestFindViewRefs:
    def test_matches_bare_alias(self):
        views = ["deposits_by_branch", "positions"]
        assert duck.find_view_refs(
            "SELECT * FROM deposits_by_branch", views,
        ) == ["deposits_by_branch"]

    def test_case_insensitive(self):
        assert duck.find_view_refs("select * from POSITIONS", ["positions"]) == ["positions"]

    def test_ignores_qualified_column_ref(self):
        # `t.deposits` is a column on alias t, not a reference to the view.
        assert duck.find_view_refs("SELECT t.deposits FROM t", ["deposits"]) == []

    def test_ignores_substring(self):
        assert duck.find_view_refs("SELECT * FROM deposits_daily", ["deposits"]) == []


# ── execute_block_sql routing ────────────────────────────────────────────────

class TestExecuteBlockRouting:
    def test_block_on_derived_view_runs_in_duckdb(self):
        conn = duckdb.connect()
        duck.register_dataframe(
            conn, "deposits_by_branch",
            pd.DataFrame({"branch": ["A", "B"], "total": [10, 5]}),
        )
        dc = OracleStub()
        ds = duck.execute_block_sql(dc, conn, "blk_derived",
                                    "SELECT * FROM deposits_by_branch")
        assert ds["engine"] == "duckdb"
        assert ds["row_count"] == 2
        assert dc.calls == []  # Oracle must NOT be queried for a derived node

    def test_block_joining_two_scope_views_runs_in_duckdb(self):
        conn = duckdb.connect()
        duck.register_dataframe(conn, "deposits",
                                pd.DataFrame({"k": [1], "bal": [100]}))
        duck.register_dataframe(conn, "branches",
                                pd.DataFrame({"k": [1], "name": ["X"]}))
        dc = OracleStub()
        ds = duck.execute_block_sql(
            dc, conn, "blk_join",
            "SELECT b.name, d.bal FROM deposits d JOIN branches b ON d.k = b.k",
        )
        assert ds["engine"] == "duckdb"
        assert ds["row_count"] == 1
        assert dc.calls == []

    def test_block_on_real_oracle_table_routes_to_oracle(self):
        conn = duckdb.connect()  # no scope views registered
        dc = OracleStub()
        ds = duck.execute_block_sql(dc, conn, "blk_oracle",
                                    "SELECT * FROM EDW.SOME_TABLE")
        assert ds["engine"] == "oracle"
        assert len(dc.calls) == 1  # routed to Oracle, as before

"""Manual-run + materialize robustness:
- run_block_sql_routed sends block SQL that references a scope dataset view to
  DuckDB (so blocks can source from Hazırlık-produced nodes) and plain Oracle
  tables to Oracle.
- execute_with_binds translates the binder's :name placeholders to DuckDB $name.
- materialize._dedupe_columns keeps parquet writes alive on duplicate columns.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from presentations import duck
from presentations.scope.materialize import _dedupe_columns


class OracleStub:
    def __init__(self):
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.calls.append({"query": query, "params": query_params})
        return pd.DataFrame({"oracle_col": [1]})


class TestRunBlockSqlRouted:
    def test_scope_view_ref_runs_in_duckdb(self):
        conn = duckdb.connect()
        duck.register_dataframe(conn, "daily_res_myu",
                                pd.DataFrame({"RES_ID": [1, 2], "OFFERED_RATE": [10, 20]}))
        dc = OracleStub()
        df, engine = duck.run_block_sql_routed(
            dc, conn, "b1", "SELECT RES_ID FROM daily_res_myu", {})
        assert engine == "duckdb"
        assert len(df) == 2
        assert dc.calls == []  # Oracle untouched

    def test_plain_oracle_table_routes_to_oracle(self):
        conn = duckdb.connect()
        dc = OracleStub()
        df, engine = duck.run_block_sql_routed(
            dc, conn, "b2", "SELECT * FROM EDW.SOME_TABLE", {"p": 1})
        assert engine == "oracle"
        assert dc.calls and dc.calls[0]["params"] == {"p": 1}


class TestExecuteWithBinds:
    def test_no_params_plain_execute(self):
        conn = duckdb.connect()
        duck.register_dataframe(conn, "t", pd.DataFrame({"a": [1, 2, 3]}))
        df = duck.execute_with_binds(conn, "SELECT * FROM t", None)
        assert len(df) == 3

    def test_named_bind_translated_to_dollar(self):
        conn = duckdb.connect()
        duck.register_dataframe(conn, "t", pd.DataFrame({"a": [1, 2, 3]}))
        df = duck.execute_with_binds(conn, "SELECT * FROM t WHERE a = :a", {"a": 2})
        assert df["a"].tolist() == [2]

    def test_overlapping_bind_names_not_clobbered(self):
        conn = duckdb.connect()
        duck.register_dataframe(conn, "t", pd.DataFrame({"a": [1], "b": [5]}))
        # :a is a prefix of :a_list — longest-first replacement must keep both.
        df = duck.execute_with_binds(
            conn, "SELECT * FROM t WHERE a = :a AND b = :a_list",
            {"a": 1, "a_list": 5})
        assert len(df) == 1


class TestDedupeColumns:
    def test_duplicates_renamed(self):
        df = pd.DataFrame([[1, 2, 3, 4]], columns=["CCY_CODE", "DAT", "CCY_CODE", "DAT"])
        out = _dedupe_columns(df)
        assert list(out.columns) == ["CCY_CODE", "DAT", "CCY_CODE_2", "DAT_2"]

    def test_no_duplicates_unchanged(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        out = _dedupe_columns(df)
        assert list(out.columns) == ["a", "b"]

    def test_deduped_df_writes_to_parquet(self):
        import io
        df = pd.DataFrame([[1, 2]], columns=["X", "X"])
        _dedupe_columns(df).to_parquet(io.BytesIO(), index=False)  # must not raise

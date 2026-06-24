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

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
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

    def test_ignores_scope_view_name_in_string_literal(self):
        # Scope-view adı yalnızca string literal/etiket olarak geçiyor; gerçek
        # tablo Oracle'daki EDW.REAL_TABLE. DuckDB'ye yönlendirme TETİKLENMEMELİ.
        assert duck.find_view_refs(
            "SELECT 'deposits_by_branch' AS label FROM EDW.REAL_TABLE",
            ["deposits_by_branch"],
        ) == []

    def test_ignores_scope_view_name_as_column_alias(self):
        # `AS deposits_by_branch` bir kolon takma adı, tablo referansı değil.
        assert duck.find_view_refs(
            "SELECT some_col AS deposits_by_branch FROM EDW.REAL_TABLE",
            ["deposits_by_branch"],
        ) == []

    def test_ignores_scope_view_name_in_comment(self):
        assert duck.find_view_refs(
            "SELECT 1 /* deposits_by_branch */ FROM EDW.REAL_TABLE",
            ["deposits_by_branch"],
        ) == []

    def test_matches_real_join(self):
        assert duck.find_view_refs(
            "SELECT * FROM a JOIN positions p ON a.k = p.k", ["positions"],
        ) == ["positions"]

    def test_matches_view_shadowing_real_table(self):
        # Gerçek tablo adı bir scope-view ile aynıysa yine DuckDB'ye yönlenir
        # (bilinçli view-shadowing davranışı korunur).
        assert duck.find_view_refs("SELECT * FROM positions", ["positions"]) == ["positions"]

    def test_matches_real_view_after_comment(self):
        # FROM/JOIN ile tablo adı ARASINDA yorum olsa bile gerçek scope-view
        # yakalanmalı — aksi halde blok yanlışlıkla Oracle'a yönlenip
        # "table does not exist" ile patlardı (review'ın yakaladığı regresyon).
        assert duck.find_view_refs(
            "SELECT * FROM /* c */ positions", ["positions"]) == ["positions"]
        assert duck.find_view_refs(
            "SELECT * FROM a JOIN -- x\n positions ON 1=1", ["positions"]) == ["positions"]


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

    def test_block_mentioning_scope_view_in_literal_routes_to_oracle(self):
        # Regresyon: blok gerçek Oracle tablosundan SELECT yapıyor ama SQL bir
        # scope-view adını yalnızca string literal olarak içeriyor. Eski bare-
        # token regex bunu DuckDB'ye yanlış yönlendirip "table does not exist"
        # ile patlatıyordu; artık Oracle'a yönlenmeli.
        conn = duckdb.connect()
        duck.register_dataframe(
            conn, "deposits_by_branch",
            pd.DataFrame({"branch": ["A"], "total": [10]}),
        )
        dc = OracleStub()
        ds = duck.execute_block_sql(
            dc, conn, "blk_lit",
            "SELECT 'deposits_by_branch' AS label, x FROM EDW.SOME_TABLE",
        )
        assert ds["engine"] == "oracle"
        assert len(dc.calls) == 1

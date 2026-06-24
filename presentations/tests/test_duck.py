import duckdb
import pandas as pd
import pytest

from presentations import duck


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


class FakeDC:
    """In-memory DataClient that returns a different DF per query string."""
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
        self.calls.append({"dataset": dataset, "query": query})
        # Match by table_id occurrence in query
        for table_id, df in self.mapping.items():
            if table_id in (query or "") or table_id == dataset:
                return df.copy()
        return pd.DataFrame()


class TestJsonable:
    """_jsonable: pandas eksik-değer sentinel'leri JSON null'a inmeli; literal
    '<NA>'/'NaT' string'i sızmamalı (pandas3 nullable/Arrow dtype regresyonu)."""

    def test_nat_returns_none(self):
        assert duck._jsonable(pd.NaT) is None

    def test_na_returns_none(self):
        assert duck._jsonable(pd.NA) is None

    def test_valid_values_unchanged(self):
        assert duck._jsonable(None) is None
        assert duck._jsonable(float("nan")) is None
        assert duck._jsonable("x") == "x"
        assert duck._jsonable(True) is True
        assert duck._jsonable(3) == 3
        # Geçerli timestamp str() ile (BOŞLUK ayraçlı, 'T' değil) — korunur.
        assert duck._jsonable(pd.Timestamp("2025-01-02 03:04:05")) == "2025-01-02 03:04:05"

    def test_nullable_and_datetime_columns_roundtrip_to_none(self):
        """execute_block_sql kalıbı: Int64 null + datetime NaT, itertuples üzerinden
        _jsonable'a girer ve '<NA>'/'NaT' değil None olmalı."""
        df = pd.DataFrame({
            "n": pd.array([1, None], dtype="Int64"),
            "d": [pd.Timestamp("2025-01-01"), pd.NaT],
        })
        rows = [
            [duck._jsonable(v) for v in row]
            for row in df.itertuples(index=False, name=None)
        ]
        assert rows == [[1, "2025-01-01 00:00:00"], [None, None]]


class TestRegisterDataframe:
    def test_register_makes_view_queryable(self, conn):
        df = pd.DataFrame({"a": [1, 2, 3]})
        duck.register_dataframe(conn, "demo", df)
        assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 3

    def test_register_replaces_existing(self, conn):
        duck.register_dataframe(conn, "demo", pd.DataFrame({"a": [1]}))
        duck.register_dataframe(conn, "demo", pd.DataFrame({"a": [10, 20]}))
        assert conn.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 2


class TestFetchBasketItem:
    def test_basic_query_built(self):
        df = pd.DataFrame({"BRANCH_CODE": ["A", "B"], "BAL": [1.0, 2.0]})
        dc = FakeDC({"EDW.DEPOSITS_BY_BRANCH": df})
        result = duck.fetch_basket_item(
            dc,
            {"table": "EDW.DEPOSITS_BY_BRANCH", "columns": ["BRANCH_CODE", "BAL"], "row_filter": None},
        )
        assert len(result) == 2
        assert "SELECT BRANCH_CODE, BAL FROM EDW.DEPOSITS_BY_BRANCH" in dc.calls[0]["query"]

    def test_with_row_filter(self):
        df = pd.DataFrame({"X": [1]})
        dc = FakeDC({"T1": df})
        duck.fetch_basket_item(dc, {"table": "T1", "columns": ["X"], "row_filter": "X > 0"})
        assert "WHERE X > 0" in dc.calls[0]["query"]

    def test_default_columns_to_star(self):
        df = pd.DataFrame({"X": [1]})
        dc = FakeDC({"T1": df})
        duck.fetch_basket_item(dc, {"table": "T1"})
        assert "SELECT *" in dc.calls[0]["query"]

    def test_empty_result_gets_placeholder_schema(self):
        """DataClient returns column-less DF (no CSV mock) → we synthesize one
        with the requested columns so DuckDB.register won't choke."""
        dc = FakeDC({})  # no mappings, always returns pd.DataFrame()
        result = duck.fetch_basket_item(
            dc,
            {"table": "EDW.UNKNOWN", "columns": ["A", "B", "C"], "row_filter": None},
        )
        assert list(result.columns) == ["A", "B", "C"]
        assert len(result) == 0

    def test_empty_result_can_be_registered_in_duckdb(self, conn):
        dc = FakeDC({})
        df = duck.fetch_basket_item(
            dc, {"table": "EDW.X", "columns": ["A", "B"], "row_filter": None}
        )
        # This is the case that was crashing before the fix.
        duck.register_dataframe(conn, "x", df)
        assert conn.execute("SELECT COUNT(*) FROM x").fetchone()[0] == 0
        # And preview works on the empty frame.
        p = duck.preview_view(conn, "x")
        assert p["columns"] == ["A", "B"]
        assert p["row_count"] == 0


class TestPopulateBasket:
    def test_registers_each_item(self, conn):
        dc = FakeDC({
            "EDW.A": pd.DataFrame({"v": [1, 2]}),
            "EDW.B": pd.DataFrame({"v": [10, 20, 30]}),
        })
        loaded = duck.populate_basket(dc, conn, [
            {"table": "EDW.A", "columns": ["v"], "row_filter": None},
            {"table": "EDW.B", "columns": ["v"], "row_filter": None},
        ])
        assert loaded == {
            "a": {"table": "EDW.A", "rows": 2},
            "b": {"table": "EDW.B", "rows": 3},
        }
        assert set(duck.list_views(conn)) >= {"a", "b"}


class TestPreviewView:
    def test_preview_basic(self, conn):
        duck.register_dataframe(conn, "demo", pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}))
        p = duck.preview_view(conn, "demo")
        assert p["row_count"] == 3
        assert p["columns"] == ["a", "b"]
        assert len(p["rows"]) == 3

    def test_preview_respects_limit(self, conn):
        duck.register_dataframe(conn, "demo", pd.DataFrame({"a": list(range(50))}))
        p = duck.preview_view(conn, "demo", limit=5)
        assert len(p["rows"]) == 5
        assert p["row_count"] == 50

    def test_rejects_unsafe_view_name(self, conn):
        with pytest.raises(ValueError):
            duck.preview_view(conn, "demo; DROP TABLE foo")

    def test_preview_nan_is_json_null_not_literal_nan(self, conn):
        """NaN'lı float kolon önizlemesi geçerli JSON üretmeli: ham `NaN` değil null.
        Client'ta 'Unexpected token N ... is not valid JSON' bunun regresyonuydu."""
        import json
        duck.register_dataframe(
            conn, "demo", pd.DataFrame({"a": [1.0, float("nan"), 3.0], "b": ["x", "y", "z"]})
        )
        p = duck.preview_view(conn, "demo")
        assert p["rows"][1][0] is None
        # Asıl bozulma: json.dumps ham NaN'da geçersiz JSON yazıyordu.
        dumped = json.dumps(p)
        assert "NaN" not in dumped
        assert json.loads(dumped)["rows"][1][0] is None


# ── D3: materialize_table — gerçek tablo, bağlantı kapansa da yaşar ──────────

class TestMaterializeTable:
    def test_survives_reconnect(self, tmp_path):
        import pandas as pd
        from presentations import duck

        path = str(tmp_path / "s.duckdb")
        conn = duck.connect_duckdb(path)
        duck.materialize_table(conn, "ds1", pd.DataFrame({"A": [1, 2]}))
        assert conn.execute('SELECT COUNT(*) FROM "ds1"').fetchone()[0] == 2
        conn.close()

        conn2 = duck.connect_duckdb(path)
        assert conn2.execute('SELECT COUNT(*) FROM "ds1"').fetchone()[0] == 2
        assert "ds1" in duck.list_views(conn2)
        conn2.close()

    def test_replaces_legacy_view(self):
        import pandas as pd
        from presentations import duck

        conn = duck.connect_duckdb(":memory:")
        duck.register_dataframe(conn, "ds2", pd.DataFrame({"A": [1]}))
        duck.materialize_table(conn, "ds2", pd.DataFrame({"A": [1, 2, 3]}))
        assert conn.execute('SELECT COUNT(*) FROM "ds2"').fetchone()[0] == 3

    def test_re_materialize_existing_table(self):
        """A-fix regression: ikinci materialize (re-build / cron refresh) — `name`
        artık gerçek bir TABLE; eski `DROP VIEW IF EXISTS` duckdb 1.5.2'de
        tip-uyuşmazlığı CatalogException atıyordu (her yeniden-build çöküyordu).
        Aynı kalıcı conn'da tekrar materialize çökmemeli, veriyi değiştirmeli."""
        import pandas as pd
        from presentations import duck

        conn = duck.connect_duckdb(":memory:")
        duck.materialize_table(conn, "ds3", pd.DataFrame({"A": [1, 2]}))
        # İkinci materialize — eskiden burada CatalogException patlıyordu.
        duck.materialize_table(conn, "ds3", pd.DataFrame({"A": [10, 20, 30]}))
        assert conn.execute('SELECT COUNT(*) FROM "ds3"').fetchone()[0] == 3
        assert conn.execute('SELECT SUM(A) FROM "ds3"').fetchone()[0] == 60
        conn.close()

    def test_internal_names_hidden_from_list_views(self):
        import pandas as pd
        from presentations import duck

        conn = duck.connect_duckdb(":memory:")
        conn.execute("CREATE TABLE __dataset_meta(alias VARCHAR, refreshed_at VARCHAR)")
        duck.materialize_table(conn, "real_ds", pd.DataFrame({"A": [1]}))
        names = duck.list_views(conn)
        assert "real_ds" in names
        assert all(not n.startswith("__") for n in names)

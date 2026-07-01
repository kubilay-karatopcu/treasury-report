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

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
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


def test_compose_row_cap_opt_in():
    # #27: the lazy path passes max_rows so an un-narrowed fetch can't OOM; the
    # cached path leaves it None (routing keeps cached tables small) → no cap.
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["CCY"], "include_all": False},
        "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000},
    }])
    capped, _ = compose_cached_sql(scope, scope.basket[0], max_rows=1000)
    assert capped.endswith("FETCH FIRST 1000 ROWS ONLY")
    uncapped, _ = compose_cached_sql(scope, scope.basket[0])
    assert "FETCH FIRST" not in uncapped


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


# ── Pasif + lineage-only alias'lar fetch edilmez (Bug 1 / Sunum'a geç) ───────

def _scope_with_inactive(extra_items, inactive):
    raw = {
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": extra_items, "filters": {"pinned": [], "interactive": []},
        "inactive_aliases": inactive,
    }
    return load_scope_from_dict(raw)


def test_fetch_skips_inactive_lineage_only_main():
    # Manuel-SQL node'unun "Çözümle" kaynak main'i: pasif + yalnız derived_from
    # lineage'ı → Oracle'dan ÇEKİLMEZ. SQL dataset'in kendisi çekilir.
    df = pd.DataFrame({"A": [1]})
    dc = StubDC(df)
    conn = duckdb.connect(":memory:")
    scope = _scope_with_inactive([
        {"table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
         "alias": "positions",
         "projection": {"columns": ["A"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
        {"sql": "SELECT 1 AS A FROM DUAL", "alias": "my_sql",
         "projection": {"columns": ["A"], "include_all": False},
         "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 0},
         "derived_from": ["positions"]},
    ], inactive=["positions"])
    loaded = fetch_cached_tables(dc, conn, scope, catalog=_catalog())
    assert set(loaded.keys()) == {"my_sql"}
    datasets = [c["dataset"] for c in dc.calls]
    assert all("positions" not in d for d in datasets)


def test_fetch_inactive_main_pulled_transiently_not_persisted():
    # Karar (Oturum 1, A2): pasif (disable) main, aktif bir CACHED aggregate'in
    # DuckDB kaynağıysa node'u materialize etmek için GEÇİCİ çekilir — ama dataset
    # olarak persist EDİLMEZ (`loaded`'a girmez, parquet yazılmaz). "disable ise
    # cache'lenmesin, sadece son tabloyu üretirken kullanılsın."
    df = pd.DataFrame({"CCY": ["TRY"], "BAL": [10]})
    dc = StubDC(df)
    conn = duckdb.connect(":memory:")
    scope = _scope_with_inactive([
        {"table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
         "alias": "positions",
         "projection": {"columns": ["CCY", "BAL"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
        {"derivation": {"kind": "aggregate", "source_alias": "positions",
                        "group_by": ["CCY"],
                        "measures": [{"column": "BAL", "fn": "sum", "as": "SUM_BAL"}]},
         "alias": "pos_agg",
         "projection": {"columns": ["CCY", "SUM_BAL"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
    ], inactive=["positions"])
    loaded = fetch_cached_tables(dc, conn, scope, catalog=_catalog())
    # Aktif türev persist edilir; pasif kaynak EDİLMEZ.
    assert "pos_agg" in loaded
    assert "positions" not in loaded
    # Ama kaynak son tabloyu üretmek için GEÇİCİ çekildi → aggregate doğru.
    assert any("positions" in c["dataset"] for c in dc.calls)
    agg = conn.execute('SELECT * FROM "pos_agg"').fetchdf()
    assert int(agg["SUM_BAL"].iloc[0]) == 10


def test_fetch_cached_guard_raises_on_gross_underestimate():
    # Tahminin çok üstünde dönen cached pull SESSİZCE KIRPILMAZ — hata verir
    # (kırpmak blok verisini bozar). Guard: max(SCOPE_FETCH_ROW_CAP, est×3).
    from presentations.scope import fetch as fetch_mod

    big = pd.DataFrame({"A": range(12)})
    dc = StubDC(big)
    conn = duckdb.connect(":memory:")
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["A"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    orig = fetch_mod.SCOPE_FETCH_ROW_CAP
    fetch_mod.SCOPE_FETCH_ROW_CAP = 10   # test için tabanı küçült
    try:
        with pytest.raises(RuntimeError, match="beklenenden çok daha büyük"):
            fetch_cached_tables(dc, conn, scope, catalog=_catalog())
    finally:
        fetch_mod.SCOPE_FETCH_ROW_CAP = orig


# ── Oturum 2.2: cancel-token threaded through fetch_cached_tables ────────────

def test_fetch_precancelled_token_raises_without_pulling():
    """Pre-cancelled token → BuildCancelled at the boundary, BEFORE any Oracle pull."""
    from presentations.scope.cancel import BuildCancelled, CancelToken
    dc = StubDC(pd.DataFrame({"A": [1]}))
    conn = duckdb.connect(":memory:")
    scope = _scope([{
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["A"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(BuildCancelled):
        fetch_cached_tables(dc, conn, scope, catalog=_catalog(), cancel_token=tok)
    assert dc.calls == []        # never reached Oracle


def test_fetch_cancel_during_pass1_caught_at_pass2_boundary():
    """A cancel that arrives during the Pass-1 source pull is caught at the
    Pass-2 derivation boundary → the derived node never runs, BuildCancelled
    unwinds (releasing the session lock in the real build)."""
    from presentations.scope.cancel import BuildCancelled, CancelToken
    tok = CancelToken()

    class _SelfCancelDC:
        def __init__(self):
            self.calls = []
        def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
            self.calls.append(dataset)
            tok.cancel()                      # cancel arrives mid-build
            return pd.DataFrame({"CCY": ["TRY"], "BAL": [10]})

    dc = _SelfCancelDC()
    conn = duckdb.connect(":memory:")
    scope = _scope([
        {"table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
         "alias": "positions",
         "projection": {"columns": ["CCY", "BAL"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
        {"derivation": {"kind": "aggregate", "source_alias": "positions",
                        "group_by": ["CCY"],
                        "measures": [{"column": "BAL", "fn": "sum", "as": "SUM_BAL"}]},
         "alias": "pos_agg",
         "projection": {"columns": ["CCY", "SUM_BAL"], "include_all": False},
         "routing": {"decision": "cached", "estimated_bytes": 0}},
    ])
    with pytest.raises(BuildCancelled):
        fetch_cached_tables(dc, conn, scope, catalog=_catalog(), cancel_token=tok)
    assert dc.calls == ["scope::p_x/positions"]   # src pulled; agg never ran


# ── D1 (Oturum N6) — projection-node-over-big-table join-key pushdown ──────────

from presentations.scope.fetch import _join_key_pushdown


class _MultiDC:
    """Tablo adına göre farklı df dönen stub — D1 entegrasyon testleri için."""
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        self.calls.append({"dataset": dataset, "query": query, "params": query_params})
        for key, df in self.frames.items():
            if key in (query or ""):
                return df.copy()
        return pd.DataFrame()


def _d1_scope(join_type="inner", secili_right=True, identity=True):
    """big (lazy) → big_secili (calculated identity projection) ⋈ small (cached)."""
    big = {"table_ref": {"schema": "EDW", "name": "BIG"}, "alias": "big",
           "projection": {"columns": ["BRANCH_ID", "AMT"], "include_all": False},
           "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}}
    expr = '"BRANCH_ID"' if identity else 'UPPER("BRANCH_ID")'
    big_secili = {"alias": "big_secili",
        "derivation": {"kind": "calculated", "source_aliases": ["big"], "join_keys": [],
                       "columns": [{"name": "BRANCH_ID", "expr": expr},
                                   {"name": "AMT", "expr": '"AMT"'}]},
        "projection": {"columns": ["BRANCH_ID", "AMT"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    small = {"table_ref": {"schema": "EDW", "name": "SMALL"}, "alias": "small",
             "projection": {"columns": ["BRANCH_ID"], "include_all": False},
             "routing": {"decision": "cached", "estimated_bytes": 0}}
    srcs = ["small", "big_secili"] if secili_right else ["big_secili", "small"]
    join = {"alias": "joined",
        "derivation": {"kind": "join", "source_aliases": srcs, "join_type": join_type,
                       "join_keys": [{"left_alias": "small", "left_column": "BRANCH_ID",
                                      "right_alias": "big_secili", "right_column": "BRANCH_ID"}]},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    return _scope([big, big_secili, small, join])


def _conn_with_small(values=("B1", "B2", "B2", None)):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE small(BRANCH_ID VARCHAR)")
    conn.executemany("INSERT INTO small VALUES (?)", [(v,) for v in values])
    return conn


def test_d1_transitive_pushdown_inner_identity():
    scope = _d1_scope()
    conn = _conn_with_small()
    res = _join_key_pushdown(conn, scope, scope.basket_item("big_secili"),
                             scope.basket_item("big"), {"small"})
    assert res is not None
    col, vals = res
    assert col == "BRANCH_ID" and sorted(vals) == ["B1", "B2"]


def test_d1_transitive_pushdown_left_join_secili_right_is_safe():
    # LEFT join, big_secili = source_aliases[1] (korunmayan sağ taraf) → güvenli.
    scope = _d1_scope(join_type="left", secili_right=True)
    res = _join_key_pushdown(_conn_with_small(), scope, scope.basket_item("big_secili"),
                             scope.basket_item("big"), {"small"})
    assert res is not None and res[0] == "BRANCH_ID"


def test_d1_no_pushdown_when_secili_is_left_preserved_side():
    # LEFT join, big_secili = source_aliases[0] (korunan sol) → daraltma eşleşmeyen
    # satırları düşürür → GÜVENSİZ → tam pull (None).
    scope = _d1_scope(join_type="left", secili_right=False)
    res = _join_key_pushdown(_conn_with_small(), scope, scope.basket_item("big_secili"),
                             scope.basket_item("big"), {"small"})
    assert res is None


def test_d1_no_pushdown_for_computed_join_key():
    # join anahtarı identity DEĞİL (UPPER(...)) → kaynak kolonuna maplenemez → None.
    scope = _d1_scope(identity=False)
    res = _join_key_pushdown(_conn_with_small(), scope, scope.basket_item("big_secili"),
                             scope.basket_item("big"), {"small"})
    assert res is None


def test_d1_no_pushdown_when_partner_not_registered():
    scope = _d1_scope()
    res = _join_key_pushdown(_conn_with_small(), scope, scope.basket_item("big_secili"),
                             scope.basket_item("big"), set())
    assert res is None


def test_d1_direct_join_pushdown_still_works():
    # big doğrudan bir join'in lazy tarafı (araya projection node yok) — A3 korunur.
    big = {"table_ref": {"schema": "EDW", "name": "BIG"}, "alias": "big",
           "projection": {"columns": ["BRANCH_ID"], "include_all": False},
           "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}}
    small = {"table_ref": {"schema": "EDW", "name": "SMALL"}, "alias": "small",
             "projection": {"columns": ["BRANCH_ID"], "include_all": False},
             "routing": {"decision": "cached", "estimated_bytes": 0}}
    join = {"alias": "joined",
        "derivation": {"kind": "join", "source_aliases": ["small", "big"], "join_type": "inner",
                       "join_keys": [{"left_alias": "small", "left_column": "BRANCH_ID",
                                      "right_alias": "big", "right_column": "BRANCH_ID"}]},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    scope = _scope([big, small, join])
    res = _join_key_pushdown(_conn_with_small(), scope, scope.basket_item("joined"),
                             scope.basket_item("big"), {"small"})
    assert res is not None and res[0] == "BRANCH_ID" and sorted(res[1]) == ["B1", "B2"]


def test_d1_fetch_narrows_big_projection_source_end_to_end():
    # Tam akış: build BIG'i big_secili için çekerken transitif pushdown'la daraltır.
    scope = _d1_scope()
    dc = _MultiDC({"EDW.SMALL": pd.DataFrame({"BRANCH_ID": ["B1", "B2"]}),
                   "EDW.BIG": pd.DataFrame({"BRANCH_ID": ["B1", "B2", "B3"], "AMT": [1, 2, 3]})})
    conn = duckdb.connect(":memory:")
    fetch_cached_tables(dc, conn, scope, catalog=None)
    big_calls = [c for c in dc.calls if "EDW.BIG" in (c["query"] or "")]
    assert big_calls, "BIG hiç çekilmedi"
    assert "BRANCH_ID IN (" in big_calls[0]["query"]
    assert set((big_calls[0]["params"] or {}).values()) == {"B1", "B2"}


def test_d1_ordering_registers_derived_partner_before_big_projection():
    # Adversaryal basket sırası: big_secili, küçük partner'dan (Pass-2 derived) ÖNCE
    # listeli. Sıralama düzeltmesi olmadan big_secili BIG'i partner registered olmadan
    # çeker → tam pull. Sıralama, lazy-pull GEREKTİRMEYEN türevleri önce işler.
    big = {"table_ref": {"schema": "EDW", "name": "BIG"}, "alias": "big",
           "projection": {"columns": ["BRANCH_ID", "AMT"], "include_all": False},
           "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}}
    big_secili = {"alias": "big_secili",
        "derivation": {"kind": "calculated", "source_aliases": ["big"], "join_keys": [],
                       "columns": [{"name": "BRANCH_ID", "expr": '"BRANCH_ID"'},
                                   {"name": "AMT", "expr": '"AMT"'}]},
        "projection": {"columns": ["BRANCH_ID", "AMT"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    small_src = {"table_ref": {"schema": "EDW", "name": "SMALL"}, "alias": "small_src",
        "projection": {"columns": ["BRANCH_ID"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    small_proj = {"alias": "small_proj",
        "derivation": {"kind": "calculated", "source_aliases": ["small_src"], "join_keys": [],
                       "columns": [{"name": "BRANCH_ID", "expr": '"BRANCH_ID"'}]},
        "projection": {"columns": ["BRANCH_ID"], "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    join = {"alias": "joined",
        "derivation": {"kind": "join", "source_aliases": ["small_proj", "big_secili"], "join_type": "inner",
                       "join_keys": [{"left_alias": "small_proj", "left_column": "BRANCH_ID",
                                      "right_alias": "big_secili", "right_column": "BRANCH_ID"}]},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "estimated_bytes": 0}}
    scope = _scope([big, big_secili, small_src, small_proj, join])  # big_secili before small_proj
    dc = _MultiDC({"EDW.SMALL": pd.DataFrame({"BRANCH_ID": ["B1", "B2"]}),
                   "EDW.BIG": pd.DataFrame({"BRANCH_ID": ["B1", "B2", "B3"], "AMT": [1, 2, 3]})})
    conn = duckdb.connect(":memory:")
    fetch_cached_tables(dc, conn, scope, catalog=None)
    big_calls = [c for c in dc.calls if "EDW.BIG" in (c["query"] or "")]
    assert big_calls and "BRANCH_ID IN (" in big_calls[0]["query"]
    assert set((big_calls[0]["params"] or {}).values()) == {"B1", "B2"}


# ── M7/K3 — lazy↔lazy join → tek Oracle sorgusu (RAM'e çekmeden) ──────────────

def test_lazy_lazy_join_runs_as_single_oracle_query():
    big = {"table_ref": {"schema": "EDW", "name": "BIG"}, "alias": "big",
           "projection": {"columns": ["K", "AMT"], "include_all": False},
           "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}}
    sml = {"table_ref": {"schema": "EDW", "name": "SML"}, "alias": "sml",
           "projection": {"columns": ["K", "NAME"], "include_all": False},
           "routing": {"decision": "lazy", "estimated_bytes": 9_000_000_000}}
    join = {"alias": "joined", "derivation": {"kind": "join",
            "source_aliases": ["big", "sml"], "join_type": "inner",
            "join_keys": [{"left_alias": "big", "left_column": "K",
                           "right_alias": "sml", "right_column": "K"}]},
            "projection": {"columns": [], "include_all": True},
            "routing": {"decision": "cached", "estimated_bytes": 0}}
    scope = _scope([big, sml, join])

    class _JoinDC:
        def __init__(self):
            self.queries = []
        def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
            self.queries.append(query)
            return pd.DataFrame({"K": [1], "AMT": [10.0], "sml_K": [1], "NAME": ["X"]})

    dc = _JoinDC()
    conn = duckdb.connect(":memory:")
    fetch_cached_tables(dc, conn, scope, catalog=None)

    # İki lazy kaynak AYRI AYRI full-pull edilmedi → TEK Oracle JOIN sorgusu.
    assert len(dc.queries) == 1, dc.queries
    q = dc.queries[0]
    assert "INNER JOIN" in q and "EDW.BIG" in q and "EDW.SML" in q
    # Sonuç DuckDB'ye materialise edildi (cache'lenir).
    assert conn.execute('SELECT COUNT(*) FROM joined').fetchone()[0] == 1

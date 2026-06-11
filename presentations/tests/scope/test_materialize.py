"""Faz A — dataset materialisation (S3 parquet) + DuckDB read-back tests."""
from __future__ import annotations

import io
import json

import duckdb
import pandas as pd
import pytest
from pydantic import ValidationError

from presentations.duck import connect_duckdb
from presentations.scope.materialize import (
    dataset_data_key,
    dataset_meta_key,
    load_into_duck,
    materialize_dataset,
    project_block_from_dataset,
    read_dataset,
    read_dataset_meta,
    write_dataset,
)
from presentations.scope.schema import load_scope_from_dict


# ── Fakes ───────────────────────────────────────────────────────────────────

class _FakeDC:
    """In-memory S3 + Oracle stub mirroring the DataClient surface used here."""

    def __init__(self, df: pd.DataFrame | None = None):
        self.objects: dict[str, bytes] = {}
        self._df = df if df is not None else pd.DataFrame()
        self.get_data_calls: list[dict] = []

    # S3
    def _upload_bytes(self, key, data, content_type=None, *, if_none_match=False):
        self.objects[key] = bytes(data)

    def read_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_json(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return json.loads(self.objects[key].decode("utf-8"))

    # Oracle
    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.get_data_calls.append({"query": query, "params": query_params})
        return self._df.copy()


def _scope(routing="cached", refresh=None):
    item = {
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": ["AS_OF_DATE", "CCY", "NET_POSITION"], "include_all": False},
        "routing": {"decision": routing, "estimated_bytes": 1000},
    }
    if refresh is not None:
        item["refresh"] = refresh
    return load_scope_from_dict({
        "presentation_id": "p_mat", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [item], "filters": {"pinned": [], "interactive": []},
    })


def _sql_scope(sql="SELECT 1 AS A", routing="cached", refresh=None):
    item = {"sql": sql, "alias": "big_query",
            "routing": {"decision": routing, "estimated_bytes": 1000}}
    if refresh is not None:
        item["refresh"] = refresh
    return load_scope_from_dict({
        "presentation_id": "p_mat", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [item], "filters": {"pinned": [], "interactive": []},
    })


# ── Schema: refresh requires cached ─────────────────────────────────────────

def test_scheduled_refresh_allowed_on_cached():
    scope = _scope(routing="cached", refresh={"kind": "scheduled", "interval_seconds": 600})
    assert scope.basket[0].refresh.interval_seconds == 600


def test_scheduled_refresh_rejected_on_lazy():
    with pytest.raises(ValidationError):
        _scope(routing="lazy", refresh={"kind": "scheduled", "interval_seconds": 600})


def test_interval_without_scheduled_kind_rejected():
    with pytest.raises(ValidationError):
        _scope(routing="cached", refresh={"kind": "manual", "interval_seconds": 600})


# ── Parquet write / read round-trip ─────────────────────────────────────────

def test_write_then_read_roundtrip():
    dc = _FakeDC()
    df = pd.DataFrame({"CCY": ["TRY", "USD"], "NET_POSITION": [100.5, -3.2]})
    meta = write_dataset(dc, "p_mat", "positions", df, sql="SELECT ...")
    assert meta.row_count == 2
    assert meta.columns == ["CCY", "NET_POSITION"]
    assert dataset_data_key("p_mat", "positions") in dc.objects
    assert dataset_meta_key("p_mat", "positions") in dc.objects

    got = read_dataset(dc, "p_mat", "positions")
    assert got is not None
    rdf, rmeta = got
    pd.testing.assert_frame_equal(rdf, df)
    assert rmeta.row_count == 2
    assert rmeta.refreshed_dt() is not None


def test_read_missing_returns_none():
    dc = _FakeDC()
    assert read_dataset_meta(dc, "p_mat", "nope") is None
    assert read_dataset(dc, "p_mat", "nope") is None


# ── materialize_dataset: Oracle → parquet ───────────────────────────────────

def test_materialize_cached_table_ref():
    df = pd.DataFrame({"AS_OF_DATE": ["2026-06-01"], "CCY": ["TRY"], "NET_POSITION": [42.0]})
    dc = _FakeDC(df)
    scope = _scope(routing="cached", refresh={"kind": "scheduled", "interval_seconds": 600})
    meta = materialize_dataset(dc, scope, scope.basket[0])
    # Oracle was queried once with the composed projection SQL.
    assert len(dc.get_data_calls) == 1
    assert "FROM ODS_TREASURY.TRD_BRANCH_POSITION" in dc.get_data_calls[0]["query"]
    # Parquet persisted and reads back identically.
    rdf, _ = read_dataset(dc, "p_mat", "positions")
    pd.testing.assert_frame_equal(rdf, df)
    assert meta.row_count == 1


# ── Free-form SQL dataset source (Faz C) ────────────────────────────────────

def test_materialize_sql_dataset():
    df = pd.DataFrame({"A": [1, 2], "B": [10.0, 20.0]})
    dc = _FakeDC(df)
    scope = _sql_scope("SELECT a, b FROM big_union",
                       refresh={"kind": "scheduled", "interval_seconds": 600})
    materialize_dataset(dc, scope, scope.basket[0])
    # The user's free-form SQL ran verbatim against Oracle.
    assert dc.get_data_calls[-1]["query"] == "SELECT a, b FROM big_union"
    rdf, _ = read_dataset(dc, "p_mat", "big_query")
    pd.testing.assert_frame_equal(rdf, df)


def test_materialize_sql_rejects_non_select():
    dc = _FakeDC(pd.DataFrame())
    scope = _sql_scope("DELETE FROM big_union")
    with pytest.raises(ValueError):
        materialize_dataset(dc, scope, scope.basket[0])
    assert dc.get_data_calls == []  # rejected by whitelist before execution


def test_sql_dataset_loads_and_projects():
    df = pd.DataFrame({"CCY": ["TRY", "USD"], "TOTAL": [1.0, 2.0]})
    dc = _FakeDC(df)
    scope = _sql_scope("SELECT ccy, total FROM agg")
    materialize_dataset(dc, scope, scope.basket[0])
    conn = connect_duckdb(":memory:")
    loaded = load_into_duck(dc, conn, scope)
    assert "big_query" in loaded and loaded["big_query"]["rows"] == 2


def test_basket_item_exactly_one_source():
    # sql alone is valid.
    _sql_scope("SELECT 1")
    # table_ref + sql → reject (two sources).
    with pytest.raises(ValidationError):
        load_scope_from_dict({
            "presentation_id": "p", "version": 1, "created_by": "A", "created_at": "2026-06-15T10:00:00Z",
            "basket": [{"sql": "SELECT 1", "alias": "x",
                        "table_ref": {"schema": "S", "name": "T"},
                        "routing": {"decision": "cached", "estimated_bytes": 1}}],
        })
    # no source → reject.
    with pytest.raises(ValidationError):
        load_scope_from_dict({
            "presentation_id": "p", "version": 1, "created_by": "A", "created_at": "2026-06-15T10:00:00Z",
            "basket": [{"alias": "x", "routing": {"decision": "cached", "estimated_bytes": 1}}],
        })


# ── load_into_duck: parquet → DuckDB view, no Oracle ────────────────────────

def test_load_into_duck_registers_view():
    df = pd.DataFrame({"CCY": ["TRY", "USD", "EUR"], "NET_POSITION": [1.0, 2.0, 3.0]})
    dc = _FakeDC(df)
    scope = _scope(routing="cached")
    materialize_dataset(dc, scope, scope.basket[0])

    conn = connect_duckdb(":memory:")
    loaded = load_into_duck(dc, conn, scope)
    assert "positions" in loaded and loaded["positions"]["rows"] == 3
    # Chart-style projection runs locally on the registered view.
    n = conn.execute('SELECT COUNT(*) FROM positions WHERE "CCY" IN (\'TRY\',\'USD\')').fetchone()[0]
    assert n == 2


def test_load_into_duck_skips_unmaterialised():
    # No cron run yet → nothing registered, no error, no Oracle call.
    dc = _FakeDC()
    scope = _scope(routing="cached")
    conn = connect_duckdb(":memory:")
    assert load_into_duck(dc, conn, scope) == {}
    assert dc.get_data_calls == []


# ── project_block_from_dataset: interactive filters as DuckDB predicates ─────

def _proj_conn():
    conn = connect_duckdb(":memory:")
    conn.register("positions", pd.DataFrame({
        "AS_OF_DATE": pd.to_datetime(["2026-06-01", "2026-06-15", "2026-07-01"]),
        "CCY": ["TRY", "USD", "EUR"],
        "TOTAL": [1.0, 2.0, 3.0],
    }))
    return conn


def test_project_no_filters_returns_all():
    conn = _proj_conn()
    out = project_block_from_dataset(conn, {"alias": "positions", "columns": ["CCY", "TOTAL"]})
    assert list(out.columns) == ["CCY", "TOTAL"]
    assert len(out) == 3


def test_project_between_date_toggle():
    # The today/past toggle: a between filter narrows the materialised view in
    # DuckDB — no Oracle.
    conn = _proj_conn()
    binding = {
        "alias": "positions", "columns": ["CCY", "TOTAL"],
        "filters": [{"filter_id": "if_date", "column": "AS_OF_DATE", "op": "between"}],
    }
    out = project_block_from_dataset(conn, binding,
                                     {"if_date": {"from": "2026-06-01", "to": "2026-06-20"}})
    assert list(out["CCY"]) == ["TRY", "USD"]   # July row excluded


def test_project_in_filter():
    conn = _proj_conn()
    binding = {
        "alias": "positions", "columns": ["CCY", "TOTAL"],
        "filters": [{"filter_id": "if_ccy", "column": "CCY", "op": "in"}],
    }
    out = project_block_from_dataset(conn, binding, {"if_ccy": ["TRY", "EUR"]})
    assert set(out["CCY"]) == {"TRY", "EUR"}


def test_project_missing_filter_value_is_noop():
    conn = _proj_conn()
    binding = {
        "alias": "positions", "columns": ["CCY"],
        "filters": [{"filter_id": "if_date", "column": "AS_OF_DATE", "op": "between"}],
    }
    # filter_state has no entry for if_date → predicate skipped → all rows.
    assert len(project_block_from_dataset(conn, binding, {})) == 3


def test_project_rejects_bad_identifier():
    conn = _proj_conn()
    # A malformed column in a filter spec is skipped (no injection), not executed.
    binding = {
        "alias": "positions", "columns": ["CCY"],
        "filters": [{"filter_id": "if_x", "column": "CCY; DROP TABLE positions", "op": "eq"}],
    }
    out = project_block_from_dataset(conn, binding, {"if_x": "TRY"})
    assert out is not None and len(out) == 3  # bad column skipped, table intact
    assert _proj_conn() is not None


# ── Derived (aggregate) dataset materialisation — cron-able (Faz C) ──────────

def _derived_scope(routing="cached", refresh=None):
    """A `sql` source + an aggregate derived item grouping it."""
    src = {"sql": "SELECT ccy, total FROM big", "alias": "src",
           "routing": {"decision": "cached", "estimated_bytes": 1000}}
    der = {
        "derivation": {"kind": "aggregate", "source_alias": "src",
                       "group_by": ["CCY"],
                       "measures": [{"column": "TOTAL", "fn": "sum", "as": "TOTAL_SUM"}]},
        "alias": "agg",
        "projection": {"columns": ["CCY", "TOTAL_SUM"], "include_all": False},
        "routing": {"decision": routing, "decided_by": "system", "estimated_bytes": 0},
    }
    if refresh is not None:
        der["refresh"] = refresh
    return load_scope_from_dict({
        "presentation_id": "p_mat", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [src, der], "filters": {"pinned": [], "interactive": []},
    })


def test_materialize_derived_pulls_source_in_memory():
    # Source has no parquet yet → derived cron pulls it once (in-memory),
    # aggregates in DuckDB, and persists ONLY the small result. The big source
    # is NOT stored (a derived-on-lazy stays small).
    df = pd.DataFrame({"CCY": ["TRY", "TRY", "USD"], "TOTAL": [1.0, 2.0, 5.0]})
    dc = _FakeDC(df)
    scope = _derived_scope()
    materialize_dataset(dc, scope, scope.basket[1])
    rdf, _ = read_dataset(dc, "p_mat", "agg")
    assert {r.CCY: r.TOTAL_SUM for r in rdf.itertuples()} == {"TRY": 3.0, "USD": 5.0}
    assert read_dataset(dc, "p_mat", "src") is None  # source not persisted


def test_materialize_derived_reuses_source_parquet():
    # Source already materialised → derived reuses its parquet, NO Oracle pull.
    dc = _FakeDC()
    write_dataset(dc, "p_mat", "src",
                  pd.DataFrame({"CCY": ["TRY", "TRY", "USD"], "TOTAL": [1.0, 2.0, 5.0]}),
                  sql="SELECT ...")
    scope = _derived_scope()
    materialize_dataset(dc, scope, scope.basket[1])
    assert dc.get_data_calls == []  # parquet reused, no Oracle
    rdf, _ = read_dataset(dc, "p_mat", "agg")
    assert {r.CCY: r.TOTAL_SUM for r in rdf.itertuples()} == {"TRY": 3.0, "USD": 5.0}


def test_load_into_duck_registers_derived_view():
    # After materialisation a derived alias is a first-class parquet → the
    # viewer registers it like any other cached dataset (no read-time recompute).
    dc = _FakeDC()
    write_dataset(dc, "p_mat", "src",
                  pd.DataFrame({"CCY": ["TRY", "USD"], "TOTAL": [3.0, 5.0]}), sql="x")
    scope = _derived_scope()
    materialize_dataset(dc, scope, scope.basket[1])
    conn = connect_duckdb(":memory:")
    loaded = load_into_duck(dc, conn, scope)
    assert "agg" in loaded   # derived view registered for viewers
    assert conn.execute('SELECT COUNT(*) FROM agg').fetchone()[0] == 2


def test_materialize_derived_calculated_single_source():
    # calculated (single-source) derived: a row-level expression over a source.
    dc = _FakeDC()
    write_dataset(dc, "p_mat", "base",
                  pd.DataFrame({"A": [10.0, 20.0], "B": [2.0, 4.0]}), sql="x")
    der = {
        "derivation": {"kind": "calculated", "source_aliases": ["base"],
                       "columns": [{"name": "RATIO", "expr": "A / B"}]},
        "alias": "calc",
        "projection": {"columns": ["RATIO"], "include_all": False},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
    }
    scope = load_scope_from_dict({
        "presentation_id": "p_mat", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{"sql": "SELECT a, b FROM x", "alias": "base",
                    "routing": {"decision": "cached", "estimated_bytes": 1}}, der],
        "filters": {"pinned": [], "interactive": []},
    })
    materialize_dataset(dc, scope, scope.basket[1])  # 'base' parquet reused
    assert dc.get_data_calls == []
    rdf, _ = read_dataset(dc, "p_mat", "calc")
    assert sorted(round(v, 2) for v in rdf["RATIO"]) == [5.0, 5.0]


# ── D3: load_into_duck tazelik kontrolü (parquet blob'u tekrar inmez) ────────

def test_load_into_duck_skips_blob_when_fresh():
    dc = _FakeDC(pd.DataFrame({"A": [1, 2]}))
    scope = _scope()
    write_dataset(dc, "p_mat", "positions",
                  pd.DataFrame({"AS_OF_DATE": ["2025-10-01"], "CCY": ["TRY"],
                                "NET_POSITION": [5]}),
                  sql="SELECT ...")
    conn = connect_duckdb(":memory:")

    reads: list[str] = []
    orig_read = dc.read_bytes
    def counting_read(key):
        reads.append(key)
        return orig_read(key)
    dc.read_bytes = counting_read

    first = load_into_duck(dc, conn, scope)
    assert "positions" in first
    blob_key = dataset_data_key("p_mat", "positions")
    assert reads.count(blob_key) == 1

    second = load_into_duck(dc, conn, scope)
    assert "positions" in second
    # Aynı refreshed_at + tablo oturumda mevcut → parquet blob'u tekrar İNMEZ.
    assert reads.count(blob_key) == 1
    assert conn.execute('SELECT COUNT(*) FROM "positions"').fetchone()[0] == 1

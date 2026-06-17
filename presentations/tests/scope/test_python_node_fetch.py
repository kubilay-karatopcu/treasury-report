"""Faz P-1 — python node'unun build (fetch_cached_tables) ve cron
(materialize_dataset) yollarında çalıştırılması.

- Build pass: cached bir tablonun üstüne python node zincirlenir; DuckDB view'i
  DataFrame'e çekilir, sandbox'ta çalıştırılır, sonuç register edilir.
- Cron pass: materialize_dataset bir python node için upstream zinciri (Oracle →
  python) sırayla yeniden koşar ve yalnız küçük sonucu parquet'e yazar.
"""
from __future__ import annotations

import json

import duckdb
import pandas as pd

from presentations.scope.fetch import fetch_cached_tables
from presentations.scope.materialize import materialize_dataset, read_dataset
from presentations.scope.schema import load_scope_from_dict


class StubDC:
    """Oracle + in-memory S3 (materialize için) çift yüzü."""

    def __init__(self, df):
        self.df = df
        self.objects: dict[str, bytes] = {}

    # Oracle
    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        return self.df.copy()

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


_PYTHON_CODE = (
    "output_node_df = input_node_df.assign("
    "BALANCE_K=input_node_df['BALANCE_TRY'] / 1000)"
)


def _scope(code=_PYTHON_CODE, source="deposits"):
    return load_scope_from_dict({
        "presentation_id": "p_py", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {
                "table_ref": {"schema": "EDW", "name": "DEPOSITS"}, "alias": "deposits",
                "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
            {
                "derivation": {"kind": "python", "source_alias": source, "python_code": code},
                "alias": "deposits_py",
                "projection": {"columns": [], "include_all": True},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
        ],
    })


def test_build_pass_runs_python_node():
    dc = StubDC(pd.DataFrame({"BRANCH_CODE": ["A", "B"], "BALANCE_TRY": [1000, 5000]}))
    conn = duckdb.connect(":memory:")
    loaded = fetch_cached_tables(dc, conn, _scope(), catalog=None)

    assert "deposits_py" in loaded
    assert loaded["deposits_py"]["derived_from"] == "deposits"
    rows = conn.execute(
        "SELECT BRANCH_CODE, BALANCE_K FROM deposits_py ORDER BY BRANCH_CODE"
    ).fetchall()
    assert rows == [("A", 1.0), ("B", 5.0)]


def test_build_pass_python_failure_raises():
    dc = StubDC(pd.DataFrame({"BRANCH_CODE": ["A"], "BALANCE_TRY": [10]}))
    conn = duckdb.connect(":memory:")
    bad = _scope(code="output_node_df = input_node_df['NO_SUCH_COLUMN']")
    try:
        fetch_cached_tables(dc, conn, bad, catalog=None)
        assert False, "kırık python node hata vermeliydi"
    except RuntimeError as exc:
        assert "deposits_py" in str(exc)


def test_cron_materialize_reruns_upstream_chain():
    # Cron yolu: python node'u doğrudan materialize et. Upstream (Oracle deposits)
    # parquet'te yokken in-memory recursive hesaplanmalı; yalnız sonuç persist olur.
    dc = StubDC(pd.DataFrame({"BRANCH_CODE": ["A", "B"], "BALANCE_TRY": [2000, 4000]}))
    scope = _scope()
    item = scope.basket_item("deposits_py")

    meta = materialize_dataset(dc, scope, item, catalog=None)
    assert meta.row_count == 2

    rdf, rmeta = read_dataset(dc, "p_py", "deposits_py")
    assert "BALANCE_K" in rdf.columns
    assert sorted(rdf["BALANCE_K"].tolist()) == [2.0, 4.0]
    # provenance: python_code'un hash'i meta'ya yazılır (sql_hash alanı).
    assert rmeta.sql_hash


def test_cron_materialize_uses_cached_parent_when_present():
    # Upstream parquet'te varsa Oracle'a gidilmeden oradan okunur.
    dc = StubDC(pd.DataFrame({"BRANCH_CODE": ["A"], "BALANCE_TRY": [9000]}))
    scope = _scope()
    # Önce parent'ı (deposits) materialize et → parquet.
    materialize_dataset(dc, scope, scope.basket_item("deposits"), catalog=None)
    # Şimdi Oracle farklı dönse bile python node parquet'teki parent'ı kullanmalı.
    dc.df = pd.DataFrame({"BRANCH_CODE": ["Z"], "BALANCE_TRY": [1]})
    materialize_dataset(dc, scope, scope.basket_item("deposits_py"), catalog=None)
    rdf, _ = read_dataset(dc, "p_py", "deposits_py")
    assert rdf["BRANCH_CODE"].tolist() == ["A"]
    assert rdf["BALANCE_K"].tolist() == [9.0]

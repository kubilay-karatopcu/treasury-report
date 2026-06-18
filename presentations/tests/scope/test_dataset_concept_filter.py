"""End-to-end #4 — concept filtreleri TÜRETİLMİŞ (dataset) node'lara da uygulanır.

Kullanıcı Hazırlık'ta bir kolona concept bağlar (column_concepts). Sunum'da o
concept'e filtre atınca, dataset-bound blok DuckDB'de identity predicate'iyle
filtrelenir — katalog tablolarındaki gibi çalışır.
"""
from __future__ import annotations

import duckdb
import pytest

from presentations.scope.materialize import project_block_from_dataset, _concept_predicates


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE deps_py AS SELECT * FROM (VALUES "
        "('USD', 100), ('EUR', 200), ('TRY', 300), ('USD', 400)) t(\"CCY\", \"AMT\")"
    )
    return c


def test_concept_filter_applied_to_dataset(conn):
    df = project_block_from_dataset(
        conn, {"alias": "deps_py"}, filter_state={},
        concept_filters=[{"concept": "currency", "operator": "in", "values": ["USD"]}],
        column_concepts={"CCY": "currency"},
    )
    assert df is not None
    assert set(df["CCY"]) == {"USD"}
    assert sorted(df["AMT"]) == [100, 400]


def test_no_column_concept_no_filter(conn):
    # Aynı concept filtresi ama kolon bağlı değil → filtre uygulanmaz (tüm satırlar).
    df = project_block_from_dataset(
        conn, {"alias": "deps_py"}, filter_state={},
        concept_filters=[{"concept": "currency", "operator": "in", "values": ["USD"]}],
        column_concepts={},
    )
    assert len(df) == 4


def test_between_concept_on_dataset(conn):
    df = project_block_from_dataset(
        conn, {"alias": "deps_py"}, filter_state={},
        concept_filters=[{"concept": "amount", "operator": "between", "values": [150, 350]}],
        column_concepts={"AMT": "amount"},
    )
    assert sorted(df["AMT"]) == [200, 300]


def test_irrelevant_concept_ignored(conn):
    # column_concepts başka concept'e bağlı → bu filtre bu node'da blind (uygulanmaz).
    df = project_block_from_dataset(
        conn, {"alias": "deps_py"}, filter_state={},
        concept_filters=[{"concept": "segment", "operator": "in", "values": ["RETAIL"]}],
        column_concepts={"CCY": "currency"},
    )
    assert len(df) == 4


def test_concept_predicates_helper_param_binding():
    params = {}
    clauses = _concept_predicates(
        {"CCY": "currency"},
        [{"concept": "currency", "operator": "in", "values": ["USD", "EUR"]}],
        params,
    )
    assert clauses == ['"CCY" IN ($cf0_0, $cf0_1)']
    assert params == {"cf0_0": "USD", "cf0_1": "EUR"}

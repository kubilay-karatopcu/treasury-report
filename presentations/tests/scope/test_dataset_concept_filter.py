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


def test_inject_dataset_concepts_filters_aggregate(conn):
    # An AVG KPI over a produced view, sentinel replaced by a concept predicate —
    # the aggregation (manual-SQL) counterpart of project_block_from_dataset.
    from presentations.scope.materialize import inject_dataset_concepts
    sql = "SELECT ROUND(AVG(AMT), 2) AS value FROM deps_py WHERE {{concept_filters}}"
    inj_sql, params = inject_dataset_concepts(
        sql, {"CCY": "currency"},
        [{"concept": "currency", "operator": "in", "values": ["USD"]}],
    )
    assert "{{concept_filters}}" not in inj_sql
    assert conn.execute(inj_sql, params).fetchone()[0] == 250.0  # AVG(100, 400)


def test_inject_dataset_concepts_no_active_filter_is_noop(conn):
    # No active concept filter → sentinel collapses to 1 = 1 → all rows.
    from presentations.scope.materialize import inject_dataset_concepts
    sql = "SELECT COUNT(*) AS value FROM deps_py WHERE {{concept_filters}}"
    inj_sql, params = inject_dataset_concepts(sql, {"CCY": "currency"}, [])
    assert "1 = 1" in inj_sql and "{{concept_filters}}" not in inj_sql
    assert conn.execute(inj_sql, params).fetchone()[0] == 4


def test_inject_dataset_concepts_unbound_concept_is_noop(conn):
    # Filter concept not bound to any column of this view → no predicate.
    from presentations.scope.materialize import inject_dataset_concepts
    sql = "SELECT COUNT(*) AS value FROM deps_py WHERE {{concept_filters}}"
    inj_sql, params = inject_dataset_concepts(
        sql, {"CCY": "currency"},
        [{"concept": "segment", "operator": "in", "values": ["RETAIL"]}],
    )
    assert "1 = 1" in inj_sql
    assert conn.execute(inj_sql, params).fetchone()[0] == 4


def test_attach_distinct_values(conn):
    # Concept-bound produced-view columns get a DISTINCT sample → the dashboard
    # filter's allowed_values (the view has no table-doc to source them from).
    from presentations.nodes.generate_patch import _attach_distinct_values
    col = {"name": "CCY", "type": "VARCHAR"}
    _attach_distinct_values(conn, "deps_py", col)
    assert col["distinct_values"] == ["EUR", "TRY", "USD"]  # DISTINCT, ORDER BY


def test_attach_distinct_values_caps_high_cardinality(conn):
    from presentations.nodes.generate_patch import _attach_distinct_values
    col = {"name": "AMT", "type": "INTEGER"}
    # cap=2 but there are 4 distinct amounts → leave allowed_values unset.
    _attach_distinct_values(conn, "deps_py", col, cap=2)
    assert "distinct_values" not in col


def test_concept_predicates_helper_param_binding():
    params = {}
    clauses = _concept_predicates(
        {"CCY": "currency"},
        [{"concept": "currency", "operator": "in", "values": ["USD", "EUR"]}],
        params,
    )
    assert clauses == ['"CCY" IN ($cf0_0, $cf0_1)']
    assert params == {"cf0_0": "USD", "cf0_1": "EUR"}

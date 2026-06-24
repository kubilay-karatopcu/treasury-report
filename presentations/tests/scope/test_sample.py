"""Oturum 1 (A5) — design-time sample composer + fidelity ledger.

compose_sample_sql wraps the raw-table fetch in Oracle SAMPLE(pct) + a row
ceiling, reusing compose_cached_sql's projection/pushdown; manual-SQL items fall
back to a top-N cap. The fidelity ledger records sample-vs-full per alias in the
session DuckDB, separate from __dataset_meta.
"""
from __future__ import annotations

import datetime

import duckdb

from presentations.scope.fetch import compose_cached_sql
from presentations.scope.materialize import (
    _ensure_state_table, dataset_fidelity, record_fidelity,
)
from presentations.scope.sample import (
    DEFAULT_SAMPLE_CEILING_ROWS, DEFAULT_SAMPLE_PCT, compose_sample_sql,
    sample_fingerprint,
)
from presentations.scope.schema import load_scope_from_dict


def _scope(basket, pinned=None):
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": basket, "filters": {"pinned": pinned or [], "interactive": []},
    })


def _raw_item(cols=("AS_OF_DATE", "CCY")):
    return {
        "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
        "alias": "positions",
        "projection": {"columns": list(cols), "include_all": False},
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }


# ── compose_cached_sql sample_pct (the seam) ─────────────────────────────────

def test_compose_cached_sql_sample_pct_position():
    # SAMPLE sits on the table, before WHERE; cap comes last.
    scope = _scope(
        [_raw_item(["AS_OF_DATE", "CCY"])],
        pinned=[{"id": "pf_q4", "concept": "as_of_time", "op": "between",
                 "from": "2025-10-01", "to": "2025-12-31", "applies_to": ["positions"]}],
    )
    from presentations.tests.scope.test_fetch import _catalog
    sql, binds = compose_cached_sql(
        scope, scope.basket[0], _catalog(), max_rows=200_000, sample_pct=10)
    assert sql == (
        "SELECT AS_OF_DATE, CCY FROM ODS_TREASURY.TRD_BRANCH_POSITION SAMPLE(10) "
        "WHERE AS_OF_DATE BETWEEN :positions_from AND :positions_to "
        "FETCH FIRST 200000 ROWS ONLY"
    )
    # Binds stay parameterised — values never concatenated into SQL.
    assert binds == {"positions_from": datetime.date(2025, 10, 1),
                     "positions_to": datetime.date(2025, 12, 31)}


def test_compose_cached_sql_no_sample_by_default():
    scope = _scope([_raw_item()])
    sql, _ = compose_cached_sql(scope, scope.basket[0])
    assert "SAMPLE(" not in sql


# ── compose_sample_sql ───────────────────────────────────────────────────────

def test_sample_raw_table_uses_oracle_sample_and_ceiling():
    scope = _scope([_raw_item()])
    sql, binds, fp = compose_sample_sql(scope, scope.basket[0])
    assert f"SAMPLE({DEFAULT_SAMPLE_PCT})" in sql
    assert sql.endswith(f"FETCH FIRST {DEFAULT_SAMPLE_CEILING_ROWS} ROWS ONLY")
    assert binds == {}
    assert len(fp) == 16


def test_sample_overrides_fraction_and_ceiling():
    scope = _scope([_raw_item()])
    sql, _, _ = compose_sample_sql(scope, scope.basket[0], fraction=5, ceiling_rows=1000)
    assert "SAMPLE(5)" in sql
    assert sql.endswith("FETCH FIRST 1000 ROWS ONLY")


def test_sample_sql_item_falls_back_to_top_n_no_sample():
    scope = _scope([{
        "alias": "manual",
        "sql": "SELECT a, b FROM ODS.T",
        "routing": {"decision": "cached", "estimated_bytes": 0},
    }])
    sql, binds, _ = compose_sample_sql(scope, scope.basket[0], ceiling_rows=500)
    assert "SAMPLE(" not in sql
    assert sql.endswith("FETCH FIRST 500 ROWS ONLY")
    assert "SELECT a, b FROM ODS.T" in sql
    assert binds == {}


def test_sample_derived_node_raises():
    scope = _scope([
        _raw_item(),
        {
            "alias": "agg",
            "derivation": {"kind": "aggregate", "source_alias": "positions",
                           "group_by": ["CCY"],
                           "measures": [{"column": "CCY", "fn": "count", "as": "N"}]},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        },
    ])
    import pytest
    with pytest.raises(ValueError):
        compose_sample_sql(scope, scope.basket_item("agg"))


def test_fingerprint_changes_when_filter_changes():
    base = _scope([_raw_item()])
    filtered = _scope(
        [_raw_item()],
        pinned=[{"id": "pf_q4", "concept": "as_of_time", "op": "between",
                 "from": "2025-10-01", "to": "2025-12-31", "applies_to": ["positions"]}],
    )
    from presentations.tests.scope.test_fetch import _catalog
    _, _, fp_base = compose_sample_sql(base, base.basket[0])
    _, _, fp_filtered = compose_sample_sql(filtered, filtered.basket[0], _catalog())
    assert fp_base != fp_filtered


def test_sample_fingerprint_includes_binds():
    assert sample_fingerprint("SELECT 1", {"a": 1}) != sample_fingerprint("SELECT 1", {"a": 2})
    assert sample_fingerprint("SELECT 1", {"a": 1}) == sample_fingerprint("SELECT 1", {"a": 1})


# ── fidelity ledger ───────────────────────────────────────────────────────────

def test_fidelity_round_trip():
    conn = duckdb.connect(":memory:")
    assert dataset_fidelity(conn, "positions") is None
    record_fidelity(conn, "positions", "sample", fingerprint="abc123", row_count=1234)
    got = dataset_fidelity(conn, "positions")
    assert got["fidelity"] == "sample"
    assert got["fingerprint"] == "abc123"
    assert got["row_count"] == 1234
    assert got["refreshed_at"]
    # Overwrite to full — same alias, INSERT OR REPLACE.
    record_fidelity(conn, "positions", "full", fingerprint=None, row_count=99999)
    got = dataset_fidelity(conn, "positions")
    assert got["fidelity"] == "full"
    assert got["fingerprint"] is None
    assert got["row_count"] == 99999


def test_fidelity_table_coexists_with_dataset_meta():
    # The fidelity ledger must not disturb __dataset_meta's positional inserts.
    conn = duckdb.connect(":memory:")
    _ensure_state_table(conn)
    conn.execute("INSERT OR REPLACE INTO __dataset_meta VALUES (?, ?)",
                 ["positions", "2026-06-24T00:00:00"])
    record_fidelity(conn, "positions", "sample", row_count=10)
    # Both tables independently readable.
    assert conn.execute(
        "SELECT refreshed_at FROM __dataset_meta WHERE alias='positions'"
    ).fetchone()[0] == "2026-06-24T00:00:00"
    assert dataset_fidelity(conn, "positions")["fidelity"] == "sample"

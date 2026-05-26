"""Polish-5 tests — calculated derivation kind end-to-end.

Covers:
  - Schema validators (kind=aggregate vs kind=calculated mutually exclusive
    fields; multi-source requires join_keys; output column names unique).
  - compile_calculated_sql for single + multi-source.
  - _apply_create_calculation mutator (alias clash, missing source, multi
    without join_keys).
  - DuckDB end-to-end roundtrip (compile → execute → verify rows).
"""
from __future__ import annotations

import pytest
import duckdb

from presentations.scope.schema import (
    BasketItem,
    CalculatedColumn,
    CalculatedJoinKey,
    Derivation,
    Measure,
    Projection,
    Routing,
)
from presentations.scope.fetch import (
    compile_aggregate_sql,
    compile_calculated_sql,
)
from presentations.routes_scope import (
    _ApplyError,
    _mutate_scope_with_suggestion,
)


# ── Schema validators ───────────────────────────────────────────────────────

class TestDerivationSchema:

    def test_aggregate_kind_still_valid(self):
        d = Derivation(
            kind="aggregate",
            source_alias="positions",
            group_by=["BRANCH_CODE"],
            measures=[Measure(column="BALANCE_TRY", fn="sum", **{"as": "SUM_BAL"})],
        )
        assert d.kind == "aggregate"
        assert d.measures[0].as_ == "SUM_BAL"

    def test_calculated_single_source_no_join_required(self):
        d = Derivation(
            kind="calculated",
            source_aliases=["positions"],
            columns=[CalculatedColumn(name="RATE_X2", expr="INTEREST_RATE * 2")],
        )
        assert d.kind == "calculated"
        assert d.source_aliases == ["positions"]
        assert d.join_keys == []

    def test_calculated_multi_source_requires_joins(self):
        with pytest.raises(ValueError, match="join_keys gerekli"):
            Derivation(
                kind="calculated",
                source_aliases=["positions", "branch_dim"],
                columns=[CalculatedColumn(name="X", expr="1")],
            )

    def test_calculated_join_alias_must_be_in_sources(self):
        with pytest.raises(ValueError, match="ghost.*source_aliases"):
            Derivation(
                kind="calculated",
                source_aliases=["positions", "branch_dim"],
                join_keys=[CalculatedJoinKey(
                    left_alias="ghost", left_column="A",
                    right_alias="branch_dim", right_column="B",
                )],
                columns=[CalculatedColumn(name="X", expr="1")],
            )

    def test_calculated_rejects_aggregate_fields(self):
        """source_alias / group_by / measures are aggregate-only."""
        with pytest.raises(ValueError, match="aggregate"):
            Derivation(
                kind="calculated",
                source_aliases=["positions"],
                source_alias="positions",
                columns=[CalculatedColumn(name="X", expr="1")],
            )

    def test_aggregate_rejects_calculated_fields(self):
        with pytest.raises(ValueError, match="calculated"):
            Derivation(
                kind="aggregate",
                source_alias="positions",
                source_aliases=["positions"],
                group_by=["X"],
            )

    def test_calculated_unique_output_columns(self):
        with pytest.raises(ValueError, match="iki kez tanımlı"):
            Derivation(
                kind="calculated",
                source_aliases=["positions"],
                columns=[
                    CalculatedColumn(name="DUP", expr="1"),
                    CalculatedColumn(name="DUP", expr="2"),
                ],
            )

    def test_calculated_requires_columns(self):
        with pytest.raises(ValueError, match="output column"):
            Derivation(
                kind="calculated",
                source_aliases=["positions"],
                columns=[],
            )


# ── SQL compiler ────────────────────────────────────────────────────────────

def _basket_item(alias: str, derivation: Derivation) -> BasketItem:
    return BasketItem(
        alias=alias,
        derivation=derivation,
        projection=Projection(columns=[c.name for c in derivation.columns],
                              include_all=False),
        routing=Routing(decision="cached", estimated_bytes=0),
    )


class TestCompileCalculatedSql:

    def test_single_source(self):
        item = _basket_item("ratio", Derivation(
            kind="calculated",
            source_aliases=["positions"],
            columns=[CalculatedColumn(name="X", expr="BALANCE_TRY / 100")],
        ))
        sql = compile_calculated_sql(item)
        assert sql == 'SELECT BALANCE_TRY / 100 AS "X" FROM positions'

    def test_multi_source_join(self):
        item = _basket_item("gap", Derivation(
            kind="calculated",
            source_aliases=["positions", "competitors"],
            join_keys=[CalculatedJoinKey(
                left_alias="positions", left_column="BRANCH_CODE",
                right_alias="competitors", right_column="BRANCH_CODE",
            )],
            columns=[CalculatedColumn(
                name="DIFF",
                expr="positions.INTEREST_RATE - competitors.RATE",
            )],
        ))
        sql = compile_calculated_sql(item)
        assert "INNER JOIN" in sql
        assert "positions" in sql
        assert "competitors" in sql
        assert "positions\".\"BRANCH_CODE\" = \"competitors\".\"BRANCH_CODE\"" in sql.replace('"', '"')

    def test_duckdb_end_to_end(self):
        """Compile + execute on an in-memory DuckDB so the emitted SQL is
        actually runnable. Mirrors how fetch_cached_tables's Pass 2 calls
        compile + conn.execute()."""
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE positions AS "
            "SELECT 'B01' AS BRANCH_CODE, 0.10 AS INTEREST_RATE "
            "UNION ALL SELECT 'B02', 0.12 "
            "UNION ALL SELECT 'B03', 0.15"
        )
        conn.execute(
            "CREATE TABLE competitors AS "
            "SELECT 'B01' AS BRANCH_CODE, 0.09 AS RATE "
            "UNION ALL SELECT 'B02', 0.11 "
            "UNION ALL SELECT 'B03', 0.14"
        )
        item = _basket_item("gap", Derivation(
            kind="calculated",
            source_aliases=["positions", "competitors"],
            join_keys=[CalculatedJoinKey(
                left_alias="positions", left_column="BRANCH_CODE",
                right_alias="competitors", right_column="BRANCH_CODE",
            )],
            columns=[CalculatedColumn(
                name="GAP",
                expr="positions.INTEREST_RATE - competitors.RATE",
            )],
        ))
        df = conn.execute(compile_calculated_sql(item)).fetchdf()
        assert len(df) == 3
        # Floating-point tolerance: 0.01 expected for every row.
        assert all(abs(v - 0.01) < 1e-6 for v in df["GAP"])


# ── Apply mutator ──────────────────────────────────────────────────────────

@pytest.fixture
def two_alias_scope() -> dict:
    return {
        "presentation_id": "p", "version": 1, "created_by": "A",
        "created_at": "2026-05-24T00:00:00Z",
        "basket": [
            {"alias": "positions",
             "table_ref": {"schema": "EDW", "name": "POSITIONS"},
             "projection": {"columns": ["BRANCH_CODE", "INTEREST_RATE"],
                            "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system",
                         "estimated_bytes": 0}},
            {"alias": "competitors",
             "table_ref": {"schema": "EDW", "name": "COMPETITORS"},
             "projection": {"columns": ["BRANCH_CODE", "RATE"],
                            "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system",
                         "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    }


class TestApplyCreateCalculation:

    def test_applies_with_multi_source_join(self, two_alias_scope):
        out = _mutate_scope_with_suggestion(two_alias_scope, {
            "kind": "create_calculation",
            "new_alias": "rate_gap",
            "source_aliases": ["positions", "competitors"],
            "join_keys": [{
                "left_alias": "positions", "left_column": "BRANCH_CODE",
                "right_alias": "competitors", "right_column": "BRANCH_CODE",
            }],
            "columns": [
                {"name": "GAP", "expr": "positions.INTEREST_RATE - competitors.RATE"},
            ],
        })
        new = out["basket"][-1]
        assert new["alias"] == "rate_gap"
        assert new["derivation"]["kind"] == "calculated"
        assert new["derivation"]["source_aliases"] == ["positions", "competitors"]
        assert new["derivation"]["columns"][0]["name"] == "GAP"
        assert new["projection"]["columns"] == ["GAP"]

    def test_alias_clash_rejected(self, two_alias_scope):
        with pytest.raises(_ApplyError, match="zaten mevcut"):
            _mutate_scope_with_suggestion(two_alias_scope, {
                "kind": "create_calculation",
                "new_alias": "positions",  # already in basket
                "source_aliases": ["positions"],
                "columns": [{"name": "X", "expr": "1"}],
            })

    def test_missing_source_alias_rejected(self, two_alias_scope):
        with pytest.raises(_ApplyError, match="basket'te yok"):
            _mutate_scope_with_suggestion(two_alias_scope, {
                "kind": "create_calculation",
                "new_alias": "ok",
                "source_aliases": ["ghost"],
                "columns": [{"name": "X", "expr": "1"}],
            })

    def test_multi_source_without_joins_rejected(self, two_alias_scope):
        with pytest.raises(_ApplyError, match="join_keys gerekli"):
            _mutate_scope_with_suggestion(two_alias_scope, {
                "kind": "create_calculation",
                "new_alias": "ok",
                "source_aliases": ["positions", "competitors"],
                "columns": [{"name": "X", "expr": "1"}],
            })

    def test_single_source_no_joins_ok(self, two_alias_scope):
        """Single-source calculated doesn't need join_keys."""
        out = _mutate_scope_with_suggestion(two_alias_scope, {
            "kind": "create_calculation",
            "new_alias": "rate_x2",
            "source_aliases": ["positions"],
            "columns": [{"name": "RATE_X2", "expr": "INTEREST_RATE * 2"}],
        })
        assert any(b.get("alias") == "rate_x2" for b in out["basket"])

    def test_empty_columns_rejected(self, two_alias_scope):
        with pytest.raises(_ApplyError, match="output column"):
            _mutate_scope_with_suggestion(two_alias_scope, {
                "kind": "create_calculation",
                "new_alias": "ok",
                "source_aliases": ["positions"],
                "columns": [],
            })

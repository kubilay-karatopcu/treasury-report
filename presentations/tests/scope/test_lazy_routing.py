"""Phase 8.d tests: lazy/cache routing, pinned filter pushdown, on-demand load.

Acceptance §10.d:
  - Lazy table query rewrite produces correct SQL targeting Oracle for a block
    referencing a lazy alias.
  - Pinned filter values are pushed into Oracle WHERE for cached fetches and
    into block query WHERE for lazy executions.
  - A block referencing both a cached and a lazy alias works.
  - Routing badge UI reflects current decision and decided_by.
  - Override link works for valid override; refuses for hard-ceiling violations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd
import pytest

from presentations.scope.binding import (
    ensure_lazy_alias_loaded,
    invalidate_lazy_cache,
)
from presentations.scope.catalog import ColumnMeta, DictCatalog, TableMeta
from presentations.scope.fetch import compose_cached_sql
from presentations.scope.routing import (
    DEFAULT_HARD_CEILING_BYTES,
    DEFAULT_THRESHOLD_BYTES,
    RoutingCeilingError,
    apply_user_override,
    decide_routing,
)
from presentations.scope.schema import (
    PinnedFilter,
    Projection,
    TableRef,
    load_scope_from_dict,
)


# ── Routing decision ────────────────────────────────────────────────────────

@pytest.fixture
def huge_table_catalog():
    """A catalog with one 12.4M-row/day table (DEPOSITS_DAILY) partitioned on
    DATE. Mirrors the DEV catalog's DEPOSITS_DAILY hint."""
    return DictCatalog(
        tables={
            "DEPOSITS_DAILY": TableMeta(
                schema_name="EDW", name="DEPOSITS_DAILY",
                partition_column="DATE",
                estimated_daily_rows=12_400_000,
                columns={
                    "DATE": ColumnMeta(type="DATE", concept="as_of_time"),
                    "BRANCH_CODE": ColumnMeta(type="VARCHAR2(10)", concept="branch"),
                    "SEGMENT": ColumnMeta(type="VARCHAR2(20)", concept="segment"),
                    "BALANCE_TRY": ColumnMeta(type="NUMBER", concept=None),
                },
            ),
            "BRANCH_DIM": TableMeta(
                schema_name="EDW", name="BRANCH_DIM",
                estimated_total_rows=2000,
                columns={
                    "BRANCH_CODE": ColumnMeta(type="VARCHAR2(10)", concept="branch"),
                    "BRANCH_NAME": ColumnMeta(type="VARCHAR2(100)"),
                },
            ),
        },
        concepts={},
    )


class TestRoutingDecision:
    def test_huge_table_goes_lazy_without_filter(self, huge_table_catalog):
        d = decide_routing(
            TableRef(schema="EDW", name="DEPOSITS_DAILY"),
            Projection(columns=["DATE", "BRANCH_CODE", "BALANCE_TRY"]),
            [], catalog=huge_table_catalog,
        )
        assert d.decision == "lazy"
        assert d.decided_by == "system"
        assert d.estimated_bytes > DEFAULT_THRESHOLD_BYTES

    def test_q4_filter_shrinks_but_still_lazy(self, huge_table_catalog):
        q4 = PinnedFilter(
            id="pf_q4", concept="as_of_time", op="between",
            **{"from": "2025-10-01"}, to="2025-12-31",
        )
        d = decide_routing(
            TableRef(schema="EDW", name="DEPOSITS_DAILY"),
            Projection(columns=["DATE", "BRANCH_CODE", "BALANCE_TRY"]),
            [q4], catalog=huge_table_catalog,
        )
        # Q4 is ~92 days × 12.4M rows × ~32 bytes/row ≈ 36 GB — well above 500 MB.
        assert d.decision == "lazy"
        assert d.estimated_bytes > DEFAULT_THRESHOLD_BYTES

    def test_tiny_partition_window_goes_cached(self, huge_table_catalog):
        # ~3 days × 12.4M × 32 bytes ≈ 1.2 GB → still lazy at default threshold
        # but cached at a relaxed one. Confirm threshold actually gates.
        w = PinnedFilter(
            id="pf_w", concept="as_of_time", op="between",
            **{"from": "2025-12-29"}, to="2025-12-31",
        )
        # 3 days × 12.4M × ~32 bytes ≈ 1.2 GB
        d_default = decide_routing(
            TableRef(schema="EDW", name="DEPOSITS_DAILY"),
            Projection(columns=["DATE", "BRANCH_CODE", "BALANCE_TRY"]),
            [w], catalog=huge_table_catalog,
        )
        assert d_default.decision == "lazy"
        # Now relax the threshold to 5 GB.
        d_relaxed = decide_routing(
            TableRef(schema="EDW", name="DEPOSITS_DAILY"),
            Projection(columns=["DATE", "BRANCH_CODE", "BALANCE_TRY"]),
            [w], catalog=huge_table_catalog,
            threshold_bytes=5_000_000_000,
        )
        assert d_relaxed.decision == "cached"

    def test_small_table_goes_cached(self, huge_table_catalog):
        d = decide_routing(
            TableRef(schema="EDW", name="BRANCH_DIM"),
            Projection(columns=["BRANCH_CODE", "BRANCH_NAME"]),
            [], catalog=huge_table_catalog,
        )
        assert d.decision == "cached"


class TestOverride:
    def test_force_lazy_always_works(self, huge_table_catalog):
        d = decide_routing(
            TableRef(schema="EDW", name="BRANCH_DIM"),
            Projection(columns=["BRANCH_CODE"]),
            [], catalog=huge_table_catalog,
        )
        o = apply_user_override(d, "lazy")
        assert o.decision == "lazy"
        assert o.decided_by == "user"
        # Original estimate preserved (audit).
        assert o.estimated_bytes == d.estimated_bytes

    def test_force_cached_refused_when_over_hard_ceiling(self, huge_table_catalog):
        d = decide_routing(
            TableRef(schema="EDW", name="DEPOSITS_DAILY"),
            Projection(columns=["DATE", "BRANCH_CODE", "BALANCE_TRY"]),
            [], catalog=huge_table_catalog,
        )
        assert d.estimated_bytes > DEFAULT_HARD_CEILING_BYTES
        with pytest.raises(RoutingCeilingError) as exc:
            apply_user_override(d, "cached", hard_ceiling_bytes=DEFAULT_HARD_CEILING_BYTES)
        assert exc.value.estimated_bytes == d.estimated_bytes
        assert exc.value.hard_ceiling_bytes == DEFAULT_HARD_CEILING_BYTES

    def test_force_cached_allowed_when_under_hard_ceiling(self, huge_table_catalog):
        d = decide_routing(
            TableRef(schema="EDW", name="BRANCH_DIM"),
            Projection(columns=["BRANCH_CODE", "BRANCH_NAME"]),
            [], catalog=huge_table_catalog,
        )
        # Small table — force cached succeeds trivially.
        o = apply_user_override(d, "cached")
        assert o.decision == "cached"
        assert o.decided_by == "user"


# ── Pinned filter pushdown ─────────────────────────────────────────────────

@dataclass
class _IdentityTransform:
    kind: str = "identity"


@dataclass
class FakeBinding:
    column: str
    transform: _IdentityTransform = None

    def __post_init__(self):
        if self.transform is None:
            self.transform = _IdentityTransform()


class FakeBindingCatalog:
    """Tiny stand-in for the Phase 7 ``BindingCatalog`` — produces an identity
    binding for every (schema, table, concept) → column it knows about, and
    ``None`` otherwise (concept-blind for that table)."""

    def __init__(self, mapping):
        # mapping: {(schema, table, concept): column_name}
        self._mapping = mapping

    def get_binding(self, schema, table, concept):
        col = self._mapping.get((schema, table, concept))
        return FakeBinding(column=col) if col else None


class FakeRegistry:
    """Registry stub: passes every value through unchanged (no canonical
    rewrite) — matches the Phase 6.5 "open" registry behaviour."""

    def resolve_value(self, concept_id, value):
        return value

    def get(self, concept_id):
        return None

    def get_concept(self, concept_id):
        return None


@pytest.fixture
def scope_with_pinned_filters():
    return load_scope_from_dict({
        "scope": {
            "presentation_id": "p_test",
            "version": 1,
            "created_by": "A16438",
            "created_at": "2025-01-01T00:00:00Z",
            "basket": [{
                "alias": "deposits_daily",
                "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
                "projection": {"columns": ["DATE", "BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000},
            }],
            "filters": {
                "pinned": [
                    {"id": "pf_q4", "concept": "as_of_time", "op": "between",
                     "from": "2025-10-01", "to": "2025-12-31", "applies_to": []},
                    {"id": "pf_seg", "concept": "segment", "op": "in",
                     "values": ["RETAIL", "SME"], "applies_to": ["deposits_daily"]},
                ],
                "interactive": [],
                "raw": [],
            },
            "joins": [],
        }
    })


class TestPinnedPushdown:
    def test_partition_date_filter_is_pushed(self, scope_with_pinned_filters, huge_table_catalog):
        item = scope_with_pinned_filters.basket[0]
        sql, binds = compose_cached_sql(
            scope_with_pinned_filters, item, huge_table_catalog,
        )
        # Partition pushdown emits `DATE BETWEEN :alias_from AND :alias_to`.
        assert re.search(r"DATE BETWEEN :deposits_daily_from AND :deposits_daily_to", sql)
        assert binds.get("deposits_daily_from") is not None
        assert binds.get("deposits_daily_to") is not None

    def test_concept_pushdown_adds_segment_clause(self, scope_with_pinned_filters, huge_table_catalog):
        # Without a concept compiler binding, segment filter stays blind (not
        # pushed). With a binding it lands in the SQL as `SEGMENT IN (:bind, ...)`.
        item = scope_with_pinned_filters.basket[0]
        registry = FakeRegistry()
        bc = FakeBindingCatalog({
            ("EDW", "DEPOSITS_DAILY", "segment"): "SEGMENT",
        })
        sql, binds = compose_cached_sql(
            scope_with_pinned_filters, item, huge_table_catalog,
            concept_registry=registry, binding_catalog=bc,
        )
        # The Phase 7 compiler emits the predicate; we just verify it landed.
        assert "SEGMENT" in sql
        # Some bind values should now carry the canonical segment codes.
        segment_binds = {k: v for k, v in binds.items() if v in ("RETAIL", "SME")}
        assert len(segment_binds) == 2

    def test_blind_filter_silently_dropped(self, scope_with_pinned_filters, huge_table_catalog):
        # No binding catalog → the concept compiler treats every filter as
        # blind → partition still pushed but segment is not.
        item = scope_with_pinned_filters.basket[0]
        sql, _ = compose_cached_sql(
            scope_with_pinned_filters, item, huge_table_catalog,
        )
        assert "SEGMENT" not in sql.upper().split("WHERE", 1)[-1].split("FROM")[0]


# ── Lazy alias on-demand load ──────────────────────────────────────────────

class StubDataClient:
    """Records each get_data() call and returns a DataFrame for testing."""

    def __init__(self, df_factory):
        self.df_factory = df_factory
        self.calls: list[dict] = []

    def get_data(self, **kwargs):
        self.calls.append(kwargs)
        return self.df_factory()


@pytest.fixture
def lazy_scope():
    return load_scope_from_dict({
        "scope": {
            "presentation_id": "p_test",
            "version": 1,
            "created_by": "A16438",
            "created_at": "2025-01-01T00:00:00Z",
            "basket": [
                {
                    "alias": "positions",
                    "table_ref": {"schema": "EDW", "name": "POSITIONS"},
                    "projection": {"columns": ["DATE", "BRANCH_CODE", "AMOUNT"], "include_all": False},
                    "routing": {"decision": "lazy", "decided_by": "system", "estimated_bytes": 9_000_000_000},
                },
                {
                    "alias": "branch_dim",
                    "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
                    "projection": {"columns": ["BRANCH_CODE", "BRANCH_NAME"], "include_all": False},
                    "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100_000},
                },
            ],
            "filters": {"pinned": [], "interactive": [], "raw": []},
            "joins": [],
        }
    })


class TestLazyLoad:
    def test_lazy_alias_pulls_and_registers_view(self, lazy_scope):
        dc = StubDataClient(lambda: pd.DataFrame({"DATE": ["2025-01-01"], "BRANCH_CODE": ["B01"], "AMOUNT": [100.0]}))
        conn = duckdb.connect(":memory:")
        loaded = ensure_lazy_alias_loaded(lazy_scope, conn, dc, "positions")
        assert loaded is True
        # View registered under the alias.
        rows = conn.execute("SELECT * FROM positions").fetchall()
        assert rows == [("2025-01-01", "B01", 100.0)]
        # Subsequent call is a no-op (cached on connection).
        assert ensure_lazy_alias_loaded(lazy_scope, conn, dc, "positions") is False
        assert len(dc.calls) == 1

    def test_cached_alias_is_not_loaded_by_lazy_helper(self, lazy_scope):
        dc = StubDataClient(lambda: pd.DataFrame({"X": [1]}))
        conn = duckdb.connect(":memory:")
        # branch_dim is cached → helper bails fast.
        assert ensure_lazy_alias_loaded(lazy_scope, conn, dc, "branch_dim") is False
        assert dc.calls == []

    def test_unknown_alias_bails(self, lazy_scope):
        dc = StubDataClient(lambda: pd.DataFrame({"X": [1]}))
        conn = duckdb.connect(":memory:")
        assert ensure_lazy_alias_loaded(lazy_scope, conn, dc, "nobody") is False
        assert dc.calls == []

    def test_invalidate_clears_cache_and_drops_view(self, lazy_scope):
        dc = StubDataClient(lambda: pd.DataFrame({"DATE": ["2025-01-01"], "BRANCH_CODE": ["B01"], "AMOUNT": [100.0]}))
        conn = duckdb.connect(":memory:")
        ensure_lazy_alias_loaded(lazy_scope, conn, dc, "positions")
        invalidate_lazy_cache(conn, ["positions"])
        # The view is gone — next reference re-fetches.
        with pytest.raises(Exception):
            conn.execute("SELECT * FROM positions").fetchall()
        ensure_lazy_alias_loaded(lazy_scope, conn, dc, "positions")
        assert len(dc.calls) == 2

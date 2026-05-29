"""Tests for presentations.cache.block_cache — Phase 6.5.c §10.c.

Coverage targets:
- Cache key stability (sorted, normalised).
- Subset routing per type (date, date_range, enum_multi, enum_single, number_range).
- LRU eviction at the soft cap.
- DuckDB roundtrip (write → find_exact → derive_from_parent).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from presentations.blocks.schema import Block, Variable
from presentations.cache.block_cache import (
    BlockCache,
    BlockCacheKey,
    cache_key,
    is_subset,
    is_subset_safe,
)


@pytest.fixture
def conn():
    return duckdb.connect(":memory:")


@pytest.fixture
def block():
    return Block(
        id="branch_position_kpi",
        version=1,
        title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query="SELECT 1",
        visualization={"type": "kpi", "config": {}},
        variables=[
            Variable(name="as_of_from", semantic_tag="as_of_time",
                     type="date", required=True, default="today - 30d"),
            Variable(name="as_of_to", semantic_tag="as_of_time",
                     type="date", required=True, default="today"),
            Variable(name="currency_list", semantic_tag="currency",
                     type="enum_multi", required=True,
                     allowed_values=["TRY", "USD", "EUR", "GBP"],
                     default=["TRY", "USD", "EUR"]),
        ],
    )


# ── cache_key ─────────────────────────────────────────────────────────────

class TestCacheKey:
    def test_same_inputs_same_digest(self):
        a = cache_key("blk", 1, {"x": [1, 2, 3], "y": "today"})
        b = cache_key("blk", 1, {"x": [1, 2, 3], "y": "today"})
        assert a.digest == b.digest

    def test_enum_order_normalised(self):
        # enum_multi values get sorted in normalisation; reordered input
        # must yield the same key.
        a = cache_key("blk", 1, {"x": ["A", "B", "C"]})
        b = cache_key("blk", 1, {"x": ["C", "A", "B"]})
        assert a.digest == b.digest

    def test_date_iso_normalised(self):
        # date objects and their ISO string representations resolve to the
        # same digest because normalize_for_cache_key turns date → ISO.
        from datetime import date
        a = cache_key("blk", 1, {"d": date(2026, 5, 21)})
        b = cache_key("blk", 1, {"d": "2026-05-21"})
        assert a.digest == b.digest

    def test_block_id_differentiates(self):
        a = cache_key("blk_a", 1, {"x": 1})
        b = cache_key("blk_b", 1, {"x": 1})
        assert a.digest != b.digest

    def test_version_differentiates(self):
        a = cache_key("blk", 1, {"x": 1})
        b = cache_key("blk", 2, {"x": 1})
        assert a.digest != b.digest

    def test_sql_differentiates(self):
        # #20: same id/version/vars but different SQL must not collide, else a
        # hit would serve the other query's stale rows.
        a = cache_key("blk", 1, {"x": 1}, "SELECT a FROM t")
        b = cache_key("blk", 1, {"x": 1}, "SELECT b FROM t")
        assert a.digest != b.digest


# ── Subset-safety gate (#11) ────────────────────────────────────────────────

class TestSubsetSafety:
    def test_pure_projection_is_safe(self):
        assert is_subset_safe(
            "SELECT branch, currency, bal FROM t WHERE currency IN (:c)")

    @pytest.mark.parametrize("sql", [
        "SELECT branch, SUM(bal) AS v FROM t WHERE ccy IN (:c) GROUP BY branch",
        "SELECT COUNT(*) FROM t",
        "SELECT branch, SUM(bal) OVER (PARTITION BY branch) FROM t",
        "SELECT DISTINCT branch FROM t",
        "SELECT a FROM t UNION ALL SELECT a FROM u",
        "SELECT a FROM t WHERE x = :v GROUP BY a HAVING COUNT(*) > 1",
        "SELECT a FROM t LIMIT 100",
        "SELECT a FROM t FETCH FIRST 10 ROWS ONLY",
        "SELECT a FROM t WHERE ROWNUM <= 50",
    ])
    def test_non_projection_is_unsafe(self, sql):
        assert not is_subset_safe(sql)

    def test_keyword_inside_string_literal_not_tripped(self):
        # 'GROUP BY' inside a string literal must not flag the query unsafe.
        assert is_subset_safe(
            "SELECT note FROM t WHERE note = 'has GROUP BY text' AND ccy IN (:c)")

    def test_aggregated_block_never_subset_routes(self, conn):
        # #11: a GROUP BY block whose narrower request is a subset BY VARS must
        # still refuse subset routing (row-filtering aggregated rows = wrong).
        agg_block = Block(
            id="agg_kpi", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query=("SELECT branch, SUM(bal) AS v FROM t "
                   "WHERE currency IN (:currency_list) GROUP BY branch"),
            visualization={"type": "bar_chart", "config": {}},
            variables=[Variable(name="currency_list", semantic_tag="currency",
                                type="enum_multi", required=True,
                                allowed_values=["TRY", "USD", "EUR"],
                                default=["TRY", "USD", "EUR"])],
        )
        cache = BlockCache(conn)
        cache.write(agg_block, {"currency_list": ["TRY", "USD", "EUR"]},
                    pd.DataFrame({"branch": ["A", "B"], "v": [10, 20]}))
        assert cache.find_subset_parent(
            agg_block, {"currency_list": ["TRY"]}) is None


# ── is_subset per type ────────────────────────────────────────────────────

class TestIsSubset:
    def test_date_equal_is_subset(self, block):
        a = {"as_of_from": "2026-05-01"}
        b = {"as_of_from": "2026-05-01"}
        assert is_subset(a, b, [block.variables[0]])

    def test_date_unequal_not_subset(self, block):
        a = {"as_of_from": "2026-05-01"}
        b = {"as_of_from": "2026-04-01"}
        assert not is_subset(a, b, [block.variables[0]])

    def test_date_range_narrower_is_subset(self, block):
        # Build a block with a date_range variable.
        from presentations.blocks.schema import Variable
        v = Variable(name="period", semantic_tag="as_of_time", type="date_range",
                     required=True,
                     default={"from": "today - 7d", "to": "today"})
        narrower = {"period": {"from": "2026-04-10", "to": "2026-04-20"}}
        parent   = {"period": {"from": "2026-04-01", "to": "2026-04-30"}}
        assert is_subset(narrower, parent, [v])
        assert not is_subset(parent, narrower, [v])

    def test_enum_multi_subset(self, block):
        narrower = {"currency_list": ["TRY", "USD"]}
        parent   = {"currency_list": ["TRY", "USD", "EUR"]}
        assert is_subset(narrower, parent, [block.variables[2]])

    def test_enum_multi_disjoint_not_subset(self, block):
        a = {"currency_list": ["GBP"]}
        b = {"currency_list": ["TRY", "USD"]}
        assert not is_subset(a, b, [block.variables[2]])

    def test_number_range_narrower_is_subset(self):
        from presentations.blocks.schema import Variable
        v = Variable(name="amount", semantic_tag="other", type="number_range",
                     required=True, default={"min": 0, "max": 100})
        assert is_subset({"amount": {"min": 10, "max": 50}},
                          {"amount": {"min": 0, "max": 100}}, [v])
        assert not is_subset({"amount": {"min": -1, "max": 50}},
                              {"amount": {"min": 0, "max": 100}}, [v])

    def test_missing_var_in_current_is_subset(self, block):
        # If a variable is optional and unset in both, that's fine.
        a = {}
        b = {}
        assert is_subset(a, b, block.variables[3:])  # empty slice

    def test_missing_var_in_only_one_fails(self, block):
        a = {"as_of_from": "2026-05-01"}
        b = {}
        assert not is_subset(a, b, [block.variables[0]])

    def test_all_vars_must_be_subset(self, block):
        # Date narrower but enum wider → overall not subset.
        a = {"as_of_from": "2026-05-15", "currency_list": ["TRY", "USD", "GBP"]}
        b = {"as_of_from": "2026-05-01", "currency_list": ["TRY", "USD"]}
        assert not is_subset(a, b, block.variables[:1] + [block.variables[2]])


# ── BlockCache round-trip ────────────────────────────────────────────────

class TestBlockCacheRoundtrip:
    def test_write_then_find_exact(self, conn, block):
        cache = BlockCache(conn)
        resolved = {
            "as_of_from": date(2026, 4, 1),
            "as_of_to": date(2026, 4, 30),
            "currency_list": ["TRY", "USD"],
        }
        df = pd.DataFrame({"BRANCH": ["A", "B"], "TOTAL": [100, 200]})
        entry = cache.write(block, resolved, df)
        assert entry.row_count == 2
        assert entry.view_name.startswith("v_cache_")
        # Re-key the same resolved values + SQL and look up.
        key = cache_key(block.id, block.version, resolved, block.query)
        hit = cache.find_exact(key)
        assert hit is not None
        assert hit.row_count == 2

    def test_sql_edit_invalidates_exact_hit(self, conn, block):
        # #20: editing a block's SQL in place (same id/version/vars, no version
        # bump) must NOT return the previous query's cached rows.
        cache = BlockCache(conn)
        resolved = {
            "as_of_from": date(2026, 4, 1),
            "as_of_to": date(2026, 4, 30),
            "currency_list": ["TRY"],
        }
        cache.write(block, resolved, pd.DataFrame({"x": [1, 2]}))
        edited = block.model_copy(update={"query": "SELECT 2 FROM dual"})
        key = cache_key(edited.id, edited.version, resolved, edited.query)
        assert cache.find_exact(key) is None

    def test_find_subset_parent_returns_widest_match(self, conn, block):
        cache = BlockCache(conn)
        parent_resolved = {
            "as_of_from": date(2026, 4, 1),
            "as_of_to": date(2026, 4, 30),
            "currency_list": ["TRY", "USD", "EUR"],
        }
        cache.write(block, parent_resolved, pd.DataFrame({"x": [1, 2, 3]}))

        narrower_resolved = {
            "as_of_from": date(2026, 4, 1),
            "as_of_to": date(2026, 4, 30),
            "currency_list": ["TRY", "USD"],
        }
        parent = cache.find_subset_parent(block, narrower_resolved)
        assert parent is not None
        assert set(parent.resolved["currency_list"]) == {"TRY", "USD", "EUR"}

    def test_subset_parent_widening_miss(self, conn, block):
        cache = BlockCache(conn)
        cache.write(block, {
            "as_of_from": date(2026, 4, 10),
            "as_of_to": date(2026, 4, 20),
            "currency_list": ["TRY"],
        }, pd.DataFrame({"x": [1]}))

        # Wider date range than the parent → no subset, no parent.
        wider = {
            "as_of_from": date(2026, 4, 1),
            "as_of_to": date(2026, 4, 30),
            "currency_list": ["TRY"],
        }
        assert cache.find_subset_parent(block, wider) is None


# ── LRU eviction ──────────────────────────────────────────────────────────

class TestLRUEviction:
    def test_under_cap_no_eviction(self, conn, block):
        cache = BlockCache(conn)
        df = pd.DataFrame({"x": [1, 2, 3]})
        cache.write(block, {"as_of_from": date(2026, 4, 1),
                             "as_of_to": date(2026, 4, 30),
                             "currency_list": ["TRY"]}, df)
        assert cache.maybe_evict() == 0
        assert len(cache.list_all()) == 1

    def test_eviction_drops_oldest(self, conn, block, monkeypatch):
        """Force the cap below the size of two entries; second write evicts the first."""
        cache = BlockCache(conn)
        df = pd.DataFrame({"x": list(range(1000))})  # ~few KB
        # Shrink the cap to slightly under 2 * df_size so a second write triggers eviction.
        df_size = df.memory_usage(deep=True, index=True).sum()
        monkeypatch.setattr(cache, "SOFT_CAP_BYTES", int(df_size + 100), raising=False)

        cache.write(block, {"as_of_from": date(2026, 4, 1),
                             "as_of_to": date(2026, 4, 30),
                             "currency_list": ["TRY"]}, df)
        cache.write(block, {"as_of_from": date(2026, 5, 1),
                             "as_of_to": date(2026, 5, 30),
                             "currency_list": ["USD"]}, df)
        # After the second write, the first must have been evicted to fit cap.
        entries = cache.list_all()
        assert len(entries) == 1
        assert "USD" in entries[0].resolved["currency_list"]

    def test_evict_all(self, conn, block):
        cache = BlockCache(conn)
        cache.write(block, {"as_of_from": date(2026, 4, 1),
                             "as_of_to": date(2026, 4, 30),
                             "currency_list": ["TRY"]}, pd.DataFrame({"x": [1]}))
        cache.write(block, {"as_of_from": date(2026, 5, 1),
                             "as_of_to": date(2026, 5, 30),
                             "currency_list": ["USD"]}, pd.DataFrame({"x": [2]}))
        n = cache.evict_all()
        assert n == 2
        assert cache.list_all() == []


# ── Persistence across BlockCache instances ───────────────────────────────

class TestMetaTablePersistence:
    def test_meta_table_survives_new_instance(self, conn, block):
        c1 = BlockCache(conn)
        c1.write(block, {"as_of_from": date(2026, 4, 1),
                          "as_of_to": date(2026, 4, 30),
                          "currency_list": ["TRY"]}, pd.DataFrame({"x": [1]}))
        # New instance, same conn → existing entries still there.
        c2 = BlockCache(conn)
        assert len(c2.list_all()) == 1

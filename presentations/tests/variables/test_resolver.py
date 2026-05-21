"""Tests for presentations.variables.resolver."""
from __future__ import annotations

from datetime import date

import pytest

from presentations.blocks.schema import Block, Variable, load_block_from_dict
from presentations.variables.resolver import (
    BindingValue,
    ResolutionError,
    normalize_for_cache_key,
    parse_date_expr,
    resolve_variables,
)


# ── parse_date_expr ───────────────────────────────────────────────────────

class TestParseDateExpr:
    def test_today_literal(self, fixed_today):
        assert parse_date_expr("today", today=fixed_today) == fixed_today

    def test_today_minus_30d(self, fixed_today):
        assert parse_date_expr("today - 30d", today=fixed_today) == date(2026, 4, 21)

    def test_today_minus_2w(self, fixed_today):
        assert parse_date_expr("today - 2w", today=fixed_today) == date(2026, 5, 7)

    def test_today_minus_1m(self, fixed_today):
        # 2026-05-21 minus one month → 2026-04-21
        assert parse_date_expr("today - 1m", today=fixed_today) == date(2026, 4, 21)

    def test_today_minus_3m_clamps_day(self):
        # 2026-05-31 minus 3 months → 2026-02-28 (clamped from 31).
        d = date(2026, 5, 31)
        assert parse_date_expr("today - 3m", today=d) == date(2026, 2, 28)

    def test_today_minus_1y(self, fixed_today):
        assert parse_date_expr("today - 1y", today=fixed_today) == date(2025, 5, 21)

    def test_today_minus_4y_leap_clamp(self):
        # Feb 29 2024 minus 1 year → falls back to Feb 28 2023.
        d = date(2024, 2, 29)
        assert parse_date_expr("today - 1y", today=d) == date(2023, 2, 28)

    def test_start_of_month(self, fixed_today):
        assert parse_date_expr("start_of_month", today=fixed_today) == date(2026, 5, 1)

    def test_start_of_year(self, fixed_today):
        assert parse_date_expr("start_of_year", today=fixed_today) == date(2026, 1, 1)

    def test_start_of_quarter(self, fixed_today):
        # 2026-05-21 → Q2 → 2026-04-01
        assert parse_date_expr("start_of_quarter", today=fixed_today) == date(2026, 4, 1)

    def test_start_of_quarter_q1(self):
        assert parse_date_expr("start_of_quarter", today=date(2026, 2, 15)) == date(2026, 1, 1)

    def test_start_of_quarter_q4(self):
        assert parse_date_expr("start_of_quarter", today=date(2026, 12, 31)) == date(2026, 10, 1)

    def test_iso_literal(self):
        assert parse_date_expr("2026-01-15") == date(2026, 1, 15)

    def test_iso_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_date_expr("2026-02-30")

    def test_unrecognised_raises(self):
        with pytest.raises(ValueError):
            parse_date_expr("yesterday")

    def test_date_object_passes_through(self):
        d = date(2025, 1, 1)
        assert parse_date_expr(d) is d

    def test_case_insensitive(self, fixed_today):
        assert parse_date_expr("TODAY - 7D", today=fixed_today) == date(2026, 5, 14)


# ── resolve_variables on fixtures ─────────────────────────────────────────

class TestResolveVariables:
    def test_resolves_sample_block_with_defaults(self, sample_block_dict, fixed_today):
        block = load_block_from_dict(sample_block_dict)
        r = resolve_variables(block, today=fixed_today)
        assert r["as_of_from"] == date(2026, 4, 21)
        assert r["as_of_to"] == fixed_today
        assert r["currency_list"] == ["TRY", "USD", "EUR"]
        assert r["maturity_list"] == ["1M", "3M", "6M"]

    def test_override_replaces_default(self, sample_block_dict, fixed_today):
        block = load_block_from_dict(sample_block_dict)
        r = resolve_variables(
            block,
            overrides={"as_of_from": "today - 7d"},
            today=fixed_today,
        )
        assert r["as_of_from"] == date(2026, 5, 14)
        # other vars still default.
        assert r["as_of_to"] == fixed_today

    def test_override_with_date_object(self, sample_block_dict):
        block = load_block_from_dict(sample_block_dict)
        r = resolve_variables(
            block,
            overrides={"as_of_from": date(2026, 1, 1), "as_of_to": date(2026, 1, 31)},
        )
        assert r["as_of_from"] == date(2026, 1, 1)
        assert r["as_of_to"] == date(2026, 1, 31)

    def test_enum_multi_subset_check(self, sample_block_dict):
        block = load_block_from_dict(sample_block_dict)
        with pytest.raises(ResolutionError) as exc:
            resolve_variables(block, overrides={"currency_list": ["TRY", "XXX"]})
        assert any("XXX" in e for e in exc.value.errors)

    def test_enum_multi_empty_rejected(self, sample_block_dict):
        block = load_block_from_dict(sample_block_dict)
        with pytest.raises(ResolutionError):
            resolve_variables(block, overrides={"currency_list": []})

    def test_required_no_default_no_value_raises(self):
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT * FROM t WHERE a = :a",
            visualization={"type": "kpi", "config": {}},
            variables=[
                Variable(name="a_var", semantic_tag="other", type="date", required=True),
            ],
        )
        with pytest.raises(ResolutionError) as exc:
            resolve_variables(block)
        assert "required" in str(exc.value)

    def test_optional_no_value_resolves_to_none(self):
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT * FROM t",
            visualization={"type": "kpi", "config": {}},
            variables=[
                Variable(name="a_var", semantic_tag="other", type="date", required=False),
            ],
        )
        r = resolve_variables(block)
        assert r["a_var"] is None

    def test_dashboard_binding_resolver_supplies_value(self, sample_block_dict, fixed_today):
        """6.5.c will plug in here; this test pins the contract."""
        block = load_block_from_dict(sample_block_dict)

        def binder(var):
            if var.name == "currency_list":
                return BindingValue(value=["GBP", "CHF"], is_expression=False)
            return None

        r = resolve_variables(block, binding_resolver=binder, today=fixed_today)
        assert r["currency_list"] == ["GBP", "CHF"]
        # Untouched variables still default.
        assert r["as_of_from"] == date(2026, 4, 21)

    def test_date_range_resolution(self):
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1 FROM dual",
            visualization={"type": "kpi", "config": {}},
            variables=[
                Variable(
                    name="period_var", semantic_tag="as_of_time", type="date_range",
                    required=True,
                    default={"from": "today - 7d", "to": "today"},
                ),
            ],
        )
        r = resolve_variables(block, today=date(2026, 5, 21))
        assert r["period_var"] == {"from": date(2026, 5, 14), "to": date(2026, 5, 21)}

    def test_number_range_resolution(self):
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1 FROM dual",
            visualization={"type": "kpi", "config": {}},
            variables=[
                Variable(
                    name="amount_var", semantic_tag="other", type="number_range",
                    required=True, default={"min": 0, "max": 100},
                ),
            ],
        )
        r = resolve_variables(block)
        assert r["amount_var"] == {"min": 0.0, "max": 100.0}

    def test_number_range_min_gt_max_rejected(self):
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1 FROM dual",
            visualization={"type": "kpi", "config": {}},
            variables=[
                Variable(
                    name="amount_var", semantic_tag="other", type="number_range",
                    required=True, default={"min": 100, "max": 0},
                ),
            ],
        )
        with pytest.raises(ResolutionError):
            resolve_variables(block)


# ── normalize_for_cache_key ───────────────────────────────────────────────

class TestNormalizeForCacheKey:
    def test_dates_to_iso(self):
        out = normalize_for_cache_key({"d": date(2026, 5, 21)})
        assert out == {"d": "2026-05-21"}

    def test_enum_multi_sorted(self):
        out = normalize_for_cache_key({"c": ["USD", "EUR", "TRY"]})
        assert out == {"c": ["EUR", "TRY", "USD"]}

    def test_dict_sorted_keys(self):
        out = normalize_for_cache_key({"p": {"to": date(2026, 5, 21), "from": date(2026, 1, 1)}})
        assert list(out["p"].keys()) == ["from", "to"]
        assert out["p"]["from"] == "2026-01-01"

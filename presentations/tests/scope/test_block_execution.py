"""Block-execution scope awareness (spec §4.2): variable resolution prefers
pinned scope filter values; lazy aliases are guarded until 8.d."""
from __future__ import annotations

from datetime import date

import pytest

from presentations.blocks.schema import Block
from presentations.scope.binding import (
    build_scope_binding_resolver,
    check_block_routing,
    is_pinned_bound,
    referenced_aliases,
    routing_for_alias,
)
from presentations.variables.resolver import resolve_variables

FIXED_TODAY = date(2026, 5, 21)


def _block():
    return Block.model_validate({
        "id": "branch_position_kpi", "version": 1, "title": "KPI",
        "team": "retail_banking", "owner": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "query": "SELECT 1 FROM positions",
        "visualization": {"type": "kpi"},
        "variables": [
            {"name": "as_of_from", "semantic_tag": "as_of_time", "type": "date",
             "required": True, "default": "today"},
            {"name": "currency_list", "semantic_tag": "currency", "type": "enum_multi",
             "required": True, "allowed_values": ["TRY", "USD", "EUR", "GBP"],
             "default": ["TRY"]},
            {"name": "region_sel", "semantic_tag": "region", "type": "enum_single",
             "required": False, "allowed_values": ["Ege", "Marmara"], "default": "Ege"},
        ],
    })


def _resolve(scope, bindings, state):
    resolver = build_scope_binding_resolver(scope, bindings, state)
    return resolve_variables(_block(), {}, binding_resolver=resolver, today=FIXED_TODAY)


class TestVariableResolution:
    def test_pinned_bound_returns_pinned_value_ignoring_widget(self, sample_scope):
        # Widget state tries to override the pinned date — must be ignored.
        resolved = _resolve(
            sample_scope,
            {"as_of_from": {"from_scope_filter": "pf_q4_2025", "accessor": "from"}},
            {"pf_q4_2025": {"from": "1999-01-01"}},
        )
        assert resolved["as_of_from"] == date(2025, 10, 1)

    def test_interactive_bound_returns_widget_value(self, sample_scope):
        resolved = _resolve(
            sample_scope,
            {"currency_list": {"from_scope_filter": "if_currency"}},
            {"if_currency": ["USD", "EUR"]},
        )
        assert resolved["currency_list"] == ["USD", "EUR"]

    def test_interactive_falls_back_to_default_when_no_widget_state(self, sample_scope):
        resolved = _resolve(
            sample_scope, {"currency_list": {"from_scope_filter": "if_currency"}}, {},
        )
        # scope interactive default_values = [TRY, USD, EUR].
        assert resolved["currency_list"] == ["TRY", "USD", "EUR"]

    def test_unbound_variable_uses_block_default(self, sample_scope):
        resolved = _resolve(sample_scope, {}, {})
        assert resolved["region_sel"] == "Ege"

    def test_pinned_date_to_accessor(self, sample_scope):
        block = Block.model_validate({
            "id": "blk_to", "version": 1, "title": "t", "team": "treasury", "owner": "o",
            "created_at": "2026-06-15T10:00:00Z", "query": "SELECT 1",
            "visualization": {"type": "kpi"},
            "variables": [{"name": "as_of_to", "semantic_tag": "as_of_time",
                           "type": "date", "required": True, "default": "today"}],
        })
        resolver = build_scope_binding_resolver(
            sample_scope, {"as_of_to": {"from_scope_filter": "pf_q4_2025", "accessor": "to"}}, {},
        )
        resolved = resolve_variables(block, {}, binding_resolver=resolver, today=FIXED_TODAY)
        assert resolved["as_of_to"] == date(2025, 12, 31)


class TestIsPinnedBound:
    def test_pinned(self, sample_scope):
        assert is_pinned_bound(sample_scope, {"from_scope_filter": "pf_q4_2025"}) is True

    def test_interactive_is_not_pinned(self, sample_scope):
        assert is_pinned_bound(sample_scope, {"from_scope_filter": "if_currency"}) is False

    def test_non_scope_binding(self, sample_scope):
        assert is_pinned_bound(sample_scope, {"from_filter": "f_local"}) is False


class TestRouting:
    def test_referenced_aliases(self):
        sql = "SELECT * FROM positions p JOIN branch_dim b ON p.x = b.x"
        assert referenced_aliases(sql) == {"positions", "branch_dim"}

    def test_cached_aliases_pass(self, sample_scope):
        # sample status.cached_tables = [positions, branch_dim], lazy = [].
        check_block_routing(sample_scope, ["positions", "branch_dim"])  # no raise

    def test_lazy_alias_raises_not_implemented(self, sample_scope):
        sample_scope.status.lazy_tables = ["positions"]
        with pytest.raises(NotImplementedError, match="8.d"):
            check_block_routing(sample_scope, ["positions"])

    def test_routing_for_alias(self, sample_scope):
        assert routing_for_alias(sample_scope, "positions") == "cached"
        assert routing_for_alias(sample_scope, "unknown_alias") is None

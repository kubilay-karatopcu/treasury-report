"""Tests for presentations.dashboards.binding — auto-binding + resolver wiring."""
from __future__ import annotations

from datetime import date

import pytest

from presentations.blocks.schema import Variable
from presentations.dashboards.binding import (
    build_binding_resolver,
    initial_filter_state,
    propose_auto_bindings,
    unbound_variables,
)
from presentations.dashboards.schema import DashboardFilter, VariableBinding
from presentations.variables.resolver import resolve_variables


def _block_vars():
    return [
        Variable(name="as_of_from", semantic_tag="as_of_time", type="date",
                 required=True, default="today - 30d"),
        Variable(name="as_of_to", semantic_tag="as_of_time", type="date",
                 required=True, default="today"),
        Variable(name="currency_list", semantic_tag="currency", type="enum_multi",
                 required=True, allowed_values=["TRY", "USD", "EUR"],
                 default=["TRY", "USD"]),
        Variable(name="counterparty_groups", semantic_tag="counterparty",
                 type="enum_multi", required=False,
                 allowed_values=["DOMESTIC", "FOREIGN"],
                 default=["DOMESTIC"]),
    ]


def _dashboard_filters():
    return [
        DashboardFilter(id="f_period", semantic_tag="as_of_time",
                        type="date_range", label="Tarih",
                        default={"from": "today - 30d", "to": "today"}),
        DashboardFilter(id="f_currency", semantic_tag="currency",
                        type="enum_multi", label="Para",
                        allowed_values=["TRY", "USD", "EUR", "GBP"],
                        default=["TRY", "USD", "EUR"]),
    ]


# ── Auto-binding ──────────────────────────────────────────────────────────

class TestProposeAutoBindings:
    def test_unambiguous_date_range_from_to(self):
        bindings = propose_auto_bindings(_block_vars(), _dashboard_filters())
        # Variable name *_from / *_to disambiguates the accessor.
        assert bindings["as_of_from"].from_filter == "f_period"
        assert bindings["as_of_from"].accessor == "from"
        assert bindings["as_of_to"].from_filter == "f_period"
        assert bindings["as_of_to"].accessor == "to"

    def test_enum_multi_type_match(self):
        bindings = propose_auto_bindings(_block_vars(), _dashboard_filters())
        assert bindings["currency_list"].from_filter == "f_currency"
        assert bindings["currency_list"].accessor is None

    def test_unbound_when_no_tag_match(self):
        bindings = propose_auto_bindings(_block_vars(), _dashboard_filters())
        # counterparty has no matching filter.
        assert "counterparty_groups" not in bindings

    def test_ambiguous_accessor_left_unbound(self):
        # Variable name doesn't hint accessor → no auto-bind.
        vars_ = [Variable(name="middle_date", semantic_tag="as_of_time",
                          type="date", default="today")]
        bindings = propose_auto_bindings(vars_, _dashboard_filters())
        assert "middle_date" not in bindings

    def test_multiple_matches_left_unbound(self):
        # Two filters with same semantic_tag — UI must ask.
        filters = [
            DashboardFilter(id="f_period1", semantic_tag="as_of_time",
                            type="date_range", label="Tarih1",
                            default={"from": "today - 30d", "to": "today"}),
            DashboardFilter(id="f_period2", semantic_tag="as_of_time",
                            type="date_range", label="Tarih2",
                            default={"from": "today - 7d", "to": "today"}),
        ]
        bindings = propose_auto_bindings(_block_vars(), filters)
        assert "as_of_from" not in bindings


# ── Unbound variables ─────────────────────────────────────────────────────

class TestUnboundVariables:
    def test_no_binding_no_tag_match_is_unbound(self):
        # counterparty has no filter for it
        unbound = unbound_variables(_block_vars(), {}, _dashboard_filters())
        names = {v.name for v in unbound}
        assert "counterparty_groups" in names

    def test_bound_var_not_in_unbound(self):
        bindings = {"as_of_from": VariableBinding(from_filter="f_period",
                                                  accessor="from")}
        unbound = unbound_variables(_block_vars(), bindings, _dashboard_filters())
        names = {v.name for v in unbound}
        assert "as_of_from" not in names

    def test_tag_match_without_binding_still_listed(self):
        # 'Filter eklemek ister misiniz?' surfaces *all* unbound vars
        # including those with tag matches — UI offers auto-binding.
        unbound = unbound_variables(_block_vars(), {}, _dashboard_filters())
        names = {v.name for v in unbound}
        assert "as_of_from" in names  # tag-match exists but no binding


# ── Resolver wiring ───────────────────────────────────────────────────────

class TestBuildBindingResolver:
    def test_from_filter_value_propagates(self):
        bindings = {
            "as_of_from": VariableBinding(from_filter="f_period", accessor="from"),
            "as_of_to":   VariableBinding(from_filter="f_period", accessor="to"),
            "currency_list": VariableBinding(from_filter="f_currency"),
        }
        state = {
            "f_period": {"from": date(2026, 4, 1), "to": date(2026, 4, 30)},
            "f_currency": ["TRY", "EUR"],
        }
        cb = build_binding_resolver(bindings, state)

        from presentations.blocks.schema import Block
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1 WHERE x IN (:currency_list) AND y BETWEEN :as_of_from AND :as_of_to",
            visualization={"type": "kpi", "config": {}},
            variables=_block_vars(),
        )
        resolved = resolve_variables(block, binding_resolver=cb)
        assert resolved["as_of_from"] == date(2026, 4, 1)
        assert resolved["as_of_to"] == date(2026, 4, 30)
        assert resolved["currency_list"] == ["TRY", "EUR"]
        # counterparty_groups not bound → block default applies.
        assert resolved["counterparty_groups"] == ["DOMESTIC"]

    def test_constant_override(self):
        bindings = {"as_of_from": VariableBinding(constant="2026-01-01")}
        state = {}
        cb = build_binding_resolver(bindings, state)

        from presentations.blocks.schema import Block
        block = Block(
            id="block_a", version=1, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1 WHERE x = :as_of_from",
            visualization={"type": "kpi", "config": {}},
            variables=[Variable(name="as_of_from", semantic_tag="as_of_time",
                                type="date", required=True, default="today")],
        )
        resolved = resolve_variables(block, binding_resolver=cb)
        # Constant wins over block default.
        assert resolved["as_of_from"] == date(2026, 1, 1)


# ── initial_filter_state ──────────────────────────────────────────────────

class TestInitialFilterState:
    def test_date_range_parsed(self):
        state = initial_filter_state(_dashboard_filters())
        period = state["f_period"]
        assert isinstance(period["from"], date)
        assert isinstance(period["to"], date)

    def test_enum_multi_pass_through(self):
        state = initial_filter_state(_dashboard_filters())
        assert state["f_currency"] == ["TRY", "USD", "EUR"]

    def test_missing_default_skipped(self):
        filters = [
            DashboardFilter(id="f_ccy", semantic_tag="currency",
                            type="enum_multi", label="Para",
                            allowed_values=["TRY"]),  # no default
        ]
        state = initial_filter_state(filters)
        assert "f_ccy" not in state

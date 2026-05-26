"""Tests for presentations.dashboards.schema — Phase 6.5.c."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentations.dashboards.schema import (
    DashboardFilter,
    VariableBinding,
)


# ── DashboardFilter ──────────────────────────────────────────────────────

class TestDashboardFilter:
    def test_date_range_default_shape(self):
        f = DashboardFilter(
            id="f_period", semantic_tag="as_of_time", type="date_range",
            label="Tarih", default={"from": "today - 30d", "to": "today"},
        )
        assert f.default == {"from": "today - 30d", "to": "today"}

    def test_date_range_default_missing_field_rejected(self):
        with pytest.raises(ValidationError):
            DashboardFilter(
                id="f_period", semantic_tag="as_of_time", type="date_range",
                label="Tarih", default={"from": "today - 30d"},  # missing 'to'
            )

    def test_enum_multi_requires_allowed_values(self):
        with pytest.raises(ValidationError) as exc:
            DashboardFilter(
                id="f_ccy", semantic_tag="currency", type="enum_multi",
                label="Para",
                # allowed_values missing
            )
        assert "allowed_values" in str(exc.value)

    def test_enum_multi_default_subset_check(self):
        with pytest.raises(ValidationError):
            DashboardFilter(
                id="f_ccy", semantic_tag="currency", type="enum_multi",
                label="Para",
                allowed_values=["TRY", "USD", "EUR"],
                default=["TRY", "ZZZ"],
            )

    def test_unknown_semantic_tag_rejected(self):
        with pytest.raises(ValidationError):
            DashboardFilter(
                id="f_bad", semantic_tag="invented_tag", type="enum_single",
                label="x", allowed_values=["a"],
            )

    def test_other_tag_allowed(self):
        f = DashboardFilter(
            id="f_misc", semantic_tag="other", type="enum_single",
            label="Misc", allowed_values=["a"],
        )
        assert f.semantic_tag == "other"

    def test_allowed_values_only_for_enum_types(self):
        with pytest.raises(ValidationError):
            DashboardFilter(
                id="f_period", semantic_tag="as_of_time", type="date_range",
                label="x", allowed_values=["nope"],
            )

    def test_short_id_rejected(self):
        with pytest.raises(ValidationError):
            DashboardFilter(
                id="ab", semantic_tag="other", type="enum_single",
                label="x", allowed_values=["a"],
            )


# ── VariableBinding ──────────────────────────────────────────────────────

class TestVariableBinding:
    def test_from_filter_only(self):
        vb = VariableBinding(from_filter="f_period", accessor="from")
        assert vb.from_filter == "f_period"
        assert vb.constant is None

    def test_constant_only(self):
        vb = VariableBinding(constant="today - 7d")
        assert vb.constant == "today - 7d"
        assert vb.from_filter is None

    def test_both_sources_rejected(self):
        with pytest.raises(ValidationError) as exc:
            VariableBinding(from_filter="f_period", constant="today")
        assert "exactly one" in str(exc.value)

    def test_neither_source_rejected(self):
        with pytest.raises(ValidationError):
            VariableBinding()

    def test_accessor_without_from_filter_rejected(self):
        with pytest.raises(ValidationError):
            VariableBinding(constant="today", accessor="from")

    def test_invalid_accessor_rejected(self):
        with pytest.raises(ValidationError):
            VariableBinding(from_filter="f_period", accessor="middle")  # not a Literal

"""Tests for presentations.blocks.schema — Phase 6.5.a."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from presentations.blocks.schema import (
    Block,
    BlockDocument,
    Variable,
    block_to_dict,
    load_block_from_dict,
)


class TestLoadFromFixture:
    def test_sample_block_parses(self, sample_block_dict):
        block = load_block_from_dict(sample_block_dict)
        assert block.id == "branch_position_kpi"
        assert block.team == "retail_banking"
        assert block.version == 1
        assert len(block.variables) == 4
        assert {v.name for v in block.variables} == {
            "as_of_from", "as_of_to", "currency_list", "maturity_list",
        }

    def test_sample_block_2_parses(self, sample_block_2_dict):
        block = load_block_from_dict(sample_block_2_dict)
        assert block.id == "fx_exposure_line"
        assert block.team == "treasury"
        assert block.version == 2

    def test_round_trip_via_block_to_dict(self, sample_block_dict):
        block = load_block_from_dict(sample_block_dict)
        out = block_to_dict(block)
        assert out["block"]["id"] == "branch_position_kpi"
        # Confirm it re-parses.
        reparsed = load_block_from_dict(out)
        assert reparsed.id == block.id
        assert [v.name for v in reparsed.variables] == [v.name for v in block.variables]


class TestSemanticTagEnforcement:
    """Acceptance: variable without semantic_tag / with bad tag is rejected."""

    def _strip_tag(self, raw, var_name, **changes):
        out = copy.deepcopy(raw)
        for v in out["block"]["variables"]:
            if v["name"] == var_name:
                v.update(changes)
                if "semantic_tag" in changes and changes["semantic_tag"] is None:
                    del v["semantic_tag"]
        return out

    def test_missing_semantic_tag_rejected(self, sample_block_dict):
        raw = self._strip_tag(sample_block_dict, "as_of_from", semantic_tag=None)
        with pytest.raises(ValidationError):
            load_block_from_dict(raw)

    def test_invalid_semantic_tag_rejected(self, sample_block_dict):
        raw = self._strip_tag(sample_block_dict, "currency_list", semantic_tag="not_a_real_tag")
        with pytest.raises(ValidationError) as exc:
            load_block_from_dict(raw)
        assert "not_a_real_tag" in str(exc.value)

    def test_other_tag_is_allowed(self, sample_block_dict):
        raw = self._strip_tag(sample_block_dict, "currency_list", semantic_tag="other")
        block = load_block_from_dict(raw)
        assert block.variables[2].semantic_tag == "other"


class TestEnumValidation:
    def _modify_var(self, raw, var_name, **changes):
        out = copy.deepcopy(raw)
        for v in out["block"]["variables"]:
            if v["name"] == var_name:
                v.update(changes)
        return out

    def test_enum_multi_default_must_be_subset(self, sample_block_dict):
        # currency_list allowed=[TRY,USD,EUR,GBP,CHF]; default has 'ZZZ' invalid.
        raw = self._modify_var(sample_block_dict, "currency_list",
                               default=["TRY", "ZZZ"])
        with pytest.raises(ValidationError) as exc:
            load_block_from_dict(raw)
        assert "ZZZ" in str(exc.value) or "not in allowed_values" in str(exc.value)

    def test_enum_multi_requires_allowed_values(self, sample_block_dict):
        raw = copy.deepcopy(sample_block_dict)
        for v in raw["block"]["variables"]:
            if v["name"] == "currency_list":
                v.pop("allowed_values", None)
        with pytest.raises(ValidationError):
            load_block_from_dict(raw)

    def test_enum_single_default_in_allowed_values(self):
        v = Variable(
            name="status_v",
            semantic_tag="other",
            type="enum_single",
            required=True,
            default="open",
            allowed_values=["open", "closed"],
        )
        assert v.default == "open"

    def test_enum_single_invalid_default(self):
        with pytest.raises(ValidationError):
            Variable(
                name="status_v",
                semantic_tag="other",
                type="enum_single",
                required=True,
                default="zzz",
                allowed_values=["open", "closed"],
            )


class TestDateExpressionShape:
    def test_today_minus_30d(self):
        Variable(
            name="date_a", semantic_tag="as_of_time", type="date",
            default="today - 30d",
        )

    def test_iso_literal(self):
        Variable(
            name="date_b", semantic_tag="as_of_time", type="date",
            default="2026-01-01",
        )

    def test_start_of_quarter(self):
        Variable(
            name="date_c", semantic_tag="as_of_time", type="date",
            default="start_of_quarter",
        )

    def test_bad_expression_rejected(self):
        with pytest.raises(ValidationError):
            Variable(
                name="date_d", semantic_tag="as_of_time", type="date",
                default="yesterday",
            )


class TestIdentifierRules:
    def test_kebab_block_id_rejected(self):
        with pytest.raises(ValidationError):
            Block(
                id="branch-position",  # hyphen disallowed
                version=1, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z",
                query="SELECT 1", visualization={"type": "kpi", "config": {}},
            )

    def test_too_short_id_rejected(self):
        with pytest.raises(ValidationError):
            Block(
                id="ab",  # length < 3
                version=1, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z",
                query="SELECT 1", visualization={"type": "kpi", "config": {}},
            )

    def test_duplicate_variable_names_rejected(self):
        with pytest.raises(ValidationError):
            Block(
                id="block_a",
                version=1, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z",
                query="SELECT 1",
                visualization={"type": "kpi", "config": {}},
                variables=[
                    {"name": "x_var", "semantic_tag": "as_of_time", "type": "date"},
                    {"name": "x_var", "semantic_tag": "as_of_time", "type": "date"},
                ],
            )


class TestVersioning:
    def test_version_must_be_positive(self):
        with pytest.raises(ValidationError):
            Block(
                id="block_a", version=0, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z",
                query="SELECT 1", visualization={"type": "kpi", "config": {}},
            )

    def test_deprecated_flag(self):
        block = Block(
            id="block_a", version=3, title="x", team="treasury", owner="x",
            created_at="2026-05-21T10:00:00Z",
            query="SELECT 1", visualization={"type": "kpi", "config": {}},
            deprecated=True,
        )
        assert block.deprecated is True

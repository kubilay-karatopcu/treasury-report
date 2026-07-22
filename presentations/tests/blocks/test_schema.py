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


class TestCompositeBlock:
    """Phase 12.container — carousel/canvas blocks stored as composite (no SQL,
    children carried verbatim)."""

    def _composite(self, **over):
        kw = dict(
            id="overview_carousel", version=1, title="Genel Bakış",
            team="treasury", owner="A16438",
            created_at="2026-05-21T10:00:00Z",
            kind="composite",
            visualization={"type": "carousel", "config": {}},
            children=[
                {"id": "sl1", "type": "kpi", "title": "Toplam",
                 "data_source": {"original_sql": "SELECT 1 AS value"}, "config": {}},
            ],
        )
        kw.update(over)
        return Block(**kw)

    def test_composite_parses(self):
        b = self._composite()
        assert b.kind == "composite"
        assert b.query == ""                       # composite carries no SQL
        assert b.visualization.type == "carousel"
        assert len(b.children) == 1

    def test_composite_round_trip(self):
        b = self._composite()
        reparsed = load_block_from_dict(block_to_dict(b))
        assert reparsed.kind == "composite"
        assert reparsed.children[0]["type"] == "kpi"

    def test_canvas_kind_allowed(self):
        b = self._composite(visualization={"type": "canvas", "config": {}})
        assert b.visualization.type == "canvas"

    def test_composite_without_children_rejected(self):
        with pytest.raises(ValidationError):
            self._composite(children=[])

    def test_composite_bad_viz_type_rejected(self):
        with pytest.raises(ValidationError):
            self._composite(visualization={"type": "kpi", "config": {}})

    def test_composite_child_without_type_rejected(self):
        with pytest.raises(ValidationError):
            self._composite(children=[{"id": "x"}])

    def test_single_requires_query(self):
        with pytest.raises(ValidationError):
            Block(
                id="block_a", version=1, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z",
                visualization={"type": "kpi", "config": {}},  # query omitted → ""
            )

    def test_single_with_children_rejected(self):
        with pytest.raises(ValidationError):
            Block(
                id="block_a", version=1, title="x", team="treasury", owner="x",
                created_at="2026-05-21T10:00:00Z", query="SELECT 1",
                visualization={"type": "kpi", "config": {}},
                children=[{"id": "c", "type": "kpi"}],
            )


class TestCustomBlock:
    """Süreç Düzenlileştirme — kind:'custom' blok: SQL/viz yok, custom_render var
    (docs/PROCESS_REGULARIZATION_PLAN.md §2.2)."""

    def _custom(self, **over):
        kw = dict(
            id="camon_bubble", version=1, title="Cost Bubble",
            team="dep", owner="A16438", created_at="2026-07-22T10:00:00Z",
            kind="custom",
            custom_render={"endpoint": "mevduat_panel.index",
                           "page": "cost-analysis", "anchor": "ca-mon-bub-bal"},
        )
        kw.update(over)
        return Block(**kw)

    def test_custom_parses(self):
        b = self._custom()
        assert b.kind == "custom"
        assert b.visualization is None
        assert b.query == ""
        assert b.custom_render.endpoint == "mevduat_panel.index"
        assert b.custom_render.anchor == "ca-mon-bub-bal"

    def test_custom_round_trip(self):
        b = self._custom(documentation={"purpose": "test"})
        reparsed = load_block_from_dict(block_to_dict(b))
        assert reparsed.kind == "custom"
        assert reparsed.custom_render.page == "cost-analysis"
        assert reparsed.documentation.purpose == "test"

    def test_custom_requires_custom_render(self):
        with pytest.raises(ValidationError):
            Block(
                id="camon_bubble", version=1, title="x", team="dep", owner="x",
                created_at="2026-07-22T10:00:00Z", kind="custom",
            )

    def test_custom_rejects_query(self):
        with pytest.raises(ValidationError):
            self._custom(query="SELECT 1")

    def test_custom_rejects_visualization(self):
        with pytest.raises(ValidationError):
            self._custom(visualization={"type": "kpi", "config": {}})

    def test_custom_rejects_children(self):
        with pytest.raises(ValidationError):
            self._custom(children=[{"id": "c", "type": "kpi"}])

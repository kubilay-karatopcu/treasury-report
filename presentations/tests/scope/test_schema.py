"""Schema + round-trip tests for the scope contract (spec §2.1, §2.4)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentations.scope.schema import (
    BasketItem,
    Join,
    PinnedFilter,
    Routing,
    ScopeContract,
    ScopeRef,
    TableRef,
    dump_scope_yaml,
    load_scope_yaml,
    scope_to_dict,
)


# ── Round-trip ──────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_load_sample(self, sample_scope):
        assert sample_scope.presentation_id == "p_abc123"
        assert sample_scope.version == 4
        assert sample_scope.parent_version == 3
        assert [b.alias for b in sample_scope.basket] == ["positions", "branch_dim"]
        assert len(sample_scope.filters.pinned) == 1
        assert len(sample_scope.filters.interactive) == 2

    def test_dump_is_idempotent(self, sample_scope):
        once = dump_scope_yaml(sample_scope)
        twice = dump_scope_yaml(load_scope_yaml(once))
        assert once == twice

    def test_object_roundtrip_preserves_model(self, sample_scope):
        reloaded = load_scope_yaml(dump_scope_yaml(sample_scope))
        assert reloaded == sample_scope

    def test_yaml_to_dict_byte_identical(self, sample_scope):
        # object → YAML → object → YAML is byte-identical (stable serialization).
        d1 = dump_scope_yaml(sample_scope)
        d2 = dump_scope_yaml(load_scope_yaml(d1))
        assert d1 == d2

    def test_on_code_not_coerced_to_bool(self, sample_scope):
        # `ON` (overnight maturity) must stay a string, not YAML-1.1 True.
        maturity = sample_scope.find_interactive("if_maturity")
        assert "ON" in maturity.allowed_values
        assert True not in maturity.allowed_values

    def test_top_level_wrapper_optional(self, sample_scope_text):
        from presentations.scope._yaml import load_yaml
        from presentations.scope.schema import load_scope_from_dict
        raw = load_yaml(sample_scope_text)
        assert set(raw.keys()) == {"scope"}
        wrapped = load_scope_from_dict(raw)
        bare = load_scope_from_dict(raw["scope"])
        assert wrapped == bare

    def test_dates_normalised_to_iso_strings(self, sample_scope):
        pf = sample_scope.find_pinned("pf_q4_2025")
        assert pf.from_ == "2025-10-01"
        assert pf.to == "2025-12-31"


# ── Defaults (§2.1) ─────────────────────────────────────────────────────────

class TestDefaults:
    def _minimal(self) -> dict:
        return dict(
            presentation_id="p_x", version=1, created_by="A16438",
            created_at="2026-06-15T10:00:00Z",
        )

    def test_documented_defaults(self):
        sc = ScopeContract.model_validate(self._minimal())
        assert sc.parent_version is None
        assert sc.basket == []
        assert sc.joins == []
        assert sc.filters.pinned == [] and sc.filters.interactive == []
        assert sc.status.state == "drafting"
        assert sc.status.cached_tables == [] and sc.status.lazy_tables == []
        assert sc.status.errors == []

    def test_projection_include_all_default_false(self):
        item = BasketItem.model_validate({
            "table_ref": {"schema": "ODS_TREASURY", "name": "T"},
            "alias": "positions",
            "projection": {"columns": ["A"]},
            "routing": {"decision": "cached", "estimated_bytes": 1},
        })
        assert item.projection.include_all is False
        assert item.routing.decided_by == "system"
        assert item.routing.threshold_bytes is None

    def test_applies_to_default_empty(self):
        pf = PinnedFilter.model_validate(
            {"id": "pf_x", "concept": "currency", "op": "in", "values": ["TRY"]}
        )
        assert pf.applies_to == []


# ── Identifier rules (§2.4) ─────────────────────────────────────────────────

class TestIdentifierRules:
    def test_alias_must_be_snake_case(self):
        with pytest.raises(ValidationError):
            BasketItem.model_validate({
                "table_ref": {"schema": "S", "name": "T"},
                "alias": "BadAlias",
                "projection": {"columns": []},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            })

    def test_alias_min_length(self):
        with pytest.raises(ValidationError):
            BasketItem.model_validate({
                "table_ref": {"schema": "S", "name": "T"},
                "alias": "ab",
                "projection": {"columns": []},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            })

    def test_pinned_id_prefix(self):
        with pytest.raises(ValidationError):
            PinnedFilter.model_validate({"id": "q4", "concept": "c", "op": "in"})
        ok = PinnedFilter.model_validate({"id": "pf_q4_2025", "concept": "c", "op": "in"})
        assert ok.id == "pf_q4_2025"

    def test_interactive_id_prefix(self):
        from presentations.scope.schema import InteractiveFilter
        with pytest.raises(ValidationError):
            InteractiveFilter.model_validate({"id": "ccy", "concept": "currency", "op": "in"})

    def test_join_id_prefix(self):
        with pytest.raises(ValidationError):
            Join.model_validate({
                "id": "positions_branch", "kind": "lookup",
                "left": {"alias": "positions", "column": "X"},
                "right": {"alias": "branch_dim", "column": "Y"},
            })

    def test_schema_alias_roundtrips(self):
        tr = TableRef.model_validate({"schema": "ODS_TREASURY", "name": "T"})
        assert tr.schema_name == "ODS_TREASURY"
        dumped = tr.model_dump(by_alias=True)
        assert dumped["schema"] == "ODS_TREASURY"

    def test_parent_version_must_be_below_version(self):
        with pytest.raises(ValidationError):
            ScopeContract.model_validate({
                "presentation_id": "p", "version": 2, "created_by": "A16438",
                "created_at": "2026-06-15T10:00:00Z", "parent_version": 2,
            })


class TestScopeRef:
    def test_valid(self):
        r = ScopeRef.model_validate({"presentation_id": "p_abc123", "scope_version": 4})
        assert r.scope_version == 4

    def test_requires_version(self):
        with pytest.raises(ValidationError):
            ScopeRef.model_validate({"presentation_id": "p_abc123"})

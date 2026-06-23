"""Schema + round-trip tests for the scope contract (spec §2.1, §2.4)."""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from presentations.scope.schema import (
    BasketItem,
    Filters,
    Join,
    PinnedFilter,
    Routing,
    ScopeContract,
    ScopeRef,
    TableRef,
    dump_scope_yaml,
    load_scope_from_dict,
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


# ── ID benzersizliği (FIX A) ────────────────────────────────────────────────
# Join.id / PinnedFilter.id / InteractiveFilter.id / RawFilter.id şemada
# benzersiz olmalı: find_* / patch validator ilk eşleşeni alır, tekrar eden id
# ikinciyi sessizce gölgeler (alias benzersizliği gibi şemada kesilir).

class TestIdUniqueness:
    def _two(self, ids):
        return [{"id": ids[0], "concept": "c", "op": "in", "values": ["A"]},
                {"id": ids[1], "concept": "c", "op": "in", "values": ["B"]}]

    def test_duplicate_pinned_id_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate pinned filter id 'pf_dup'"):
            Filters.model_validate({
                "pinned": self._two(["pf_dup", "pf_dup"]),
                "interactive": [], "raw": [],
            })

    def test_duplicate_interactive_id_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate interactive filter id 'if_dup'"):
            Filters.model_validate({
                "pinned": [],
                "interactive": [{"id": "if_dup", "concept": "c", "op": "in"},
                                {"id": "if_dup", "concept": "c", "op": "in"}],
                "raw": [],
            })

    def test_duplicate_raw_id_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate raw filter id 'rf_dup'"):
            Filters.model_validate({
                "pinned": [], "interactive": [],
                "raw": [{"id": "rf_dup", "alias": "positions", "column": "C",
                         "op": "eq", "value": 1},
                        {"id": "rf_dup", "alias": "positions", "column": "C",
                         "op": "eq", "value": 2}],
            })

    def test_unique_filter_ids_accepted(self):
        f = Filters.model_validate({
            "pinned": self._two(["pf_a", "pf_b"]),
            "interactive": [{"id": "if_a", "concept": "c", "op": "in"},
                            {"id": "if_b", "concept": "c", "op": "in"}],
            "raw": [{"id": "rf_a", "alias": "positions", "column": "C",
                     "op": "eq", "value": 1}],
        })
        assert [p.id for p in f.pinned] == ["pf_a", "pf_b"]
        assert [i.id for i in f.interactive] == ["if_a", "if_b"]
        assert [r.id for r in f.raw] == ["rf_a"]

    def _scope_with_joins(self, joins):
        return {
            "presentation_id": "p", "version": 1, "created_by": "A",
            "created_at": "2026-06-15T10:00:00Z",
            "basket": [
                {"alias": "positions", "table_ref": {"schema": "S", "name": "T"},
                 "projection": {"columns": [], "include_all": True},
                 "routing": {"decision": "cached", "estimated_bytes": 0}},
                {"alias": "branch_dim", "table_ref": {"schema": "S", "name": "T2"},
                 "projection": {"columns": [], "include_all": True},
                 "routing": {"decision": "cached", "estimated_bytes": 0}},
            ],
            "joins": joins,
        }

    def test_duplicate_join_id_rejected(self):
        dup = [
            {"id": "j_dup", "kind": "lookup",
             "left": {"alias": "positions", "column": "X"},
             "right": {"alias": "branch_dim", "column": "Y"}},
            {"id": "j_dup", "kind": "inner",
             "left": {"alias": "positions", "column": "X"},
             "right": {"alias": "branch_dim", "column": "Y"}},
        ]
        with pytest.raises(ValidationError, match="Duplicate join id 'j_dup'"):
            load_scope_from_dict(self._scope_with_joins(dup))

    def test_unique_join_ids_accepted(self):
        ok = [
            {"id": "j_one", "kind": "lookup",
             "left": {"alias": "positions", "column": "X"},
             "right": {"alias": "branch_dim", "column": "Y"}},
            {"id": "j_two", "kind": "inner",
             "left": {"alias": "positions", "column": "X"},
             "right": {"alias": "branch_dim", "column": "Y"}},
        ]
        sc = load_scope_from_dict(self._scope_with_joins(ok))
        assert [j.id for j in sc.joins] == ["j_one", "j_two"]


class TestScopeRef:
    def test_valid(self):
        r = ScopeRef.model_validate({"presentation_id": "p_abc123", "scope_version": 4})
        assert r.scope_version == 4

    def test_requires_version(self):
        with pytest.raises(ValidationError):
            ScopeRef.model_validate({"presentation_id": "p_abc123"})


# ── Bool-safe loader: yinelenen anahtarlar reddedilir (silent-corruption fix) ─

class TestDuplicateKeysRejected:
    """``_BoolSafeLoader`` yinelenen eşleme anahtarlarını sessizce son-kazanır
    olarak kabul etmek yerine hata vermeli; bool-resolver override'ı ve geçerli
    YAML davranışı bozulmamalı."""

    def test_duplicate_top_level_key_raises(self):
        from presentations.scope._yaml import load_yaml
        with pytest.raises(yaml.constructor.ConstructorError):
            load_yaml("version: 1\nversion: 2\n")

    def test_duplicate_nested_key_raises(self):
        from presentations.scope._yaml import load_yaml
        with pytest.raises(yaml.constructor.ConstructorError):
            load_yaml("scope:\n  presentation_id: a\n  presentation_id: b\n")

    def test_valid_yaml_still_loads(self):
        from presentations.scope._yaml import load_yaml
        assert load_yaml("a: 1\nb:\n  c: 2\n") == {"a": 1, "b": {"c": 2}}

    def test_bool_resolver_override_intact(self):
        # Gerçek YAML bool'ları bool kalır; ``ON`` / ``NO`` gibi kodlar string.
        from presentations.scope._yaml import load_yaml
        out = load_yaml("flag: true\noff: false\ncode: ON\nno: NO\n")
        assert out == {"flag": True, "off": False, "code": "ON", "no": "NO"}

    def test_sample_scope_still_loads(self, phase8_dir):
        from presentations.scope._yaml import load_yaml
        text = (phase8_dir / "sample_scope.yaml").read_text(encoding="utf-8")
        raw = load_yaml(text)
        assert set(raw.keys()) == {"scope"}

    def test_concept_loader_duplicate_key_raises(self):
        # Parite: concepts/registry yükleyicisi de yinelenen anahtarı reddetmeli.
        from presentations.concepts.registry import _load_yaml
        with pytest.raises(yaml.constructor.ConstructorError):
            _load_yaml("id: a\nid: b\n")

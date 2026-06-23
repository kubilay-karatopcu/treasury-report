"""Validator tests (spec §2.2). One case per rule, driven by the fixture
``examples/phase_8/expected_validator_outputs.yaml`` with exact-string matching
so the 8.b UI can rely on the messages."""
from __future__ import annotations

import pytest

from presentations.scope import validators as V
from presentations.scope.validators import ValidationResult, validate_scope


_RULE_FUNCS = {
    1: V.rule_alias_uniqueness,
    2: V.rule_concept_validity,
    3: V.rule_concept_coverage,
    4: V.rule_pinned_consistency,
    5: V.rule_join_consistency,
    6: V.rule_projection_sanity,
    7: V.rule_routing_threshold,
}


def _case_id(case: dict) -> str:
    return f"rule{case['rule']}_{case['name']}"


def test_all_rules_have_a_case(validator_cases):
    covered = {c["rule"] for c in validator_cases}
    assert covered == set(range(1, 8)), f"missing rule coverage: {set(range(1, 8)) - covered}"


def test_validator_cases(validator_cases, catalog, scope_from_excerpt):
    """Each fixture case: run only its named rule, assert the exact error /
    warnings. Runs them all here (also see the per-rule explicit tests below)."""
    for case in validator_cases:
        scope = scope_from_excerpt(case["scope_excerpt"])
        errors, warnings = _RULE_FUNCS[case["rule"]](scope, catalog)

        exp_err = case.get("expected_error")
        if exp_err is None:
            assert errors == [], f"{_case_id(case)}: unexpected errors {errors}"
        else:
            assert exp_err in errors, f"{_case_id(case)}: {exp_err!r} not in {errors}"

        exp_warn = case.get("expected_warnings") or []
        assert warnings == exp_warn, f"{_case_id(case)}: warnings {warnings} != {exp_warn}"


# ── Explicit per-rule tests (independently testable, spec §2.2.1–7) ──────────

def _case(cases, name):
    for c in cases:
        if c["name"] == name:
            return c
    raise KeyError(name)


def test_rule1_alias_uniqueness(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "duplicate_alias")
    errors, _ = V.rule_alias_uniqueness(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == ["Duplicate basket alias 'positions'"]


def test_rule2_concept_validity(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "unknown_concept")
    errors, _ = V.rule_concept_validity(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert "Concept 'not_a_real_concept' not in registry" in errors


def test_rule3_concept_coverage_is_warning(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "concept_coverage_warning")
    errors, warnings = V.rule_concept_coverage(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == []
    assert warnings == [
        "Filter 'if_currency' has no effect on alias 'branch_dim' "
        "(concept 'currency' not bound)"
    ]


def test_rule4_between_inverted(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "pinned_between_inverted")
    errors, _ = V.rule_pinned_consistency(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == [
        "Pinned filter 'pf_bad_range': between requires from <= to "
        "(got 2025-12-31 > 2025-10-01)"
    ]


def test_rule4_value_outside_canonical(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "pinned_value_outside_canonical")
    errors, _ = V.rule_pinned_consistency(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == [
        "Pinned filter 'pf_bad_ccy': value 'ZZZ' not in concept 'currency' canonical_values"
    ]


def test_rule5_join_unknown_alias(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "join_unknown_alias")
    errors, _ = V.rule_join_consistency(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert "Join 'j_bogus': right alias 'nonexistent_alias' not in basket" in errors


def test_rule5_join_column_not_projected(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "join_column_not_projected")
    errors, _ = V.rule_join_consistency(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert "Join 'j_missing_col': column 'BRANCH_ID' not projected on alias 'positions'" in errors


def test_rule6_missing_partition_warns(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "projection_missing_partition_column")
    errors, warnings = V.rule_projection_sanity(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == []
    assert warnings == [
        "Projection on 'positions' omits partition column 'AS_OF_DATE'; queries may be slow"
    ]


def test_rule6_unknown_column_errors(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "projection_unknown_column")
    errors, _ = V.rule_projection_sanity(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == [
        "Projection on 'positions': column 'NONEXISTENT_COL' does not exist on TRD_BRANCH_POSITION"
    ]


def test_rule7_negative_bytes_errors(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "routing_estimated_bytes_negative")
    errors, _ = V.rule_routing_threshold(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == ["Routing for 'positions': estimated_bytes must be >= 0 (got -100)"]


def test_rule7_threshold_below_floor_warns(validator_cases, catalog, scope_from_excerpt):
    c = _case(validator_cases, "routing_threshold_below_floor")
    errors, warnings = V.rule_routing_threshold(scope_from_excerpt(c["scope_excerpt"]), catalog)
    assert errors == []
    assert warnings == [
        "Routing for 'positions': threshold_bytes 1000 below floor (1048576), "
        "likely misconfiguration"
    ]


# ── Rule 4/8: reversed NUMERIC between (edge case) ───────────────────────────
# Ters sayısal between (from > to) eskiden yalnız tarih çözülünce yakalanıyordu;
# sayısal aralık (tutar/adet) sızıp `col BETWEEN 20 AND 10` üretip sessizce 0
# satır eşliyordu. Hem pinned hem raw için _StubCatalog yeterli (between kolu
# katalog çağırmaz).


class _StubCatalog:
    """between kontrolü kataloğa dokunmaz; table_meta/concept_canonical_codes
    None döndüren minimal stub."""

    def table_meta(self, *a):
        return None

    def concept_canonical_codes(self, c):
        return None


def _scope_with_filters(scope_from_excerpt, filters):
    return scope_from_excerpt({
        "basket": [{
            "alias": "positions",
            "table_ref": {"schema": "S", "name": "T"},
            "projection": {"columns": [], "include_all": True},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        }],
        "filters": filters,
        "joins": [],
    })


def test_raw_reversed_numeric_between_errors(scope_from_excerpt):
    scope = _scope_with_filters(scope_from_excerpt, {
        "pinned": [], "interactive": [],
        "raw": [{"id": "rf_amt", "alias": "positions", "column": "AMOUNT",
                 "op": "between", "from": 20, "to": 10}],
    })
    errors, _ = V.rule_raw_filters(scope, _StubCatalog())
    assert errors == ["Raw filter 'rf_amt': between requires from <= to (got 20 > 10)"]


def test_pinned_reversed_numeric_between_errors(scope_from_excerpt):
    scope = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_amt", "concept": "amount",
                    "op": "between", "from": 20, "to": 10}],
        "interactive": [], "raw": [],
    })
    errors, _ = V.rule_pinned_consistency(scope, _StubCatalog())
    assert errors == ["Pinned filter 'pf_amt': between requires from <= to (got 20 > 10)"]


def test_numeric_between_correct_order_ok(scope_from_excerpt):
    cat = _StubCatalog()
    pinned = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_amt", "concept": "amount",
                    "op": "between", "from": 10, "to": 20}],
        "interactive": [], "raw": [],
    })
    raw = _scope_with_filters(scope_from_excerpt, {
        "pinned": [], "interactive": [],
        "raw": [{"id": "rf_amt", "alias": "positions", "column": "AMOUNT",
                 "op": "between", "from": 10, "to": 20}],
    })
    assert V.rule_pinned_consistency(pinned, cat) == ([], [])
    assert V.rule_raw_filters(raw, cat) == ([], [])


def test_reversed_date_between_still_errors(scope_from_excerpt):
    # Regresyon: tarih ters aralığı eskisi gibi hata vermeli.
    cat = _StubCatalog()
    pinned = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_d", "concept": "date", "op": "between",
                    "from": "2025-12-31", "to": "2025-10-01"}],
        "interactive": [], "raw": [],
    })
    raw = _scope_with_filters(scope_from_excerpt, {
        "pinned": [], "interactive": [],
        "raw": [{"id": "rf_d", "alias": "positions", "column": "D",
                 "op": "between", "from": "2025-12-31", "to": "2025-10-01"}],
    })
    p_err, _ = V.rule_pinned_consistency(pinned, cat)
    r_err, _ = V.rule_raw_filters(raw, cat)
    assert p_err == ["Pinned filter 'pf_d': between requires from <= to (got 2025-12-31 > 2025-10-01)"]
    assert r_err == ["Raw filter 'rf_d': between requires from <= to (got 2025-12-31 > 2025-10-01)"]


# ── Rule 4: in/not_in scalar `value` kanonik kontrolü (FIX B) ────────────────
# Bir 'in' filtresi yanlışlıkla skalar `value` taşıyorsa (op/value uyumsuzluğu),
# rule_pinned_consistency eskiden `f.values or []` okuyup kanonik üyelik
# kontrolünü TÜMÜYLE ATLIYORDU → kanonik-dışı kod sessizce geçiyordu. Artık
# skalar `value` de aralık kontrolünden geçer.

class _CanonStubCatalog:
    """concept_canonical_codes belirli bir concept için kanonik liste döndürür;
    table_meta None (between yolu kataloğa dokunmaz)."""

    def table_meta(self, *a):
        return None

    def concept_canonical_codes(self, concept_id):
        return ["TRY", "USD", "EUR"] if concept_id == "currency" else None


def test_in_filter_scalar_value_outside_canonical_flagged(scope_from_excerpt):
    scope = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_ccy", "concept": "currency",
                    "op": "in", "value": "ZZZ"}],
        "interactive": [], "raw": [],
    })
    errors, _ = V.rule_pinned_consistency(scope, _CanonStubCatalog())
    assert errors == [
        "Pinned filter 'pf_ccy': value 'ZZZ' not in concept 'currency' canonical_values"
    ]


def test_in_filter_scalar_value_in_canonical_ok(scope_from_excerpt):
    # Doğru op/value kullanımı (values listesi) ve kanonik-içi skalar `value`
    # ikisi de temiz geçmeli — false positive yok.
    via_values = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_ccy", "concept": "currency",
                    "op": "in", "values": ["TRY", "USD"]}],
        "interactive": [], "raw": [],
    })
    via_scalar = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_ccy", "concept": "currency",
                    "op": "in", "value": "EUR"}],
        "interactive": [], "raw": [],
    })
    assert V.rule_pinned_consistency(via_values, _CanonStubCatalog()) == ([], [])
    assert V.rule_pinned_consistency(via_scalar, _CanonStubCatalog()) == ([], [])


def test_unresolvable_relative_date_between_silently_skipped(scope_from_excerpt):
    # Çözülemeyen string ifadeler (örn. değişken adları) sıralanamaz → sessiz
    # geç; sayısal kola da düşmemeli (false positive yok).
    scope = _scope_with_filters(scope_from_excerpt, {
        "pinned": [{"id": "pf_rel", "concept": "date", "op": "between",
                    "from": "some_var", "to": "other_var"}],
        "interactive": [], "raw": [],
    })
    errors, _ = V.rule_pinned_consistency(scope, _StubCatalog())
    assert errors == []


# ── Aggregate ────────────────────────────────────────────────────────────────

def test_sample_scope_validates_clean(sample_scope, catalog):
    res = validate_scope(sample_scope, catalog)
    assert isinstance(res, ValidationResult)
    assert res.ok is True
    assert res.errors == []
    assert res.warnings == []


# ── Rule 10: derivation DAG (cycle) ─────────────────────────────────────────

def test_rule10_derivation_cycle_blocked():
    from presentations.scope.schema import load_scope_from_dict

    scope = load_scope_from_dict({
        "presentation_id": "p_c", "version": 1, "created_by": "A",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {"alias": "node_a",
             "derivation": {"kind": "filter", "source_alias": "node_b",
                            "filters": {"pinned": [], "raw": [
                                {"id": "rf_c_1", "alias": "node_a", "column": "C",
                                 "op": "eq", "value": 1}]}},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "estimated_bytes": 0}},
            {"alias": "node_b",
             "derivation": {"kind": "filter", "source_alias": "node_a",
                            "filters": {"pinned": [], "raw": [
                                {"id": "rf_c_2", "alias": "node_b", "column": "C",
                                 "op": "eq", "value": 2}]}},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    })
    errors, warnings = V.rule_derivation_dag(scope, None)
    assert warnings == []
    assert len(errors) == 1 and "node_a" in errors[0] and "node_b" in errors[0]


def test_rule10_chain_without_cycle_passes():
    from presentations.scope.schema import load_scope_from_dict

    scope = load_scope_from_dict({
        "presentation_id": "p_c", "version": 1, "created_by": "A",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {"alias": "raw_src",
             "table_ref": {"schema": "S", "name": "T"},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "estimated_bytes": 0}},
            {"alias": "flt",
             "derivation": {"kind": "filter", "source_alias": "raw_src",
                            "filters": {"pinned": [], "raw": [
                                {"id": "rf_c_3", "alias": "flt", "column": "C",
                                 "op": "eq", "value": 1}]}},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "estimated_bytes": 0}},
            {"alias": "agg",
             "derivation": {"kind": "aggregate", "source_alias": "flt",
                            "group_by": ["C"],
                            "measures": [{"column": "X", "fn": "sum", "as": "SX"}]},
             "projection": {"columns": ["C", "SX"], "include_all": False},
             "routing": {"decision": "cached", "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    })
    errors, _ = V.rule_derivation_dag(scope, None)
    assert errors == []

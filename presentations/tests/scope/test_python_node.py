"""Faz P — ``kind: "python"`` node'unun scope katmanına entegrasyonu.

Şema doğrulaması, çapraz-referans validator'ı (rule_derived_tables),
DAG/döngü kuralı (rule_derivation_dag) ve fetch sıralaması (duck_source_aliases)
python node'unu TANIMALI. Bu testler katalog/Oracle gerektirmez."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentations.scope.fetch import duck_source_aliases
from presentations.scope.schema import Derivation, load_scope_from_dict
from presentations.scope.validators import rule_derivation_dag, rule_derived_tables


# ── Şema ────────────────────────────────────────────────────────────────────

def test_python_derivation_valid():
    d = Derivation(
        kind="python", source_alias="deposits",
        python_code="output_node_df = input_node_df.head(5)",
    )
    assert d.kind == "python"
    assert d.source_alias == "deposits"
    assert d.output_columns == []


def test_python_requires_source_alias():
    with pytest.raises(ValidationError):
        Derivation(kind="python", python_code="output_node_df = input_node_df")


def test_python_requires_code():
    with pytest.raises(ValidationError):
        Derivation(kind="python", source_alias="deposits")


def test_python_rejects_multi_source_fields():
    # İzolasyon: python tek-girişlidir, source_aliases/join_keys taşıyamaz.
    with pytest.raises(ValidationError):
        Derivation(
            kind="python", source_alias="deposits",
            python_code="output_node_df = input_node_df",
            source_aliases=["a", "b"],
        )


def test_other_kinds_reject_python_code():
    with pytest.raises(ValidationError):
        Derivation(
            kind="aggregate", source_alias="deposits",
            group_by=["BRANCH_CODE"], python_code="x = 1",
        )


# ── Scope entegrasyonu ──────────────────────────────────────────────────────

def _scope_with_python(source_alias="deposits"):
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {
                "table_ref": {"schema": "EDW", "name": "DEPOSITS"}, "alias": "deposits",
                "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
            {
                "derivation": {
                    "kind": "python", "source_alias": source_alias,
                    "python_code": "output_node_df = input_node_df.assign(X=1)",
                },
                "alias": "deposits_py",
                "projection": {"columns": [], "include_all": True},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
        ],
    })


def test_rule_derived_tables_accepts_valid_source():
    scope = _scope_with_python()
    errors, _ = rule_derived_tables(scope)
    assert errors == []


def test_rule_derived_tables_flags_missing_source():
    scope = _scope_with_python(source_alias="nope")
    errors, _ = rule_derived_tables(scope)
    assert any("nope" in e for e in errors)


def test_duck_source_aliases_includes_input():
    scope = _scope_with_python()
    item = scope.basket_item("deposits_py")
    assert duck_source_aliases(scope, item) == {"deposits"}


def test_dag_rule_passes_for_acyclic_python_chain():
    scope = _scope_with_python()
    errors, _ = rule_derivation_dag(scope)
    assert errors == []


def test_python_node_participates_in_dag_cycle_detection():
    # python(a)->b ve python(b)->a döngüsü DAG kuralıyla yakalanmalı.
    scope = load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {
                "derivation": {"kind": "python", "source_alias": "node_b",
                               "python_code": "output_node_df = input_node_df"},
                "alias": "node_a",
                "projection": {"columns": [], "include_all": True},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
            {
                "derivation": {"kind": "python", "source_alias": "node_a",
                               "python_code": "output_node_df = input_node_df"},
                "alias": "node_b",
                "projection": {"columns": [], "include_all": True},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
        ],
    })
    errors, _ = rule_derivation_dag(scope)
    assert errors  # döngü tespit edilmeli

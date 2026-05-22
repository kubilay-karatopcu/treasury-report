"""Phase 7.d — user-scoped concept validation + effective registry + promotion."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.user_scope import (
    validate_user_concept,
    build_effective_registry,
    UserConceptError,
)
from presentations.concepts.promotions import record_promotion, load_promotions


@pytest.fixture(scope="module")
def base():
    return ConceptRegistry.from_dir(Path(presentations.__file__).parent / "catalog" / "concepts")


# ── validate_user_concept ──────────────────────────────────────────────────

def test_valid_user_concept_forces_scope(base):
    c = validate_user_concept(base, {
        "id": "desk", "name": "Masa", "type": "enum",
        "canonical_values": [{"code": "FX"}, {"code": "RATES"}],
    })
    assert c.id == "desk"
    assert c.scope == "user"


def test_user_concept_collision_rejected(base):
    with pytest.raises(UserConceptError) as ei:
        validate_user_concept(base, {"id": "currency", "name": "X", "type": "enum"})
    assert "currency" in str(ei.value)


def test_user_concept_scope_field_ignored(base):
    # Even if the user tries to claim 'global', we force 'user'.
    c = validate_user_concept(base, {"id": "desk", "name": "M", "type": "enum",
                                     "scope": "global"})
    assert c.scope == "user"


def test_invalid_concept_rejected(base):
    with pytest.raises(UserConceptError):
        validate_user_concept(base, {"id": "Bad Id!", "name": "x", "type": "enum"})


# ── build_effective_registry ───────────────────────────────────────────────

def test_effective_includes_user_concept(base):
    eff = build_effective_registry(base, [
        {"id": "desk", "name": "Masa", "type": "enum",
         "canonical_values": [{"code": "FX"}]},
    ])
    assert eff.has("desk")
    assert eff.has("currency")          # base preserved
    assert eff.get("desk").scope == "user"


def test_effective_base_wins_collision(base):
    eff = build_effective_registry(base, [
        {"id": "currency", "name": "Hijack", "type": "enum",
         "canonical_values": [{"code": "BTC"}]},
    ])
    # User attempt to redefine currency is dropped — base definition stands.
    assert eff.get("currency").name != "Hijack"


def test_effective_skips_malformed(base):
    eff = build_effective_registry(base, [{"id": "Bad Id!", "type": "enum"}])
    assert len(eff) == len(base)        # malformed user concept ignored


def test_effective_empty_user_list(base):
    eff = build_effective_registry(base, None)
    assert eff.all_ids() == base.all_ids()


# ── promotions ─────────────────────────────────────────────────────────────

def test_record_promotion_appends(tmp_path):
    entry = record_promotion(tmp_path, concept={"id": "desk", "name": "Masa", "type": "enum"},
                             presentation_id="p_1", requested_by="A16438")
    assert entry["status"] == "pending"
    ledger = load_promotions(tmp_path)
    assert len(ledger) == 1
    assert ledger[0]["concept_id"] == "desk"
    assert ledger[0]["presentation_id"] == "p_1"


def test_record_promotion_dedups(tmp_path):
    record_promotion(tmp_path, concept={"id": "desk", "type": "enum", "name": "M"},
                     presentation_id="p_1", requested_by="A1")
    record_promotion(tmp_path, concept={"id": "desk", "type": "enum", "name": "M2"},
                     presentation_id="p_1", requested_by="A1")
    ledger = load_promotions(tmp_path)
    assert len(ledger) == 1               # same (concept, pid) → replaced
    record_promotion(tmp_path, concept={"id": "desk", "type": "enum", "name": "M"},
                     presentation_id="p_2", requested_by="A1")
    assert len(load_promotions(tmp_path)) == 2   # different pid → separate

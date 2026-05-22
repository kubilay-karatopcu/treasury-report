"""Phase 7.a — concept schema validation + value resolution tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentations.concepts.schema import (
    CanonicalValue,
    Concept,
    ConceptFile,
    load_concept_file_from_dict,
)


# ── CanonicalValue ─────────────────────────────────────────────────────────

def test_canonical_value_minimal():
    cv = CanonicalValue(code="USD")
    assert cv.code == "USD"
    assert cv.aliases == []
    assert cv.day_range is None


def test_canonical_value_day_range_ok():
    cv = CanonicalValue(code="1M", day_range=[8, 32])
    assert cv.day_range == [8, 32]


def test_canonical_value_day_range_open_top():
    cv = CanonicalValue(code="10Y+", day_range=[3650, None])
    assert cv.day_range == [3650, None]


def test_canonical_value_day_range_hi_le_lo_rejected():
    with pytest.raises(ValidationError):
        CanonicalValue(code="bad", day_range=[32, 8])


def test_canonical_value_day_range_null_low_rejected():
    with pytest.raises(ValidationError):
        CanonicalValue(code="bad", day_range=[None, 8])


def test_canonical_value_empty_alias_rejected():
    with pytest.raises(ValidationError):
        CanonicalValue(code="USD", aliases=["  "])


# ── Concept type rules ─────────────────────────────────────────────────────

def test_time_concept_rejects_canonical_values():
    with pytest.raises(ValidationError):
        Concept(
            id="as_of_time", name="t", type="time",
            canonical_values=[{"code": "X"}],
        )


def test_non_time_rejects_granularity_default():
    with pytest.raises(ValidationError):
        Concept(id="currency", name="c", type="enum", granularity_default="day")


def test_duplicate_value_codes_rejected():
    with pytest.raises(ValidationError):
        Concept(
            id="currency", name="c", type="enum",
            canonical_values=[{"code": "USD"}, {"code": "USD"}],
        )


def test_alias_collision_across_values_rejected():
    with pytest.raises(ValidationError):
        Concept(
            id="currency", name="c", type="enum",
            canonical_values=[
                {"code": "USD", "aliases": ["Dollar"]},
                {"code": "EUR", "aliases": ["Dollar"]},  # collides
            ],
        )


def test_bad_id_rejected():
    with pytest.raises(ValidationError):
        Concept(id="Bad-Id", name="c", type="enum")  # caps + dash


def test_bad_scope_rejected():
    with pytest.raises(ValidationError):
        Concept(id="currency", name="c", type="enum", scope="department")


# ── Value resolution ───────────────────────────────────────────────────────

@pytest.fixture
def currency() -> Concept:
    return Concept(
        id="currency", name="Para Birimi", type="enum",
        canonical_values=[
            {"code": "USD", "label": "US Doları", "aliases": ["US Dollar", "Dollar"]},
            {"code": "EUR", "label": "Euro", "aliases": ["Avro"]},
        ],
    )


def test_resolve_exact_code(currency):
    assert currency.resolve_value("USD") == "USD"


def test_resolve_case_insensitive_code(currency):
    assert currency.resolve_value("usd") == "USD"


def test_resolve_alias(currency):
    assert currency.resolve_value("US Dollar") == "USD"
    assert currency.resolve_value("dollar") == "USD"  # case-insensitive alias
    assert currency.resolve_value("Avro") == "EUR"


def test_resolve_unknown_returns_none(currency):
    assert currency.resolve_value("ZZZ") is None


def test_resolve_none_returns_none(currency):
    assert currency.resolve_value(None) is None


def test_resolve_empty_alphabet_passthrough():
    """Concept with no canonical_values (e.g. data team hasn't filled it in)
    resolves permissively — preserves Phase 6.5 'accept what the user types'."""
    c = Concept(id="counterparty", name="CP", type="enum")
    assert c.resolve_value("ACME_BANK") == "ACME_BANK"


# ── ConceptFile ────────────────────────────────────────────────────────────

def test_file_stamps_scope_onto_concepts():
    f = load_concept_file_from_dict({
        "version": 1,
        "scope": "dept:treasury",
        "concepts": [{"id": "maturity", "name": "Vade", "type": "bucket"}],
    })
    assert f.concepts[0].scope == "dept:treasury"


def test_file_scope_mismatch_rejected():
    with pytest.raises(ValidationError):
        load_concept_file_from_dict({
            "version": 1,
            "scope": "global",
            "concepts": [{
                "id": "maturity", "name": "Vade", "type": "bucket",
                "scope": "dept:treasury",  # contradicts file scope
            }],
        })


def test_file_duplicate_ids_rejected():
    with pytest.raises(ValidationError):
        load_concept_file_from_dict({
            "version": 1,
            "scope": "global",
            "concepts": [
                {"id": "currency", "name": "c", "type": "enum"},
                {"id": "currency", "name": "c2", "type": "enum"},
            ],
        })

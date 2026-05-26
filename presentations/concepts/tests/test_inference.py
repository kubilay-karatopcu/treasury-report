"""Phase 7.c.1 — deterministic inference pipeline tests."""
from __future__ import annotations

from pathlib import Path

import pytest

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.schema import Concept
from presentations.concepts.inference import ColumnProfile, infer_bindings
from presentations.concepts.inference.pipeline import columns_without_proposal
from presentations.concepts.inference.dtype_filter import (
    dtype_family, is_timestamp, candidate_transform_kinds,
)
from presentations.concepts.inference.regex_matcher import regex_candidates
from presentations.concepts.inference.sample_matcher import sample_overlap, choose_transform


@pytest.fixture(scope="module")
def registry():
    return ConceptRegistry.from_dir(Path(presentations.__file__).parent / "catalog" / "concepts")


# ── dtype filter ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("dtype,fam", [
    ("CHAR(3)", "char"), ("VARCHAR2(20)", "char"), ("NVARCHAR2(8)", "char"),
    ("NUMBER", "numeric"), ("INTEGER", "numeric"), ("NUMBER(10,2)", "numeric"),
    ("DATE", "temporal"), ("TIMESTAMP", "temporal"), ("TIMESTAMP(6)", "temporal"),
    ("BLOB", "other"), ("RAW(16)", "other"),
])
def test_dtype_family(dtype, fam):
    assert dtype_family(dtype) == fam


def test_is_timestamp():
    assert is_timestamp("TIMESTAMP(6)")
    assert not is_timestamp("DATE")


def test_candidate_transforms_time():
    assert candidate_transform_kinds("time", "TIMESTAMP") == ["time_truncation"]
    assert candidate_transform_kinds("time", "DATE") == ["identity"]
    assert candidate_transform_kinds("time", "NUMBER") == []


def test_candidate_transforms_bucket():
    assert candidate_transform_kinds("bucket", "NUMBER") == ["bucket_from_range"]
    assert candidate_transform_kinds("bucket", "VARCHAR2(6)") == ["identity", "map"]


def test_candidate_transforms_enum():
    assert candidate_transform_kinds("enum", "CHAR(3)") == ["identity", "map"]
    assert candidate_transform_kinds("enum", "DATE") == []


# ── regex matcher ──────────────────────────────────────────────────────────

def test_regex_currency():
    assert "currency" in regex_candidates("CCY")
    assert "currency" in regex_candidates("DEAL_CCY")
    assert "currency" not in regex_candidates("CURRENCY_NAME")  # not a hint pattern


def test_regex_time_columns():
    assert regex_candidates("AS_OF_DATE") == ["as_of_time"]
    assert regex_candidates("TRADE_DATE") == ["trade_time"]


def test_regex_maturity():
    assert "maturity" in regex_candidates("MATURITY_DAYS")
    assert "maturity" in regex_candidates("MATURITY_BUCKET")


def test_regex_no_match():
    assert regex_candidates("NET_POSITION") == []


# ── sample matcher ─────────────────────────────────────────────────────────

def test_sample_overlap_exact(registry):
    ccy = registry.get("currency")
    ratio, pairs = sample_overlap(["TRY", "USD", "EUR"], ccy)
    assert ratio == 1.0
    assert pairs == {"TRY": "TRY", "USD": "USD", "EUR": "EUR"}
    assert choose_transform(pairs) == {"kind": "identity"}


def test_sample_overlap_alias_map(registry):
    ccy = registry.get("currency")
    ratio, pairs = sample_overlap(["US Dollar", "USD"], ccy)
    assert ratio == 1.0
    t = choose_transform(pairs)
    assert t["kind"] == "map"
    assert t["pairs"]["US Dollar"] == "USD"


def test_sample_overlap_no_canonical(registry):
    # counterparty has no canonical_values → can't sample-match.
    cp = registry.get("counterparty")
    ratio, pairs = sample_overlap(["ACME", "BETA"], cp)
    assert ratio == 0.0
    assert pairs == {}


def test_sample_overlap_partial(registry):
    ccy = registry.get("currency")
    ratio, _ = sample_overlap(["USD", "ZZZ", "QQQ"], ccy)
    assert ratio == pytest.approx(1 / 3)


# ── end-to-end pipeline ────────────────────────────────────────────────────

def test_infer_bindings_full_table(registry):
    profiles = [
        ColumnProfile("CCY", "CHAR(3)", ["TRY", "USD", "EUR"]),
        ColumnProfile("AS_OF_DATE", "DATE", []),
        ColumnProfile("TRADE_DATE", "TIMESTAMP", []),
        ColumnProfile("MATURITY_DAYS", "NUMBER", [45, 120, 7]),
        ColumnProfile("NET_POSITION", "NUMBER", [1.2, 3.4]),
    ]
    res = infer_bindings(profiles, registry)

    top = {col: (props[0] if props else None) for col, props in res.items()}

    assert top["CCY"].concept == "currency"
    assert top["CCY"].transform["kind"] == "identity"
    assert top["CCY"].confidence == "inferred_sample"
    assert top["CCY"].score == 1.0

    assert top["AS_OF_DATE"].concept == "as_of_time"
    assert top["AS_OF_DATE"].transform["kind"] == "identity"

    assert top["TRADE_DATE"].concept == "trade_time"
    assert top["TRADE_DATE"].transform["kind"] == "time_truncation"

    assert top["MATURITY_DAYS"].concept == "maturity"
    assert top["MATURITY_DAYS"].transform == {
        "kind": "bucket_from_range", "ranges_concept": "maturity"}

    assert top["NET_POSITION"] is None  # nothing matched


def test_columns_without_proposal(registry):
    profiles = [
        ColumnProfile("CCY", "CHAR(3)", ["TRY"]),
        ColumnProfile("MYSTERY", "BLOB", ["x"]),
    ]
    res = infer_bindings(profiles, registry)
    assert columns_without_proposal(res) == ["MYSTERY"]


def test_obviously_bindable_coverage(registry):
    """Spec §11.c acceptance: ≥70% of obviously-bindable columns get a
    proposal at inferred_sample-or-better."""
    profiles = [
        ColumnProfile("CCY", "CHAR(3)", ["TRY", "USD"]),
        ColumnProfile("BRANCH_ID", "VARCHAR2(8)", ["0123"]),
        ColumnProfile("AS_OF_DATE", "DATE", []),
        ColumnProfile("TRADE_DATE", "TIMESTAMP", []),
        ColumnProfile("MATURITY_DAYS", "NUMBER", [30]),
    ]
    res = infer_bindings(profiles, registry)
    placed = sum(1 for props in res.values() if props)
    assert placed / len(profiles) >= 0.7


def test_deterministic(registry):
    profiles = [ColumnProfile("CCY", "CHAR(3)", ["USD", "EUR", "TRY"])]
    runs = [infer_bindings(profiles, registry) for _ in range(10)]
    sigs = [[(p.concept, p.confidence, round(p.score, 4)) for p in r["CCY"]] for r in runs]
    assert all(s == sigs[0] for s in sigs)

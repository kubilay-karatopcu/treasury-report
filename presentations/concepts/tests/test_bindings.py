"""Phase 7.b.1 — column binding schema + BindingCatalog loader tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import presentations
from presentations.concepts.schema import ColumnBinding
from presentations.concepts.bindings import (
    BindingCatalog,
    CachedBindingCatalog,
    TableBindingDoc,
)


# ── Transform discriminated union ──────────────────────────────────────────

def test_identity_transform():
    b = ColumnBinding(concept="currency", column="CCY",
                      transform={"kind": "identity"}, confidence="human_verified")
    assert b.transform.kind == "identity"
    assert b.is_usable


def test_map_transform():
    b = ColumnBinding(concept="currency", column="CURRENCY_NAME",
                      transform={"kind": "map", "pairs": {"US Dollar": "USD"}})
    assert b.transform.pairs["US Dollar"] == "USD"


def test_lookup_transform():
    b = ColumnBinding(concept="branch", column="BRANCH_ID",
                      transform={"kind": "lookup", "dim_table": "DIM_BRANCH",
                                 "dim_key": "BRANCH_ID", "dim_canonical": "BRANCH_CODE"})
    assert b.transform.dim_table == "DIM_BRANCH"


# ── Lookup identifier validation (defense-in-depth) ────────────────────────
# dim_table / dim_key / dim_canonical derleyicide alt-sorguya verbatim gömülür;
# ColumnBinding.column gibi katı Oracle tanımlayıcı doğrulamasına tabi olmalı.

def _lookup(**over):
    base = {"kind": "lookup", "dim_table": "DIM_BRANCH",
            "dim_key": "BRANCH_ID", "dim_canonical": "BRANCH_CODE"}
    base.update(over)
    return ColumnBinding(concept="branch", column="BRANCH_ID", transform=base)


def test_lookup_injection_dim_table_rejected():
    # Eskiden (min_length=1) kabul ediliyordu; artık reddedilmeli.
    with pytest.raises(ValidationError):
        _lookup(dim_table="DIM_BRANCH; DROP TABLE X--")


def test_lookup_lowercase_dim_table_rejected():
    with pytest.raises(ValidationError):
        _lookup(dim_table="dim_branch")


def test_lookup_schema_qualified_dim_table_accepted():
    # dim_table tek noktalı (şema-nitelikli) olabilir.
    b = _lookup(dim_table="DIM.BRANCH")
    assert b.transform.dim_table == "DIM.BRANCH"


def test_lookup_double_dotted_dim_table_rejected():
    with pytest.raises(ValidationError):
        _lookup(dim_table="DIM.BRANCH.X")


def test_lookup_dim_key_validated():
    with pytest.raises(ValidationError):
        _lookup(dim_key="BRANCH_ID; --")
    # Bare dim_key kabul edilir.
    assert _lookup(dim_key="BRANCH_ID").transform.dim_key == "BRANCH_ID"


def test_lookup_dim_canonical_validated():
    with pytest.raises(ValidationError):
        _lookup(dim_canonical="DIM.BRANCH")  # nokta bare kolon için geçersiz
    assert _lookup(dim_canonical="BRANCH_CODE").transform.dim_canonical == "BRANCH_CODE"


def test_bucket_from_range_transform():
    b = ColumnBinding(concept="maturity", column="MATURITY_DAYS",
                      transform={"kind": "bucket_from_range", "ranges_concept": "maturity"})
    assert b.transform.ranges_concept == "maturity"


def test_time_truncation_transform():
    b = ColumnBinding(concept="trade_time", column="TRADE_DATE",
                      transform={"kind": "time_truncation"})
    assert b.transform.kind == "time_truncation"


def test_unknown_transform_kind_rejected():
    with pytest.raises(ValidationError):
        ColumnBinding(concept="x", column="Y", transform={"kind": "wormhole"})


def test_map_requires_pairs():
    with pytest.raises(ValidationError):
        ColumnBinding(concept="x", column="Y", transform={"kind": "map"})


def test_lowercase_column_rejected():
    with pytest.raises(ValidationError):
        ColumnBinding(concept="x", column="lower_col", transform={"kind": "identity"})


# ── Confidence gating ──────────────────────────────────────────────────────

def test_default_confidence_is_not_usable():
    b = ColumnBinding(concept="currency", column="CCY", transform={"kind": "identity"})
    assert b.confidence == "llm_proposed"
    assert not b.is_usable


def test_only_human_verified_is_usable():
    for conf in ("llm_proposed", "inferred_sample", "inferred_regex", "inferred_dtype"):
        b = ColumnBinding(concept="currency", column="CCY", transform={"kind": "identity"},
                          confidence=conf)
        assert not b.is_usable
    b = ColumnBinding(concept="currency", column="CCY", transform={"kind": "identity"},
                      confidence="human_verified")
    assert b.is_usable


# ── TableBindingDoc tolerant parse ─────────────────────────────────────────

def test_table_doc_ignores_extra_fields():
    """A full Phase 6.5.b table doc (columns, descriptions) parses; we read
    only the binding-relevant fields."""
    doc = TableBindingDoc.model_validate({
        "table": "TRD_BRANCH_POSITION",
        "schema": "ODS_TREASURY",
        "description": "ignored",
        "partition_column": "AS_OF_DATE",
        "columns": {"AS_OF_DATE": {"type": "DATE", "filterable": True}},
        "primary_time_concept": "as_of_time",
        "concept_bindings": [
            {"concept": "currency", "column": "CCY",
             "transform": {"kind": "identity"}, "confidence": "human_verified"},
        ],
    })
    assert doc.key() == ("ODS_TREASURY", "TRD_BRANCH_POSITION")
    assert doc.primary_time_concept == "as_of_time"
    assert len(doc.concept_bindings) == 1


# ── BindingCatalog ─────────────────────────────────────────────────────────

@pytest.fixture
def catalog() -> BindingCatalog:
    return BindingCatalog.from_dicts([
        {
            "table": "FX_SWAP_DEALS", "schema": "ODS_TREASURY",
            "primary_time_concept": "trade_time",
            "concept_bindings": [
                {"concept": "currency", "column": "CCY",
                 "transform": {"kind": "identity"}, "confidence": "human_verified"},
                {"concept": "maturity", "column": "MATURITY_DAYS",
                 "transform": {"kind": "bucket_from_range", "ranges_concept": "maturity"},
                 "confidence": "human_verified"},
                # An unverified binding — must NOT be returned by default.
                {"concept": "segment", "column": "SEG",
                 "transform": {"kind": "identity"}, "confidence": "llm_proposed"},
            ],
        },
    ])


def test_get_binding_returns_verified(catalog):
    b = catalog.get_binding("ODS_TREASURY", "FX_SWAP_DEALS", "currency")
    assert b is not None and b.column == "CCY"


def test_get_binding_gates_unverified(catalog):
    # segment is llm_proposed → not returned by default (verified_only).
    assert catalog.get_binding("ODS_TREASURY", "FX_SWAP_DEALS", "segment") is None
    # …but visible when verified_only=False.
    assert catalog.get_binding("ODS_TREASURY", "FX_SWAP_DEALS", "segment",
                               verified_only=False) is not None


def test_get_binding_blind_returns_none(catalog):
    assert catalog.get_binding("ODS_TREASURY", "FX_SWAP_DEALS", "branch") is None


def test_unknown_table_returns_none(catalog):
    assert catalog.get_binding("ODS_RISK", "NOPE", "currency") is None
    assert catalog.get_bindings("ODS_RISK", "NOPE") == []


def test_primary_time_concept(catalog):
    assert catalog.primary_time_concept("ODS_TREASURY", "FX_SWAP_DEALS") == "trade_time"


def test_get_bindings_verified_only(catalog):
    verified = catalog.get_bindings("ODS_TREASURY", "FX_SWAP_DEALS")
    assert {b.concept for b in verified} == {"currency", "maturity"}
    all_b = catalog.get_bindings("ODS_TREASURY", "FX_SWAP_DEALS", verified_only=False)
    assert {b.concept for b in all_b} == {"currency", "maturity", "segment"}


# ── from_dir against the shipped catalog ───────────────────────────────────

def test_from_dir_loads_shipped_catalog():
    d = Path(presentations.__file__).parent / "catalog" / "tables"
    cat = BindingCatalog.from_dir(d)
    assert ("ODS_TREASURY", "FX_SWAP_DEALS") in cat.all_keys()
    b = cat.get_binding("ODS_TREASURY", "TRD_BRANCH_POSITION", "branch")
    assert b is not None and b.transform.kind == "lookup"


def test_cached_catalog_reload(tmp_path):
    import yaml
    sub = tmp_path / "ODS_TREASURY"
    sub.mkdir()
    f = sub / "T.yaml"
    f.write_text(yaml.safe_dump({
        "table": "T", "schema": "ODS_TREASURY",
        "concept_bindings": [{"concept": "currency", "column": "CCY",
                              "transform": {"kind": "identity"},
                              "confidence": "human_verified"}],
    }), encoding="utf-8")
    cat = CachedBindingCatalog(tmp_path, check_interval_s=0.0)
    assert cat.get_binding("ODS_TREASURY", "T", "currency") is not None
    cat.reload()
    assert len(cat) == 1

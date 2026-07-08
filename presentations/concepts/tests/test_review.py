"""Phase 7.c.3 — review queue + approve→YAML + reject persistence."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.inference import ColumnProfile
from presentations.concepts.review import (
    build_queue,
    approve_bindings,
    reject_items,
    load_review_state,
)


@pytest.fixture(scope="module")
def registry():
    return ConceptRegistry.from_dir(Path(presentations.__file__).parent / "catalog" / "concepts")


@pytest.fixture
def profiles():
    return [
        ColumnProfile("CCY", "CHAR(3)", ["TRY", "USD", "EUR"]),
        ColumnProfile("AS_OF_DATE", "DATE", []),
        ColumnProfile("NET_POSITION", "NUMBER", [1.2]),
    ]


# ── build_queue ────────────────────────────────────────────────────────────

def test_queue_lists_proposals(registry, profiles, tmp_path):
    cat = BindingCatalog.empty()
    q = build_queue("ODS_X", "T", profiles, registry, cat, catalog_root=tmp_path)
    cols = {row["column"] for row in q}
    assert "CCY" in cols
    assert "AS_OF_DATE" in cols
    assert "NET_POSITION" not in cols  # no proposal


def test_queue_drops_already_bound(registry, profiles, tmp_path):
    cat = BindingCatalog.from_dicts([{
        "table": "T", "schema": "ODS_X",
        "concept_bindings": [{"concept": "currency", "column": "CCY",
                              "transform": {"kind": "identity"},
                              "confidence": "human_verified"}],
    }])
    q = build_queue("ODS_X", "T", profiles, registry, cat, catalog_root=tmp_path)
    assert "CCY" not in {row["column"] for row in q}   # already bound → drop


def test_queue_drops_rejected(registry, profiles, tmp_path):
    reject_items(tmp_path, "ODS_X", "T", [{"column": "CCY", "concept": "currency"}])
    cat = BindingCatalog.empty()
    q = build_queue("ODS_X", "T", profiles, registry, cat, catalog_root=tmp_path)
    # CCY's only proposal (currency) was rejected → CCY drops out.
    assert "CCY" not in {row["column"] for row in q}


def test_queue_strong_first(registry, profiles, tmp_path):
    cat = BindingCatalog.empty()
    q = build_queue("ODS_X", "T", profiles, registry, cat, catalog_root=tmp_path)
    # CCY (sample 1.0) ranks before AS_OF_DATE (regex 0.5).
    assert q[0]["column"] == "CCY"


# ── approve_bindings ────────────────────────────────────────────────────────

def test_approve_writes_yaml_human_verified(registry, tmp_path):
    n = approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A16438")
    assert n == 1
    path = tmp_path / "tables" / "ODS_X" / "T.yaml"
    assert path.exists()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    cb = doc["concept_bindings"][0]
    assert cb["confidence"] == "human_verified"
    assert cb["verified_by"] == "A16438"
    assert cb["column"] == "CCY"


def test_approve_roundtrips_to_catalog(registry, tmp_path):
    approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A16438")
    cat = BindingCatalog.from_dir(tmp_path / "tables")
    b = cat.get_binding("ODS_X", "T", "currency")
    assert b is not None and b.is_usable and b.column == "CCY"


def test_approve_idempotent_merge(registry, tmp_path):
    approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A1")
    approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A2")
    doc = yaml.safe_load((tmp_path / "tables" / "ODS_X" / "T.yaml").read_text(encoding="utf-8"))
    # Re-approving the same (column, concept) replaces, doesn't duplicate.
    assert len(doc["concept_bindings"]) == 1
    assert doc["concept_bindings"][0]["verified_by"] == "A2"


def test_approve_preserves_existing_fields(registry, tmp_path):
    path = tmp_path / "tables" / "ODS_X" / "T.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "table": "T", "schema": "ODS_X",
        "description": "keep me",
        "columns": {"CCY": {"type": "CHAR(3)"}},
    }), encoding="utf-8")
    approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A1")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["description"] == "keep me"
    assert "columns" in doc
    assert len(doc["concept_bindings"]) == 1


def test_approve_invalid_transform_raises(registry, tmp_path):
    with pytest.raises(Exception):
        approve_bindings(tmp_path, "ODS_X", "T", [
            {"column": "CCY", "concept": "currency", "transform": {"kind": "telepathy"}},
        ], verified_by="A1")


def test_approve_lookup_transform(registry, tmp_path):
    n = approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "BRANCH_ID", "concept": "branch",
         "transform": {"kind": "lookup", "dim_table": "DIM_BRANCH",
                       "dim_key": "BRANCH_ID", "dim_canonical": "BRANCH_CODE"}},
    ], verified_by="A1")
    assert n == 1
    cat = BindingCatalog.from_dir(tmp_path / "tables")
    b = cat.get_binding("ODS_X", "T", "branch")
    assert b.transform.kind == "lookup"


# ── reject_items ────────────────────────────────────────────────────────────

def test_reject_persists_and_dedupes(tmp_path):
    n1 = reject_items(tmp_path, "ODS_X", "T", [{"column": "X", "concept": "currency"}])
    n2 = reject_items(tmp_path, "ODS_X", "T", [{"column": "X", "concept": "currency"}])
    assert n1 == 1 and n2 == 0   # second is a dup → no-op
    state = load_review_state(tmp_path)
    assert len(state["rejected"]) == 1


def test_approve_via_catalog_uses_save_doc(registry, tmp_path):
    """PROD: approve aktif katalog (S3) üzerinden yazmalı — pod-lokal dosyaya
    yazılan onay compiler'a hiç ulaşmıyordu (S3 katalog dosyayı okumaz)."""

    class _FakeS3Catalog:
        def __init__(self):
            self.docs: dict[tuple[str, str], dict] = {}

        def get_raw_doc(self, schema, table):
            return self.docs.get((schema, table))

        def save_doc(self, schema, table, raw):
            self.docs[(schema, table)] = raw

    cat = _FakeS3Catalog()
    n = approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A16438", catalog=cat)
    assert n == 1
    # Dosya sistemine DEĞİL, kataloğa yazıldı.
    assert not (tmp_path / "tables" / "ODS_X" / "T.yaml").exists()
    doc = cat.docs[("ODS_X", "T")]
    cb = doc["concept_bindings"][0]
    assert cb["confidence"] == "human_verified"
    assert cb["column"] == "CCY"


def test_approve_via_catalog_merges_existing(registry, tmp_path):
    class _FakeS3Catalog:
        def __init__(self):
            self.docs = {("ODS_X", "T"): {
                "table": "T", "schema": "ODS_X", "description": "keep me",
                "concept_bindings": [{
                    "concept": "as_of_time", "column": "AS_OF_DATE",
                    "transform": {"kind": "time_truncation"},
                    "confidence": "human_verified",
                }],
            }}

        def get_raw_doc(self, schema, table):
            return self.docs.get((schema, table))

        def save_doc(self, schema, table, raw):
            self.docs[(schema, table)] = raw

    cat = _FakeS3Catalog()
    approve_bindings(tmp_path, "ODS_X", "T", [
        {"column": "CCY", "concept": "currency", "transform": {"kind": "identity"}},
    ], verified_by="A16438", catalog=cat)
    doc = cat.docs[("ODS_X", "T")]
    assert doc["description"] == "keep me"
    assert {b["concept"] for b in doc["concept_bindings"]} == {"as_of_time", "currency"}

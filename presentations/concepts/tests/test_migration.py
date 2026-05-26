"""Phase 7.a — migration 0001 (v0 semantic tags → concept skeletons)."""
from __future__ import annotations

import importlib

import pytest

from presentations.concepts.registry import ConceptRegistry
from presentations.variables.semantic_tags import SEMANTIC_TAGS_V0


# Digit-leading module name → import via importlib.
mig = importlib.import_module("presentations.concepts.migrations.0001_v0_to_v1")


def test_every_v0_tag_becomes_a_concept():
    files = mig.build_skeleton_files()
    reg = ConceptRegistry.from_dicts(files.values())
    assert reg.all_ids() == set(SEMANTIC_TAGS_V0)


def test_concept_types_inferred():
    files = mig.build_skeleton_files()
    reg = ConceptRegistry.from_dicts(files.values())
    assert reg.get("as_of_time").type == "time"
    assert reg.get("trade_time").type == "time"
    assert reg.get("maturity").type == "bucket"
    assert reg.get("other").type == "scalar"
    assert reg.get("currency").type == "enum"


def test_scope_partition():
    files = mig.build_skeleton_files()
    assert files["global"]["scope"] == "global"
    assert files["treasury"]["scope"] == "dept:treasury"
    treasury_ids = {c["id"] for c in files["treasury"]["concepts"]}
    assert "maturity" in treasury_ids
    assert "branch" in treasury_ids
    assert "currency" not in treasury_ids  # currency is global


def test_skeletons_have_empty_canonical_values():
    files = mig.build_skeleton_files()
    by_id = {c["id"]: c for f in files.values() for c in f["concepts"]}
    # enum/bucket skeletons carry an explicit empty list (the "fill me" slot).
    assert by_id["currency"]["canonical_values"] == []
    assert by_id["maturity"]["canonical_values"] == []
    # time concepts have no canonical_values, just a granularity default.
    assert "canonical_values" not in by_id["as_of_time"]
    assert by_id["as_of_time"]["granularity_default"] == "day"


def test_emit_and_reload_roundtrip(tmp_path):
    paths = mig.emit_skeletons(tmp_path)
    assert len(paths) == 2
    reg = ConceptRegistry.from_dir(tmp_path)
    assert reg.all_ids() == set(SEMANTIC_TAGS_V0)


def test_emit_refuses_overwrite_without_force(tmp_path):
    mig.emit_skeletons(tmp_path)
    with pytest.raises(FileExistsError):
        mig.emit_skeletons(tmp_path)
    # force overwrites cleanly.
    mig.emit_skeletons(tmp_path, force=True)


def test_emit_is_idempotent(tmp_path):
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    mig.emit_skeletons(p1)
    mig.emit_skeletons(p2)
    for name in ("global.yaml", "treasury.yaml"):
        assert (p1 / name).read_bytes() == (p2 / name).read_bytes()

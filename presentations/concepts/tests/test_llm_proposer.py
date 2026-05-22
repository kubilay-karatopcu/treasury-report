"""Phase 7.c.2 — LLM fallback proposer tests (fake complete_fn, no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.inference import ColumnProfile
from presentations.concepts.inference.llm_proposer import (
    propose_bindings_llm,
    build_prompt,
    _extract_json,
)


@pytest.fixture(scope="module")
def registry():
    return ConceptRegistry.from_dir(Path(presentations.__file__).parent / "catalog" / "concepts")


def _fake(resp: str):
    return lambda system, user: resp


# ── JSON extraction ────────────────────────────────────────────────────────

def test_extract_plain_json():
    assert _extract_json('{"columns": {}}') == {"columns": {}}


def test_extract_fenced_json():
    assert _extract_json('```json\n{"columns": {"A": []}}\n```') == {"columns": {"A": []}}


def test_extract_prose_wrapped():
    txt = 'Here you go:\n{"columns": {"X": [{"concept": "currency"}]}}\nHope that helps!'
    assert _extract_json(txt)["columns"]["X"][0]["concept"] == "currency"


def test_extract_garbage_returns_empty():
    assert _extract_json("not json at all") == {}
    assert _extract_json("") == {}


# ── Prompt building ────────────────────────────────────────────────────────

def test_prompt_includes_concepts_and_columns(registry):
    profiles = [ColumnProfile("MYSTERY_COL", "VARCHAR2(20)", ["a", "b"])]
    prompt = build_prompt("MY_TABLE", profiles, registry)
    assert "currency" in prompt
    assert "MYSTERY_COL" in prompt
    assert "MY_TABLE" in prompt
    assert "{concepts_block}" not in prompt  # placeholder substituted


# ── Proposal validation ────────────────────────────────────────────────────

def test_valid_llm_proposal(registry):
    profiles = [ColumnProfile("CUR", "VARCHAR2(20)", ["US Dollar"])]
    resp = json.dumps({"columns": {"CUR": [
        {"concept": "currency", "transform": {"kind": "map", "pairs": {"US Dollar": "USD"}},
         "confidence": 0.7, "rationale": "tam ad → ISO"}
    ]}})
    out = propose_bindings_llm("T", profiles, registry, _fake(resp))
    assert "CUR" in out
    p = out["CUR"][0]
    assert p.concept == "currency"
    assert p.confidence == "llm_proposed"
    assert p.score == 0.7
    assert p.stage == "llm"


def test_hallucinated_concept_dropped(registry):
    profiles = [ColumnProfile("X", "VARCHAR2(20)", [])]
    resp = json.dumps({"columns": {"X": [
        {"concept": "wormhole_flux", "transform": {"kind": "identity"}, "confidence": 0.9}
    ]}})
    out = propose_bindings_llm("T", profiles, registry, _fake(resp))
    assert out == {}


def test_invalid_transform_kind_dropped(registry):
    profiles = [ColumnProfile("X", "VARCHAR2(20)", [])]
    resp = json.dumps({"columns": {"X": [
        {"concept": "currency", "transform": {"kind": "telepathy"}, "confidence": 0.9}
    ]}})
    out = propose_bindings_llm("T", profiles, registry, _fake(resp))
    assert out == {}


def test_dtype_incompatible_dropped(registry):
    # currency on a DATE column — dtype rules it out even if the LLM proposes it.
    profiles = [ColumnProfile("WEIRD", "DATE", [])]
    resp = json.dumps({"columns": {"WEIRD": [
        {"concept": "currency", "transform": {"kind": "identity"}, "confidence": 0.9}
    ]}})
    out = propose_bindings_llm("T", profiles, registry, _fake(resp))
    assert out == {}


def test_empty_columns_response(registry):
    profiles = [ColumnProfile("X", "VARCHAR2(20)", [])]
    out = propose_bindings_llm("T", profiles, registry, _fake('{"columns": {}}'))
    assert out == {}


def test_complete_fn_raises_is_swallowed(registry):
    def boom(system, user):
        raise RuntimeError("network down")
    profiles = [ColumnProfile("X", "VARCHAR2(20)", [])]
    out = propose_bindings_llm("T", profiles, registry, boom)
    assert out == {}


def test_no_profiles_noop(registry):
    assert propose_bindings_llm("T", [], registry, _fake('{"columns": {}}')) == {}


# ── FakeLLM.complete integration ───────────────────────────────────────────

def test_fakellm_complete_yields_nothing(registry):
    from presentations.llm import FakeLLM
    fake = FakeLLM()
    profiles = [ColumnProfile("X", "VARCHAR2(20)", ["a"])]
    out = propose_bindings_llm("T", profiles, registry, fake.complete)
    assert out == {}

"""Phase 10D — expert suggestion endpoint + helper tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from prisma_home.experts import LocalExpertStore
from prisma_home.suggest import (
    _expert_corpus,
    _parse_llm_suggestions,
    _tokens,
    keyword_score,
    summarise_manifest,
    suggest_experts,
)


FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "phase_10" / "experts"


@pytest.fixture
def store():
    return LocalExpertStore(base_dir=FIXTURES)


# ── Tokenizer + corpus ──────────────────────────────────────────────────────

class TestTokens:
    def test_strips_short_tokens(self):
        assert _tokens("ve bir kısa") == {"bir", "kısa"}  # 've' too short

    def test_handles_turkish(self):
        assert "şirket" in _tokens("Şirket TL faiz")

    def test_empty_returns_empty_set(self):
        assert _tokens("") == set()


# ── Manifest summary ────────────────────────────────────────────────────────

class TestSummariseManifest:
    def test_extracts_block_titles_nested(self):
        manifest = {
            "meta": {"title": "M"},
            "blocks": [
                {"title": "Bölüm A", "children": [
                    {"title": "KPI 1"},
                    {"title": "Grafik X", "children": [
                        {"title": "Slide 1"},
                    ]},
                ]},
            ],
        }
        s = summarise_manifest(manifest)
        assert s["title"] == "M"
        assert "Bölüm A" in s["block_titles"]
        assert "KPI 1" in s["block_titles"]
        assert "Grafik X" in s["block_titles"]
        assert "Slide 1" in s["block_titles"]

    def test_title_override(self):
        s = summarise_manifest({"meta": {"title": "OldTitle"}}, title="NewTitle")
        assert s["title"] == "NewTitle"

    def test_extracts_basket_tables(self):
        s = summarise_manifest({"basket": [
            {"table": "EDW.X"}, {"table": "EDW.Y"}, "not a dict",
        ]})
        assert s["basket_tables"] == ["EDW.X", "EDW.Y"]


# ── Keyword scoring ─────────────────────────────────────────────────────────

class TestKeywordScore:
    def test_likidite_summary_picks_liq(self, store):
        # A summary about LCR/repo should rank LIQ first.
        summary = {
            "title": "Likidite Brifingi",
            "description": "LCR oranı ve repo maliyetleri",
            "block_titles": ["LCR Trend", "Repo Volume"],
            "basket_tables": [],
        }
        out = keyword_score(summary, store.list_all())
        assert out, "expected at least one suggestion"
        assert out[0]["id"] == "liq"
        assert out[0]["confidence"] == 1.0

    def test_mevduat_summary_picks_dep(self, store):
        summary = {
            "title": "Mevduat Tabanı",
            "description": "Kurumsal mevduat çıkışları",
            "block_titles": ["Mevduat Trend"],
            "basket_tables": [],
        }
        out = keyword_score(summary, store.list_all())
        assert out[0]["id"] == "dep"

    def test_no_matches_returns_empty(self, store):
        summary = {
            "title": "xyz abc def",
            "description": "qrs",
            "block_titles": [],
            "basket_tables": [],
        }
        assert keyword_score(summary, store.list_all()) == []

    def test_corpus_includes_voice_examples(self, store):
        liq = store.load("liq")
        corpus = _expert_corpus(liq)
        # LIQ voice example mentions "repo"
        assert "repo" in corpus


# ── LLM parse tolerance ────────────────────────────────────────────────────

class TestParseLlmSuggestions:
    def test_plain_json(self):
        raw = '{"suggestions": [{"id":"liq", "confidence":0.92, "reason":"r"}]}'
        out = _parse_llm_suggestions(raw)
        assert out == [{"id": "liq", "confidence": 0.92, "reason": "r"}]

    def test_with_code_fence(self):
        raw = '```json\n{"suggestions": [{"id":"dep","confidence":0.5,"reason":"x"}]}\n```'
        out = _parse_llm_suggestions(raw)
        assert out[0]["id"] == "dep"

    def test_garbage_returns_none(self):
        assert _parse_llm_suggestions("not json at all") is None

    def test_missing_suggestions_key_returns_none(self):
        assert _parse_llm_suggestions('{"foo": "bar"}') is None

    def test_clamps_confidence(self):
        raw = '{"suggestions": [{"id":"liq","confidence":1.5,"reason":""}]}'
        assert _parse_llm_suggestions(raw)[0]["confidence"] == 1.0
        raw2 = '{"suggestions": [{"id":"liq","confidence":-0.4,"reason":""}]}'
        assert _parse_llm_suggestions(raw2)[0]["confidence"] == 0.0


# ── End-to-end suggest_experts (no LLM) ────────────────────────────────────

class TestSuggestExpertsKeywordOnly:
    def test_returns_keyword_baseline_when_llm_is_none(self, store):
        manifest = {
            "meta": {"title": "LCR Brifingi"},
            "blocks": [{"title": "Repo Volume", "children": []}],
        }
        out = suggest_experts(
            manifest=manifest, title="", description="",
            expert_store=store, llm_client=None,
        )
        assert out
        assert out[0]["id"] == "liq"
        assert "code" in out[0]


class TestSuggestExpertsLlmRefine:
    def test_llm_results_take_priority(self, store):
        # Stub LLM that always returns dep as the top suggestion.
        class _StubLLM:
            def complete(self, system, user, **kw):
                return ('{"suggestions": ['
                        '{"id":"dep","confidence":0.95,"reason":"llm picked dep"}'
                        ']}')
        manifest = {"meta": {"title": "LCR Brifingi"}}
        out = suggest_experts(
            manifest=manifest, title="", description="",
            expert_store=store, llm_client=_StubLLM(),
        )
        assert out[0]["id"] == "dep"
        assert "llm picked dep" in out[0]["reason"]

    def test_llm_failure_falls_back_to_keywords(self, store):
        class _BrokenLLM:
            def complete(self, system, user, **kw):
                raise RuntimeError("network down")
        manifest = {"meta": {"title": "LCR Brifingi"}}
        out = suggest_experts(
            manifest=manifest, title="", description="",
            expert_store=store, llm_client=_BrokenLLM(),
        )
        # Falls back to keyword scoring; LCR → LIQ.
        assert out[0]["id"] == "liq"

    def test_llm_garbage_falls_back_to_keywords(self, store):
        class _GarbageLLM:
            def complete(self, system, user, **kw):
                return "totally not json"
        manifest = {"meta": {"title": "LCR Brifingi"}}
        out = suggest_experts(
            manifest=manifest, title="", description="",
            expert_store=store, llm_client=_GarbageLLM(),
        )
        assert out[0]["id"] == "liq"

    def test_unknown_ids_from_llm_are_dropped(self, store):
        class _StubLLM:
            def complete(self, system, user, **kw):
                return ('{"suggestions": ['
                        '{"id":"made_up","confidence":0.9,"reason":"r"},'
                        '{"id":"liq","confidence":0.7,"reason":"r2"}'
                        ']}')
        manifest = {"meta": {"title": "LCR Brifingi"}}
        out = suggest_experts(
            manifest=manifest, title="", description="",
            expert_store=store, llm_client=_StubLLM(),
        )
        ids = [s["id"] for s in out]
        assert "made_up" not in ids
        assert "liq" in ids


# ── HTTP /api/experts/suggest ──────────────────────────────────────────────

class TestSuggestEndpoint:
    def test_returns_suggestions_for_liq_themed_manifest(self, auth_client):
        body = {
            "manifest": {
                "meta": {"title": "LCR Brifingi · 25 Mayıs"},
                "blocks": [{"title": "Repo Volume", "children": []}],
            },
            "title": "",
            "description": "",
        }
        rv = auth_client.post(
            "/api/experts/suggest",
            data=json.dumps(body),
            content_type="application/json",
        )
        assert rv.status_code == 200
        payload = rv.get_json()
        assert "suggestions" in payload
        assert payload["suggestions"][0]["id"] == "liq"

    def test_empty_manifest_returns_empty_or_valid_shape(self, auth_client):
        rv = auth_client.post(
            "/api/experts/suggest",
            data=json.dumps({"manifest": {}}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        payload = rv.get_json()
        # Empty manifest has no tokens → no keyword matches → empty list.
        assert payload["suggestions"] == [] or isinstance(payload["suggestions"], list)

    def test_non_dict_manifest_returns_400(self, auth_client):
        rv = auth_client.post(
            "/api/experts/suggest",
            data=json.dumps({"manifest": "not a dict"}),
            content_type="application/json",
        )
        assert rv.status_code == 400

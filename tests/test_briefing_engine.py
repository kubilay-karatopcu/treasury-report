"""Phase 10E — BriefingEngine cache + resolver + LLM fallback + HTTP."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from prisma_home.briefing import (
    BriefingEngine,
    BriefingResult,
    _parse_llm_html,
    _render_raw,
    _resolve_fill_from,
    _strip_unsafe_html,
)
from prisma_home.experts import LocalExpertStore


FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "phase_10" / "experts"


# ── Helpers + stubs ─────────────────────────────────────────────────────────

class _StubSnapshotStore:
    def __init__(self, metas):
        self._metas = list(metas)
    def list_all_meta(self):
        return list(self._metas)


class _StubLLM:
    """Always returns the same well-formed JSON HTML."""
    def __init__(self, html='<p>Stub paragraph.<sup>1</sup></p>'):
        self.html = html
        self.calls = []
    def complete(self, system, user, **kw):
        self.calls.append((system, user))
        return json.dumps({"html": self.html})


class _BrokenLLM:
    def complete(self, *a, **kw):
        raise RuntimeError("network down")


@pytest.fixture
def store():
    return LocalExpertStore(base_dir=FIXTURES)


@pytest.fixture
def liq(store):
    return store.load("liq")


# ── fill_from resolver ─────────────────────────────────────────────────────

class TestResolveFillFrom:
    def test_empty_snapshots_yields_empty(self):
        assert _resolve_fill_from({"kind": "snapshot", "limit": 5}, []) == []

    def test_block_kind_returns_empty(self):
        snaps = [{"snapshot_id": "a"}]
        assert _resolve_fill_from({"kind": "block"}, snaps) == []

    def test_limit_caps_results(self):
        snaps = [{"snapshot_id": f"s{i}"} for i in range(20)]
        out = _resolve_fill_from({"kind": "snapshot", "limit": 3}, snaps)
        assert len(out) == 3

    def test_default_limit(self):
        snaps = [{"snapshot_id": f"s{i}"} for i in range(20)]
        out = _resolve_fill_from({"kind": "snapshot"}, snaps)
        assert len(out) == 6  # default

    def test_role_filter(self):
        snaps = [
            {"snapshot_id": "a", "semantic_role": "daily_pulse"},
            {"snapshot_id": "b", "semantic_role": "other"},
        ]
        out = _resolve_fill_from(
            {"kind": "snapshot", "role": "daily_pulse"}, snaps,
        )
        assert len(out) == 1 and out[0]["snapshot_id"] == "a"

    def test_role_filter_no_match_falls_back_to_full_pool(self):
        # Spec §5.6 friendly degradation: better to show recent than empty.
        snaps = [{"snapshot_id": "a"}, {"snapshot_id": "b"}]
        out = _resolve_fill_from(
            {"kind": "snapshot", "role": "nope"}, snaps,
        )
        assert len(out) == 2


# ── HTML sanitization ──────────────────────────────────────────────────────

class TestStripUnsafeHtml:
    def test_keeps_whitelisted_tags(self):
        html = "<p>hello <strong>world</strong> <em>x</em><sup>1</sup></p>"
        assert _strip_unsafe_html(html) == html

    def test_strips_script(self):
        html = '<p>hi</p><script>alert(1)</script>'
        out = _strip_unsafe_html(html)
        assert "<script>" not in out and "alert(1)" in out

    def test_strips_inline_handlers_via_tag_removal(self):
        html = '<div onclick="evil()">x</div>'
        out = _strip_unsafe_html(html)
        # div is not whitelisted → tag removed, content stays
        assert "<div" not in out and "x" in out


# ── LLM parse ──────────────────────────────────────────────────────────────

class TestParseLlmHtml:
    def test_plain_json(self):
        out = _parse_llm_html('{"html":"<p>x</p>"}')
        assert out == "<p>x</p>"

    def test_code_fence(self):
        out = _parse_llm_html('```json\n{"html":"<p>y</p>"}\n```')
        assert out == "<p>y</p>"

    def test_strips_unsafe_tags(self):
        out = _parse_llm_html('{"html":"<p>ok</p><script>bad</script>"}')
        assert "<script>" not in out
        assert "<p>ok</p>" in out

    def test_garbage_returns_none(self):
        assert _parse_llm_html("totally not json") is None

    def test_empty_html_returns_none(self):
        assert _parse_llm_html('{"html":""}') is None


# ── Raw renderer ───────────────────────────────────────────────────────────

class TestRenderRaw:
    def test_empty_yields_empty_paragraph(self):
        html = _render_raw([], {})
        assert "henüz" in html.lower()

    def test_items_yield_list(self):
        html = _render_raw(
            [{"snapshot_id": "a", "title": "Snap A", "created_at": "2026-05-25T10:00:00"}],
            {},
        )
        assert "<li>Snap A" in html
        assert "2026-05-25" in html


# ── Engine ─────────────────────────────────────────────────────────────────

class TestBriefingEngine:
    def test_renders_one_section_per_recipe_entry(self, store, liq):
        snaps = [
            {"snapshot_id": "s1", "manifest_version": 1, "title": "A",
             "bound_experts": ["liq"], "created_at": "2026-05-25"},
        ]
        eng = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=None,
        )
        result = eng.render_briefing(liq)
        # LIQ recipe has 3 sections (pulse, key_metrics, citations).
        assert len(result.sections) == 3
        assert result.sections[0].id == "pulse"
        assert result.sections[0].llm_paraphrase is True
        assert result.sections[1].llm_paraphrase is False
        # The first section was llm_paraphrase but no LLM → falls back to raw list.
        assert "<li>" in result.sections[0].content_html

    def test_engine_uses_llm_when_available(self, store, liq):
        snaps = [{"snapshot_id": "s1", "title": "T",
                  "bound_experts": ["liq"], "manifest_version": 1}]
        llm = _StubLLM(html='<p>Engine prose.<sup>1</sup></p>')
        eng = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=llm,
        )
        result = eng.render_briefing(liq)
        # pulse section opted into llm_paraphrase
        pulse = next(s for s in result.sections if s.id == "pulse")
        assert "Engine prose" in pulse.content_html
        assert llm.calls, "LLM should have been called for pulse section"

    def test_engine_falls_back_when_llm_broken(self, store, liq):
        snaps = [{"snapshot_id": "s1", "title": "T",
                  "bound_experts": ["liq"], "manifest_version": 1}]
        eng = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=_BrokenLLM(),
        )
        result = eng.render_briefing(liq)
        pulse = next(s for s in result.sections if s.id == "pulse")
        # Falls back to raw listing of the snapshot.
        assert "<li>T" in pulse.content_html

    def test_engine_pulls_metrics_from_static_md(self, store, liq):
        # Even when the engine drives sections, the sidebar metrics come from
        # the static MD's frontmatter (authored metadata).
        eng = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore([]),
            llm_client=None,
        )
        result = eng.render_briefing(liq)
        assert result.metrics, "metrics should come from static MD frontmatter"
        assert any(m["k"] == "LCR" for m in result.metrics)
        assert result.sidebar_eyebrow

    def test_citations_attached_to_section(self, store, liq):
        snaps = [
            {"snapshot_id": "s1", "title": "A",
             "bound_experts": ["liq"], "manifest_version": 1, "created_at": "2026-05-25"},
            {"snapshot_id": "s2", "title": "B",
             "bound_experts": ["liq"], "manifest_version": 1, "created_at": "2026-05-24"},
        ]
        eng = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=None,
        )
        result = eng.render_briefing(liq)
        citations = result.sections[0].citations
        assert len(citations) <= 6
        assert all(c["ref"] in {"s1", "s2"} for c in citations)


# ── Cache ──────────────────────────────────────────────────────────────────

class TestBriefingCache:
    def _engine(self, store, snaps, llm=None):
        return BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=llm,
            default_ttl_seconds=10,
        )

    def test_cache_hit_marks_from_cache(self, store, liq):
        eng = self._engine(store, [])
        r1 = eng.render_briefing(liq)
        assert r1.from_cache is False
        r2 = eng.render_briefing(liq)
        assert r2.from_cache is True
        assert r2.cache_key == r1.cache_key

    def test_cache_invalidated_by_new_snapshot(self, store, liq):
        snaps = [{"snapshot_id": "a", "bound_experts": ["liq"], "manifest_version": 1}]
        eng = self._engine(store, snaps)
        first = eng.render_briefing(liq)
        # Mutate the underlying store — add a new bound snapshot.
        snaps.append({"snapshot_id": "b", "bound_experts": ["liq"], "manifest_version": 1})
        # Re-create the store stub from the same list. Since the engine stub
        # holds a reference, the next render sees both snaps → new cache key.
        eng2 = BriefingEngine(
            expert_store=store,
            snapshot_store=_StubSnapshotStore(snaps),
            llm_client=None,
            default_ttl_seconds=10,
        )
        # Prime cache once.
        eng2.render_briefing(liq)
        # Adding another snap → cache key changes.
        snaps.append({"snapshot_id": "c", "bound_experts": ["liq"], "manifest_version": 1})
        eng2.snapshot_store = _StubSnapshotStore(snaps)
        second = eng2.render_briefing(liq)
        assert second.from_cache is False, (
            "cache key should change when bound snapshot set changes"
        )

    def test_cache_invalidated_by_manifest_version_bump(self, store, liq):
        snaps = [{"snapshot_id": "a", "bound_experts": ["liq"], "manifest_version": 1}]
        eng = self._engine(store, snaps)
        eng.render_briefing(liq)
        # Same snapshot id, bumped version.
        snaps[0]["manifest_version"] = 2
        eng.snapshot_store = _StubSnapshotStore(snaps)
        second = eng.render_briefing(liq)
        assert second.from_cache is False

    def test_invalidate_specific_expert(self, store, liq):
        eng = self._engine(store, [])
        eng.render_briefing(liq)
        assert len(eng._cache) == 1
        n = eng.invalidate(expert_id="liq")
        assert n == 1
        assert eng._cache == {}

    def test_invalidate_all(self, store):
        eng = self._engine(store, [])
        for code in ("liq", "dep", "fnd"):
            eng.render_briefing(store.load(code))
        assert len(eng._cache) == 3
        n = eng.invalidate()
        assert n == 3
        assert eng._cache == {}


# ── HTTP /uzmanlar/<code>/briefing ─────────────────────────────────────────

class TestBriefingHTTP:
    def test_json_endpoint_returns_engine_payload(self, auth_client):
        rv = auth_client.get("/uzmanlar/liq/briefing")
        assert rv.status_code == 200
        payload = rv.get_json()
        # Shape checks.
        assert payload["expert_id"] == "liq"
        assert isinstance(payload["sections"], list)
        assert payload["sections"], "liq has 3 recipe sections"
        for sec in payload["sections"]:
            assert "id" in sec and "title" in sec and "content_html" in sec
        # Sidebar metadata bubbled up from static MD.
        assert payload["metrics"]
        assert payload["rendered_at"]

    def test_second_request_marks_from_cache(self, auth_client, flask_app):
        # First request seeds the cache; second is a hit.
        # Reset the engine's cache so this test is isolated.
        engine = flask_app.config["BRIEFING_ENGINE"]
        engine.invalidate()
        first = auth_client.get("/uzmanlar/liq/briefing").get_json()
        second = auth_client.get("/uzmanlar/liq/briefing").get_json()
        assert first["from_cache"] is False
        assert second["from_cache"] is True
        assert first["cache_key"] == second["cache_key"]

    def test_unknown_expert_returns_404(self, auth_client):
        rv = auth_client.get("/uzmanlar/no-such/briefing")
        assert rv.status_code == 404

    def test_html_page_renders_sections(self, auth_client, flask_app):
        # Ensure HTML route uses the engine output (not the raw static MD).
        flask_app.config["BRIEFING_ENGINE"].invalidate()
        rv = auth_client.get("/uzmanlar/liq")
        body = rv.data.decode("utf-8")
        assert "briefing-section" in body
        # All three LIQ recipe section titles appear.
        for title in ("Bu Sabah", "Anahtar Göstergeler", "Kaynakça"):
            assert title in body

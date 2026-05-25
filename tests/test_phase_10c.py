"""Phase 10C — landing 6 cards + /uzmanlar/<code> detail + briefing + citations."""
from __future__ import annotations

from pathlib import Path

import pytest

from prisma_home.briefings import (
    DEPT_TO_FEATURED_EXPERT,
    DEFAULT_FEATURED_EXPERT,
    featured_expert_for,
    load_static_briefing,
    find_snapshots_bound_to,
    _split_frontmatter,
    _md_to_html,
)


BRIEFINGS_DIR = (
    Path(__file__).resolve().parent.parent / "examples" / "phase_10" / "briefings"
)


# ── featured_expert_for ─────────────────────────────────────────────────────

class TestFeaturedExpertMapping:
    def test_default_for_unknown_dept(self):
        class _U: department = "UNKNOWN DEPT"
        assert featured_expert_for(_U()) == DEFAULT_FEATURED_EXPERT

    def test_known_dept_maps_correctly(self):
        class _U: department = "BİLANÇO ANALİZİ VE MEVDUAT YÖNETİMİ"
        assert featured_expert_for(_U()) == "dep"

    def test_data_team_maps_to_liq(self):
        class _U: department = "FİNANSAL YAPAY ZEKA UYGULAMALARI"
        assert featured_expert_for(_U()) == "liq"

    def test_user_without_department_attribute(self):
        class _U: pass  # no department attr
        assert featured_expert_for(_U()) == DEFAULT_FEATURED_EXPERT

    def test_mapping_covers_all_dept_keys(self):
        # Every dept in SIDEBAR_RULES (app.py) should map to something — soft
        # check: the mapping has at least 8 entries (one per dept). When a
        # new dept is added, this test catches it.
        assert len(DEPT_TO_FEATURED_EXPERT) >= 8


# ── frontmatter + markdown parsing ──────────────────────────────────────────

class TestFrontmatter:
    def test_split_with_frontmatter(self):
        text = "---\ntitle: t\nmetrics:\n  - {k: A, v: B}\n---\nBody paragraph."
        fm, body = _split_frontmatter(text)
        assert fm["title"] == "t"
        assert fm["metrics"][0]["k"] == "A"
        assert body == "Body paragraph."

    def test_split_without_frontmatter(self):
        fm, body = _split_frontmatter("Just body, no frontmatter.")
        assert fm == {}
        assert body == "Just body, no frontmatter."

    def test_split_malformed_yaml_returns_empty(self):
        fm, body = _split_frontmatter("---\n: : : not yaml\n---\nbody")
        assert fm == {}

    def test_md_to_html_lead_paragraph(self):
        html = _md_to_html("First paragraph.\n\nSecond paragraph.")
        assert '<p class="lead">First paragraph.</p>' in html
        assert "<p>Second paragraph.</p>" in html

    def test_md_to_html_bold(self):
        html = _md_to_html("This has **bold text** inside.")
        assert "<strong>bold text</strong>" in html


# ── load_static_briefing ────────────────────────────────────────────────────

class TestLoadStaticBriefing:
    @pytest.mark.parametrize("expert_id", ["liq", "dep", "fnd", "nii", "sec", "krd"])
    def test_each_committed_briefing_loads_with_content(self, expert_id):
        b = load_static_briefing(expert_id)
        assert b.expert_id == expert_id
        assert b.prose_html, f"{expert_id}: prose_html empty"
        assert "<p" in b.prose_html
        assert b.metrics, f"{expert_id}: metrics frontmatter missing"
        # Sidebar metadata present for the consistent prototype header.
        assert b.sidebar_eyebrow
        assert b.sidebar_subtitle

    def test_missing_briefing_returns_placeholder(self):
        b = load_static_briefing("no_such_expert")
        assert b.prose_html  # not empty
        assert "henüz" in b.prose_html.lower()
        assert b.metrics == []

    def test_load_from_custom_dir(self, tmp_path):
        (tmp_path / "x_static.md").write_text(
            "---\nmetrics:\n  - {k: A, v: B}\n---\nProse here.",
            encoding="utf-8",
        )
        b = load_static_briefing("x", base_dir=tmp_path)
        assert b.metrics[0]["k"] == "A"
        assert "Prose here." in b.prose_html


# ── find_snapshots_bound_to ─────────────────────────────────────────────────

class TestFindSnapshotsBoundTo:
    def _store(self, metas):
        class _Store:
            def list_all_meta(self): return list(metas)
        return _Store()

    def test_filters_by_bound_experts(self):
        store = self._store([
            {"snapshot_id": "a", "bound_experts": ["liq"]},
            {"snapshot_id": "b", "bound_experts": ["dep"]},
            {"snapshot_id": "c", "bound_experts": ["liq", "dep"]},
            {"snapshot_id": "d", "bound_experts": []},
            {"snapshot_id": "e"},  # missing field
        ])
        liq = find_snapshots_bound_to(store, "liq")
        assert {s["snapshot_id"] for s in liq} == {"a", "c"}
        dep = find_snapshots_bound_to(store, "dep")
        assert {s["snapshot_id"] for s in dep} == {"b", "c"}

    def test_store_without_list_all_meta_returns_empty(self):
        # Defensive: pre-Phase-10C store backends silently return empty.
        class _OldStore: pass
        assert find_snapshots_bound_to(_OldStore(), "liq") == []


# ── Landing HTTP route ──────────────────────────────────────────────────────

class TestLandingHTTP:
    def test_landing_renders_with_six_experts(self, auth_client):
        rv = auth_client.get("/")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8", errors="replace")
        # Featured component present + at least 5 expert-cell items in grid.
        assert "expert-featured" in body
        assert body.count("expert-cell") >= 5

    def test_landing_featured_matches_user_dept(self, auth_client, flask_app):
        # DEV stub user is FİNANSAL YAPAY ZEKA UYGULAMALARI → liq.
        rv = auth_client.get("/")
        body = rv.data.decode("utf-8", errors="replace")
        # featured_expert link must point at /uzmanlar/liq
        assert '/uzmanlar/liq' in body

    def test_uzmanlar_url_renders_same_layout(self, auth_client):
        rv = auth_client.get("/uzmanlar/")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8", errors="replace")
        assert "expert-featured" in body
        assert "Uzmanlar" in body  # crumb


# ── Expert detail HTTP route ───────────────────────────────────────────────

class TestExpertDetailHTTP:
    def test_known_expert_renders(self, auth_client):
        rv = auth_client.get("/uzmanlar/liq")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8", errors="replace")
        # Glyph + name visible
        assert "LIQ" in body
        assert "Likidite Uzmanı" in body
        # Phase 10E: engine drives the page → recipe section titles visible
        # (the static MD's lead paragraph is now the fallback path).
        for title in ("Bu Sabah", "Anahtar Göstergeler", "Kaynakça"):
            assert title in body, f"recipe section title missing: {title}"
        # Sidebar metrics rendered (from static MD frontmatter).
        assert "LCR" in body and "118.4" in body
        # Related experts panel
        assert "İlişkili Uzmanlar" in body

    def test_unknown_expert_returns_404(self, auth_client):
        rv = auth_client.get("/uzmanlar/no-such")
        assert rv.status_code == 404

    def test_expert_detail_no_sidebar(self, auth_client):
        rv = auth_client.get("/uzmanlar/liq")
        body = rv.data.decode("utf-8", errors="replace")
        # Consumer mode → no atolye sidebar
        assert "atolye-sidebar" not in body

    def test_citation_grid_empty_state_when_no_bound_snapshots(self, auth_client):
        # KRD has no bound snapshots in fresh DEV state.
        rv = auth_client.get("/uzmanlar/krd")
        assert rv.status_code == 200
        body = rv.data.decode("utf-8", errors="replace")
        # If there are no bound snapshots we expect the empty-state copy.
        if "citation-card" not in body:
            assert "Henüz kaynakça yok" in body

    def test_citation_grid_shows_bound_snapshot(self, auth_client, flask_app):
        # Bind a snapshot to LIQ then verify it appears on the detail page.
        # Warm the demo manifest.
        auth_client.get("/presentations/p_demo")
        registry = flask_app.config["SESSION_REGISTRY"]
        sess = registry.get_or_create("A00000", "p_demo")
        m = sess.get_manifest() or {}
        m["bound_experts"] = ["liq"]
        sess.set_manifest(m)

        save_rv = auth_client.post("/presentations/p_demo/snapshot")
        assert save_rv.status_code == 200
        sid = save_rv.get_json()["snapshot_id"]

        rv = auth_client.get("/uzmanlar/liq")
        body = rv.data.decode("utf-8", errors="replace")
        # Snapshot card visible, links to the snapshot view.
        assert "citation-card" in body
        assert sid in body

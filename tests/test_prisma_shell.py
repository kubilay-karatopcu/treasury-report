"""Phase 10A acceptance tests — PRISMA shell + atölye sidebar.

The shell is "chrome only" in this phase: every existing PRISMA page must
render inside the new top bar + (mode-conditional) sidebar without changing
behaviour. These tests verify markup, not visual fidelity.
"""
from __future__ import annotations

import pytest


# ── Landing / consumer mode ──────────────────────────────────────────────────

def test_landing_returns_200_with_brand_mark(auth_client):
    rv = auth_client.get("/")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "brand-mark" in body
    assert "PR" in body and "SM" in body  # PR<i>I</i>SM<a>A</a> shards


def test_landing_excludes_sidebar(auth_client):
    rv = auth_client.get("/")
    assert rv.status_code == 200
    # Consumer mode → no atölye sidebar in the markup.
    assert b"atolye-sidebar" not in rv.data


# ── Atölye home / producer mode ──────────────────────────────────────────────

def test_atolye_home_includes_sidebar(auth_client):
    rv = auth_client.get("/atolye/")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "atolye-sidebar" in body
    # Sidebar groups present.
    assert "Kütüphane" in body
    assert "Pipeline" in body
    assert "Meta" in body


def test_atolye_home_has_in_atolye_class(auth_client):
    rv = auth_client.get("/atolye/")
    body = rv.data.decode("utf-8", errors="replace")
    assert "in-atolye" in body


# ── Sidebar wires straight to existing presentations.* endpoints (Phase 11.wire) ──

def test_sidebar_pipeline_targets_real_endpoints(flask_app):
    """Phase 11.wire: pipeline items point directly at the existing
    presentations.* routes — no prisma_home redirect layer in between."""
    from prisma_home.sidebar import SIDEBAR_GROUPS
    by_key = {it["key"]: it for grp in SIDEBAR_GROUPS for it in grp["items"]}
    assert by_key["kesif"]["route"] == "presentations.atolye_kesif"
    assert by_key["hazirlik"]["route"] == "presentations.hazirlik_new"
    assert by_key["sunum"]["route"] == "presentations.list_presentations"
    assert by_key["bloklar"]["route"] == "presentations.atolye_bloklar"
    assert by_key["surec"]["route"] == "presentations.atolye_surecler"
    # All endpoints must resolve in the live app's URL map.
    with flask_app.test_request_context("/"):
        from flask import url_for
        for key in ("kesif", "hazirlik", "sunum", "bloklar", "surec"):
            assert url_for(by_key[key]["route"])  # raises if unknown endpoint


# ── Existing presentations pages render under the new shell ──────────────────

def test_presentations_list_renders_under_prisma_shell(auth_client):
    rv = auth_client.get("/presentations/")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # Top bar + sidebar both present (presentations list = atolye mode).
    assert "brand-mark" in body
    assert "atolye-sidebar" in body


def test_editor_still_loads(auth_client):
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # The React mount root must still be present so the editor JS bundle
    # can attach. Sidebar markup is also present (mode='atolye') even
    # though the fullscreen mount overlays it visually.
    assert 'id="presentation-root"' in body
    assert "atolye-sidebar" in body


def test_snapshot_view_has_no_sidebar(auth_client):
    """Snapshots are consumer-mode reading material → top bar but no sidebar.

    Creates an ephemeral snapshot from p_demo via the existing POST endpoint,
    then GETs the read-only view. Both responses come from the same
    Flask app under DEV_MODE so the snapshot store is LocalSnapshotStore.
    """
    # Ensure the demo presentation has a manifest in the session registry.
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200

    rv = auth_client.post("/presentations/p_demo/snapshot")
    assert rv.status_code == 200, rv.data
    sid = rv.get_json().get("snapshot_id")
    assert sid, "snapshot endpoint did not return a snapshot_id"

    rv = auth_client.get(f"/presentations/snapshot/{sid}")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "brand-mark" in body
    assert "atolye-sidebar" not in body


# ── Stub pages return "yakında" ──────────────────────────────────────────────

def test_wired_pipeline_pages_render_under_shell(auth_client):
    """Phase 11.wrap: keşif / hazırlık / bloklar / süreçler / tablolar /
    şablonlar now render inside the new PRISMA shell."""
    for path in (
        "/presentations/atolye/kesif",
        "/presentations/atolye/bloklar",
        "/presentations/atolye/surecler",
        "/presentations/atolye/tablolar",
        "/presentations/atolye/sablonlar",
    ):
        rv = auth_client.get(path)
        assert rv.status_code == 200, f"{path} returned {rv.status_code}"
        body = rv.data.decode("utf-8", errors="replace")
        # New shell signature.
        assert "brand-mark" in body, f"{path} missing PRISMA topbar"
        assert "atolye-sidebar" in body, f"{path} missing atölye sidebar"

    # Hazırlık redirects when no presentation is current; follow it.
    rv = auth_client.get("/presentations/hazirlik", follow_redirects=True)
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "brand-mark" in body
    assert "atolye-sidebar" in body


def test_tablolar_page_lists_tables(auth_client):
    """Phase 11.lib: Tablolar page renders catalog tables grouped by schema."""
    rv = auth_client.get("/presentations/atolye/tablolar")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # Page head + dark layout.
    assert "Tablolar" in body
    # At least the empty-state OR a domain-divider for a real schema.
    assert ("domain-divider" in body) or ("Catalog yüklenemedi" in body)


def test_sablonlar_page_shows_bound_snapshots(auth_client, flask_app):
    """Phase 11.lib: Şablonlar page surfaces snapshots bound to ≥1 expert
    as starter templates."""
    # Create a bound snapshot so the page has at least one template.
    auth_client.get("/presentations/p_demo")
    rv = auth_client.post(
        "/presentations/p_demo/snapshot",
        data='{"title": "Tablo Şablon Test", "bound_experts": ["liq"]}',
        content_type="application/json",
    )
    assert rv.status_code == 200
    sid = rv.get_json()["snapshot_id"]

    page = auth_client.get("/presentations/atolye/sablonlar")
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="replace")
    assert "Şablonlar" in body
    assert sid in body, "newly bound snapshot should appear as a template"


def test_sidebar_library_targets_real_endpoints(flask_app):
    """Phase 11.lib: all three Kütüphane items resolve to live endpoints."""
    from prisma_home.sidebar import SIDEBAR_GROUPS
    by_key = {it["key"]: it for grp in SIDEBAR_GROUPS for it in grp["items"]}
    assert by_key["tablolar"]["route"] == "presentations.atolye_tablolar"
    assert by_key["sablonlar"]["route"] == "presentations.atolye_sablonlar"
    with flask_app.test_request_context("/"):
        from flask import url_for
        for key in ("tablolar", "bloklar", "sablonlar"):
            assert url_for(by_key[key]["route"])


# ── Active sidebar item highlighting ────────────────────────────────────────

def test_sidebar_active_key_marks_correct_item(auth_client):
    # Phase 11.wire: bloklar route is now the real /presentations/atolye/bloklar.
    rv = auth_client.get("/presentations/atolye/bloklar")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # Active item gets the "on" class via the partial's `{% if item.active %}on{% endif %}`.
    assert "sidebar-item on" in body
    # The active item should be Bloklar.
    assert 'data-target="bloklar"' in body


# ── Mode toggle reflects current page ───────────────────────────────────────

def test_mode_toggle_reflects_atolye_page(auth_client):
    rv = auth_client.get("/atolye/")
    body = rv.data.decode("utf-8", errors="replace")
    # Producer pill is on, consumer pill is off — order matters in the partial.
    assert 'id="modeProducer"' in body
    assert 'id="modeConsumer"' in body
    # Producer should have "on" class (rendered server-side from `mode == 'atolye'`).
    import re
    producer_match = re.search(r'id="modeProducer"\s+class="([^"]*)"', body)
    assert producer_match, "modeProducer button not found with class attribute"
    assert "on" in producer_match.group(1)


def test_mode_toggle_reflects_landing_page(auth_client):
    rv = auth_client.get("/")
    body = rv.data.decode("utf-8", errors="replace")
    import re
    consumer_match = re.search(r'id="modeConsumer"\s+class="([^"]*)"', body)
    assert consumer_match
    assert "on" in consumer_match.group(1)


def test_snapshot_mode_pill_is_consumer_not_atolye(auth_client):
    """Regression: prisma_shell.js used to force Atölye `on` for any URL that
    started with /presentations/, which incorrectly flipped the pill on
    snapshot views. Server now renders the canonical class via body class
    and the JS reads from there.
    """
    # Create a snapshot off p_demo (warm the demo manifest first).
    auth_client.get("/presentations/p_demo")
    rv = auth_client.post("/presentations/p_demo/snapshot")
    assert rv.status_code == 200
    sid = rv.get_json()["snapshot_id"]

    rv = auth_client.get(f"/presentations/snapshot/{sid}")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")

    # Body must carry the consumer class so the shared JS reads
    # consumer-as-truth. Producer pill must NOT have `on` in its
    # server-rendered class attribute.
    assert 'class="prisma consumer' in body, "snapshot should render in consumer mode"
    import re
    producer_match = re.search(r'id="modeProducer"\s+class="([^"]*)"', body)
    assert producer_match, "modeProducer button missing"
    assert "on" not in producer_match.group(1), (
        "Atölye pill should NOT be `on` for snapshot views"
    )
    consumer_match = re.search(r'id="modeConsumer"\s+class="([^"]*)"', body)
    assert consumer_match
    assert "on" in consumer_match.group(1)


def test_editor_mount_leaves_room_for_topbar(auth_client):
    """Polish: editor mount should sit below the 56px topbar, not overlap it."""
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # The inline style block in editor.html sets top: 56px on the mount.
    assert "top: 56px" in body, "editor mount should leave 56px headroom for topbar"

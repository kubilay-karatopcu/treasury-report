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
    assert "brand-name" in body  # Phase 11.kesif: brand-mark → brand-stack/brand-name
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
    assert "brand-name" in body  # Phase 11.kesif: brand-mark → brand-stack/brand-name
    assert "atolye-sidebar" in body


def test_editor_still_loads(auth_client):
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # The React mount root must still be present so the editor JS bundle
    # can attach. Phase 11.kesif: editor opts out of the PRISMA atölye
    # sidebar because it owns its own header + side panels.
    assert 'id="presentation-root"' in body
    assert "atolye-sidebar" not in body


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
    assert "brand-name" in body  # Phase 11.kesif: brand-mark → brand-stack/brand-name
    assert "atolye-sidebar" not in body


# ── Stub pages return "yakında" ──────────────────────────────────────────────

def test_wired_pipeline_pages_render_under_shell(auth_client):
    """Phase 11.wrap + .workbench: keşif / hazırlık / bloklar / süreçler /
    tablolar / şablonlar all render inside the PRISMA shell."""
    workbench_paths = {
        "/presentations/atolye/kesif",
        "/presentations/atolye/bloklar",
        "/presentations/atolye/surecler",
    }
    library_paths = {
        "/presentations/atolye/tablolar",
        "/presentations/atolye/sablonlar",
    }
    for path in workbench_paths | library_paths:
        rv = auth_client.get(path)
        assert rv.status_code == 200, f"{path} returned {rv.status_code}"
        body = rv.data.decode("utf-8", errors="replace")
        assert "brand-name" in body, f"{path} missing PRISMA topbar"
        # Workbench routes (kesif/bloklar/surecler) all serve the kesif
        # shell which opts out of the PRISMA sidebar (they have their own
        # internal nav). Library routes keep the sidebar.
        if path in workbench_paths or "hazirlik" in path:
            continue
        assert "atolye-sidebar" in body, f"{path} missing atölye sidebar"

    # Hazırlık redirects when no presentation is current; follow it.
    # Phase 11.kesif: Hazırlık opts out of the PRISMA sidebar.
    rv = auth_client.get("/presentations/hazirlik", follow_redirects=True)
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "brand-name" in body


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
    # Phase 11.workbench: bloklar lives under the kesif shell (no PRISMA
    # sidebar). The Tablolar / Şablonlar library pages still render the
    # PRISMA sidebar, so we test active-key highlighting there instead.
    rv = auth_client.get("/presentations/atolye/tablolar")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert "sidebar-item on" in body
    assert 'data-target="tablolar"' in body


# ── Mode toggle reflects current page ───────────────────────────────────────

def test_mode_back_on_atolye_home_points_at_masa(auth_client):
    """Phase 11.topbar-v3: a single contextual back button.
    On the Atölye landing the chip reads "← Masaya dön" and links to /."""
    rv = auth_client.get("/atolye/")
    body = rv.data.decode("utf-8", errors="replace")
    import re
    m = re.search(r'<a[^>]+id="modeBack"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', body)
    assert m, "modeBack chip should render in the topbar"
    href, label = m.group(1), m.group(2).strip()
    assert href.rstrip("/") in ("", "/"), f"chip should link to /, got {href!r}"
    assert "Masaya" in label, f"chip label should mention Masa, got {label!r}"


def test_mode_back_on_masa_points_at_atolye(auth_client):
    """On the consumer landing the chip reads "Atölyeye geç →" → /atolye/."""
    rv = auth_client.get("/")
    body = rv.data.decode("utf-8", errors="replace")
    import re
    m = re.search(r'<a[^>]+id="modeBack"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', body)
    assert m, "modeBack chip should render"
    href, label = m.group(1), m.group(2).strip()
    assert href.rstrip("/") == "/atolye"
    assert "Atöly" in label


def test_mode_back_on_atolye_subpage_points_at_atolye_home(auth_client):
    """Inside any Atölye sub-page (Keşif/Hazırlık/Bloklar/etc) the chip reads
    "← Atölyeye dön" so the user can pop back up to Atölye Ana, not all the
    way out to Masa."""
    rv = auth_client.get("/presentations/atolye/kesif")
    body = rv.data.decode("utf-8", errors="replace")
    import re
    m = re.search(r'<a[^>]+id="modeBack"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', body)
    assert m, "modeBack chip should render on Keşif sub-page"
    href, label = m.group(1), m.group(2).strip()
    assert href.rstrip("/") == "/atolye"
    assert "Atöly" in label


def test_snapshot_mode_back_points_at_atolye(auth_client):
    """Snapshot view is consumer mode → the chip suggests jumping to Atölye."""
    auth_client.get("/presentations/p_demo")
    rv = auth_client.post("/presentations/p_demo/snapshot")
    assert rv.status_code == 200
    sid = rv.get_json()["snapshot_id"]

    rv = auth_client.get(f"/presentations/snapshot/{sid}")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    assert 'class="prisma consumer' in body, "snapshot should render in consumer mode"
    import re
    m = re.search(r'<a[^>]+id="modeBack"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', body)
    assert m
    href, label = m.group(1), m.group(2).strip()
    assert href.rstrip("/") == "/atolye"
    assert "Atöly" in label


def test_editor_mount_leaves_room_for_topbar(auth_client):
    """Polish: editor mount should sit below the 56px topbar, not overlap it."""
    rv = auth_client.get("/presentations/p_demo")
    assert rv.status_code == 200
    body = rv.data.decode("utf-8", errors="replace")
    # The inline style block in editor.html sets top: 56px on the mount.
    assert "top: 56px" in body, "editor mount should leave 56px headroom for topbar"


# ── Theme toggle (Phase 12.light) ──────────────────────────────────────────

def test_theme_toggle_button_on_consumer(auth_client):
    """The sun/moon theme toggle should render on the consumer landing."""
    rv = auth_client.get("/")
    body = rv.data.decode("utf-8", errors="replace")
    assert 'id="themeToggle"' in body, "theme toggle button missing from topbar"
    # Both icon SVGs ship; CSS toggles visibility based on data-theme.
    assert "theme-toggle__icon--moon" in body
    assert "theme-toggle__icon--sun" in body


def test_theme_toggle_button_on_atolye(auth_client):
    """The toggle should also render on the producer (Atölye) side."""
    rv = auth_client.get("/atolye/")
    body = rv.data.decode("utf-8", errors="replace")
    assert 'id="themeToggle"' in body


def test_theme_flash_prevention_script_present(auth_client):
    """The inline <head> script must apply data-theme BEFORE stylesheets
    load so the first paint matches the saved theme (no FOUC)."""
    rv = auth_client.get("/")
    body = rv.data.decode("utf-8", errors="replace")
    # The script reads localStorage and sets data-theme on documentElement.
    assert "prisma-theme" in body, "flash-prevention script missing"
    assert "data-theme" in body

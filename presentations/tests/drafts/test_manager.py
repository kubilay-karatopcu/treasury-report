"""Phase 9.a — DraftManager: create, persist, promote, garbage-collect."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from presentations.drafts.manager import (
    DraftManager,
    DraftRecord,
    is_draft_pid,
)


# ── Creation ─────────────────────────────────────────────────────────────


def test_create_returns_draft_record(manager):
    r = manager.get_or_create_current("A16438")
    assert isinstance(r, DraftRecord)
    assert is_draft_pid(r.pid)
    assert r.sicil == "A16438"
    assert r.basket_count == 0


def test_create_is_idempotent(manager):
    r1 = manager.get_or_create_current("A16438")
    r2 = manager.get_or_create_current("A16438")
    assert r1.pid == r2.pid


def test_create_writes_manifest_to_session(manager, session_registry):
    r = manager.get_or_create_current("A16438")
    sess = session_registry.get_or_create("A16438", r.pid)
    manifest = sess.get_manifest()
    assert manifest is not None
    assert manifest["id"] == r.pid
    assert manifest.get("is_draft") is True
    assert manifest.get("basket") == []


def test_drafts_isolated_per_user(manager):
    a = manager.get_or_create_current("A16438")
    b = manager.get_or_create_current("B99999")
    assert a.pid != b.pid
    assert a.sicil == "A16438"
    assert b.sicil == "B99999"


# ── Basket count updates ─────────────────────────────────────────────────


def test_update_basket_count_persists(manager):
    r = manager.get_or_create_current("A16438")
    manager.update_basket_count("A16438", r.pid, 3)
    drafts = manager.list_drafts("A16438")
    rec = next(d for d in drafts if d.pid == r.pid)
    assert rec.basket_count == 3


# ── Promotion ────────────────────────────────────────────────────────────


def test_promote_returns_real_pid(manager, session_registry):
    r = manager.get_or_create_current("A16438")
    # Add an item to the draft basket so it survives promotion.
    sess = session_registry.get_or_create("A16438", r.pid)
    m = sess.get_manifest()
    m["basket"] = [{"table": "EDW.DEPOSITS_DAILY", "columns": ["BRANCH_CODE"], "row_filter": None}]
    sess.set_manifest(m)

    new_pid = manager.promote("A16438", r.pid, title="Sunum 1")
    assert new_pid.startswith("p_")
    assert not is_draft_pid(new_pid)

    new_sess = session_registry.get_or_create("A16438", new_pid)
    promoted = new_sess.get_manifest()
    assert promoted["id"] == new_pid
    assert promoted["basket"] == m["basket"]
    assert promoted["meta"]["title"] == "Sunum 1"
    # ``is_draft`` should not survive — the promoted record is a real presentation.
    assert promoted.get("is_draft") in (None, False) or not promoted.get("is_draft")


def test_promote_clears_current_pointer(manager):
    r = manager.get_or_create_current("A16438")
    manager.promote("A16438", r.pid)
    # A subsequent get_or_create_current should mint a new draft.
    r2 = manager.get_or_create_current("A16438")
    assert r2.pid != r.pid


def test_promote_invalid_pid_raises(manager):
    import pytest
    from presentations.drafts.manager import DraftError
    with pytest.raises(DraftError):
        manager.promote("A16438", "p_already_real")


# ── Discard ──────────────────────────────────────────────────────────────


def test_discard_removes_draft(manager, session_registry):
    r = manager.get_or_create_current("A16438")
    manager.discard("A16438", r.pid)
    sess = session_registry.get_or_create("A16438", r.pid)
    assert sess.get_manifest() is None
    # Pref pointer cleared.
    drafts = manager.list_drafts("A16438")
    assert not any(d.pid == r.pid for d in drafts)


# ── GC ───────────────────────────────────────────────────────────────────


def _backdate_draft(manager, sicil, pid, days_ago):
    """Rewrite the created_at field in prefs to simulate an old draft."""
    prefs = manager._load_prefs(sicil)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_ago)
    for d in prefs.get("drafts", []):
        if d.get("pid") == pid:
            d["created_at"] = cutoff.isoformat()
    manager._save_prefs(sicil, prefs)


def test_gc_drops_empty_old_drafts(manager):
    r = manager.get_or_create_current("A16438")
    _backdate_draft(manager, "A16438", r.pid, days_ago=30)
    drafts = manager.list_drafts("A16438")
    assert not any(d.pid == r.pid for d in drafts)


def test_gc_keeps_drafts_with_basket_items(manager):
    r = manager.get_or_create_current("A16438")
    manager.update_basket_count("A16438", r.pid, 2)
    _backdate_draft(manager, "A16438", r.pid, days_ago=30)
    drafts = manager.list_drafts("A16438")
    # Non-empty basket → kept even though old.
    assert any(d.pid == r.pid for d in drafts)


def test_gc_keeps_recent_empty_drafts(manager):
    r = manager.get_or_create_current("A16438")
    drafts = manager.list_drafts("A16438")
    assert any(d.pid == r.pid for d in drafts)

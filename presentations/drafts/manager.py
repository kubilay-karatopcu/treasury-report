"""Phase 9.a — Draft presentation manager.

A draft pid looks like ``draft_<sicil>_<unix_ts>``. While the user browses
Keşif, basket adds/removes route to the draft's manifest (via the existing
``SessionRegistry`` storage — drafts are just manifests with a special
``id`` prefix). On "Hazırlık'a geç" the draft is promoted: a real pid
(``p_<token>``) is minted, the manifest is rewritten with the new id, and
the draft record is dropped from the user's prefs.

Garbage collection: drafts older than 7 days with empty baskets are deleted
on next list / promote. Drafts with non-empty baskets are preserved
indefinitely — the user may come back to a half-built basket.

Storage:

- The draft manifest lives at the same S3 key the SessionRegistry uses
  for any other presentation: ``presentations/<sicil>/draft_<sicil>_<ts>/manifest.json``.
- The "current draft pid for user" pointer lives in a small per-user
  prefs file at ``presentations/<sicil>/_drafts.json`` (see :meth:`_load_prefs`).

Failure handling: every IO operation is wrapped; a failure logs and
returns sensible defaults. This module is on the request path and must
never crash the page.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────


DRAFT_PID_PREFIX = "draft_"
PROMOTED_PID_PREFIX = "p_"
DEFAULT_GC_DAYS = 7


def _prefs_key(sicil: str) -> str:
    """S3 key for the user's drafts pointer file."""
    return f"presentations/{sicil}/_drafts.json"


def is_draft_pid(pid: str) -> bool:
    return pid.startswith(DRAFT_PID_PREFIX)


def make_draft_pid(sicil: str) -> str:
    """Mint a fresh draft pid. Time component is monotonic-ish (seconds since
    epoch), entropy comes from a short token to avoid same-second collisions."""
    suffix = secrets.token_hex(3)
    return f"{DRAFT_PID_PREFIX}{sicil}_{int(time.time())}_{suffix}"


# ── Errors ────────────────────────────────────────────────────────────────


class DraftError(RuntimeError):
    pass


# ── Records ───────────────────────────────────────────────────────────────


@dataclass
class DraftRecord:
    pid: str
    sicil: str
    created_at: str
    basket_count: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DraftRecord":
        return cls(
            pid=d.get("pid") or "",
            sicil=d.get("sicil") or "",
            created_at=d.get("created_at") or "",
            basket_count=int(d.get("basket_count") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "sicil": self.sicil,
            "created_at": self.created_at,
            "basket_count": self.basket_count,
        }


# ── Manager ───────────────────────────────────────────────────────────────


class DraftManager:
    """Owns the draft lifecycle: create, find current, promote, GC.

    Constructor takes the :class:`SessionRegistry` (for manifest storage)
    and the data client (for the per-user prefs file). Both come from
    Flask config in production.
    """

    def __init__(
        self,
        *,
        session_registry,
        data_client,
        gc_days: int = DEFAULT_GC_DAYS,
    ):
        self._sessions = session_registry
        self._dc = data_client
        self._gc_days = gc_days

    # ── Prefs IO ──────────────────────────────────────────────────────────

    def _load_prefs(self, sicil: str) -> dict[str, Any]:
        """Read the per-user drafts pointer. Returns an empty stub on any
        miss / failure — drafts are not load-bearing for the rest of the
        app, so missing prefs are a normal state.
        """
        if self._dc is None:
            return {"current": None, "drafts": []}
        try:
            return self._dc.read_json(_prefs_key(sicil))
        except FileNotFoundError:
            return {"current": None, "drafts": []}
        except Exception:
            log.warning("drafts: prefs load failed for %s", sicil, exc_info=True)
            return {"current": None, "drafts": []}

    def _save_prefs(self, sicil: str, prefs: dict[str, Any]) -> None:
        if self._dc is None:
            return
        try:
            body = json.dumps(prefs, ensure_ascii=False).encode("utf-8")
            self._dc._upload_bytes(
                _prefs_key(sicil), body, content_type="application/json"
            )
        except Exception:
            log.warning("drafts: prefs save failed for %s", sicil, exc_info=True)

    # ── Public API ────────────────────────────────────────────────────────

    def get_or_create_current(self, sicil: str) -> DraftRecord:
        """Return the user's current draft pid, creating one on first call.

        Idempotent: subsequent calls return the same pid until the user
        promotes or the draft is GC'd.
        """
        prefs = self._load_prefs(sicil)
        current = prefs.get("current")
        if current and self._draft_exists(sicil, current):
            return self._record_from_prefs(prefs, current) or self._fresh_record(sicil, current)

        # Create a new draft.
        new_pid = make_draft_pid(sicil)
        now = datetime.now(timezone.utc).isoformat()
        manifest = self._empty_draft_manifest(new_pid, sicil, now)

        sess = self._sessions.get_or_create(sicil, new_pid)
        sess.set_manifest(manifest)

        record = DraftRecord(pid=new_pid, sicil=sicil, created_at=now, basket_count=0)
        prefs["current"] = new_pid
        prefs.setdefault("drafts", [])
        prefs["drafts"] = [d for d in prefs["drafts"] if d.get("pid") != new_pid]
        prefs["drafts"].append(record.to_dict())
        self._save_prefs(sicil, prefs)
        return record

    def list_drafts(self, sicil: str) -> list[DraftRecord]:
        """Return all known drafts for the user (after GC)."""
        prefs = self._load_prefs(sicil)
        records = [DraftRecord.from_dict(d) for d in (prefs.get("drafts") or [])]
        records = self._gc(sicil, prefs, records)
        return records

    def update_basket_count(self, sicil: str, pid: str, count: int) -> None:
        """Track how many items the draft holds. Drives both UX badges and
        the GC decision (empty drafts GC sooner)."""
        prefs = self._load_prefs(sicil)
        for d in prefs.get("drafts") or []:
            if d.get("pid") == pid:
                d["basket_count"] = int(count)
                break
        else:
            # Not in prefs yet — register it. Happens when the user adds
            # to basket on a draft created in a previous process restart.
            prefs.setdefault("drafts", []).append(DraftRecord(
                pid=pid, sicil=sicil,
                created_at=datetime.now(timezone.utc).isoformat(),
                basket_count=int(count),
            ).to_dict())
        self._save_prefs(sicil, prefs)

    def promote(self, sicil: str, draft_pid: str, *, title: str | None = None) -> str:
        """Promote a draft pid to a real presentation. Returns the new pid.

        Side effects:
          - Writes a new manifest at the real pid carrying the draft's basket.
          - Marks the draft as 'promoted' in prefs (kept for audit but
            excluded from list_drafts).
          - If the promoted draft was the user's current, clears it.
        """
        if not is_draft_pid(draft_pid):
            raise DraftError(f"{draft_pid} is not a draft pid")

        sess = self._sessions.get_or_create(sicil, draft_pid)
        draft_manifest = sess.get_manifest()
        if draft_manifest is None:
            raise DraftError(f"draft {draft_pid} has no manifest")

        new_pid = f"{PROMOTED_PID_PREFIX}{secrets.token_urlsafe(8)}"
        now = datetime.now(timezone.utc).isoformat()
        promoted = dict(draft_manifest)
        promoted["id"] = new_pid
        promoted["version"] = 1
        promoted["created_at"] = now
        promoted["updated_at"] = now
        promoted["owner_id"] = sicil
        # Drop the draft marker — the promoted record is a real presentation.
        promoted.pop("is_draft", None)
        meta = dict(promoted.get("meta") or {})
        if title:
            meta["title"] = title
        elif not meta.get("title"):
            meta["title"] = "Yeni Sunum"
        promoted["meta"] = meta

        new_sess = self._sessions.get_or_create(sicil, new_pid)
        new_sess.set_manifest(promoted)

        # Update prefs: drop the draft from active list, clear current.
        prefs = self._load_prefs(sicil)
        prefs["drafts"] = [d for d in prefs.get("drafts") or [] if d.get("pid") != draft_pid]
        if prefs.get("current") == draft_pid:
            prefs["current"] = None
        prefs.setdefault("promoted", []).append({
            "draft_pid": draft_pid,
            "presentation_id": new_pid,
            "promoted_at": now,
        })
        self._save_prefs(sicil, prefs)

        # Best-effort: drop the draft's manifest from S3. Failure is non-
        # fatal — the GC pass will pick it up later.
        try:
            sess.delete_manifest()
        except Exception:
            log.info("drafts: post-promote delete failed for %s (non-fatal)", draft_pid)

        return new_pid

    def discard(self, sicil: str, draft_pid: str) -> None:
        """Drop a draft entirely. Used by the GC and by a manual "X" button
        on the drafts list (UI not in 9.a but the API supports it)."""
        if not is_draft_pid(draft_pid):
            return
        try:
            sess = self._sessions.get_or_create(sicil, draft_pid)
            sess.delete_manifest()
        except Exception:
            log.info("drafts: discard delete_manifest failed for %s", draft_pid)
        prefs = self._load_prefs(sicil)
        prefs["drafts"] = [d for d in prefs.get("drafts") or [] if d.get("pid") != draft_pid]
        if prefs.get("current") == draft_pid:
            prefs["current"] = None
        self._save_prefs(sicil, prefs)

    # ── GC ────────────────────────────────────────────────────────────────

    def _gc(
        self,
        sicil: str,
        prefs: dict[str, Any],
        records: list[DraftRecord],
    ) -> list[DraftRecord]:
        """Drop drafts older than ``gc_days`` *with empty baskets*. Drafts
        carrying items are preserved indefinitely (the user may resume)."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._gc_days)
        kept: list[DraftRecord] = []
        dropped: list[str] = []
        for r in records:
            try:
                created = datetime.fromisoformat(r.created_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except Exception:
                # Bad timestamp — drop only if basket is also empty.
                if r.basket_count == 0:
                    dropped.append(r.pid)
                    continue
                kept.append(r)
                continue
            if created < cutoff and r.basket_count == 0:
                dropped.append(r.pid)
                continue
            kept.append(r)

        if dropped:
            for pid in dropped:
                try:
                    sess = self._sessions.get_or_create(sicil, pid)
                    sess.delete_manifest()
                except Exception:
                    log.info("drafts: GC delete_manifest failed for %s", pid)
            prefs["drafts"] = [d.to_dict() for d in kept]
            if prefs.get("current") in dropped:
                prefs["current"] = None
            self._save_prefs(sicil, prefs)
        return kept

    # ── Helpers ───────────────────────────────────────────────────────────

    def _draft_exists(self, sicil: str, pid: str) -> bool:
        try:
            sess = self._sessions.get_or_create(sicil, pid)
            return sess.get_manifest() is not None
        except Exception:
            return False

    def _record_from_prefs(self, prefs: dict[str, Any], pid: str) -> DraftRecord | None:
        for d in prefs.get("drafts") or []:
            if d.get("pid") == pid:
                return DraftRecord.from_dict(d)
        return None

    def _fresh_record(self, sicil: str, pid: str) -> DraftRecord:
        return DraftRecord(
            pid=pid, sicil=sicil,
            created_at=datetime.now(timezone.utc).isoformat(),
            basket_count=0,
        )

    @staticmethod
    def _empty_draft_manifest(pid: str, sicil: str, now: str) -> dict[str, Any]:
        return {
            "id": pid,
            "version": 1,
            "owner_id": sicil,
            "created_at": now,
            "updated_at": now,
            "meta": {
                "title": "Taslak",
                "eyebrow": "Atölye / Keşif",
                "date": "",
                "author_label": sicil,
            },
            "basket": [],
            "blocks": [],
            "is_draft": True,
        }

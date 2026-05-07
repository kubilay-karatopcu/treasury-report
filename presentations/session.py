"""
Per-(user, presentation) session manager.

Each session owns:
- A DuckDB connection (file-backed under the session dir)
- The cached manifest (also persisted as JSON for restart durability)
- A timestamp for idle cleanup
- Knowledge of which basket items are currently loaded into DuckDB

Sessions are best-effort. Pod/process restart wipes the in-memory registry but
the on-disk manifest.json + session.duckdb survive — the next request rehydrates.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import duckdb

from presentations.duck import populate_basket, list_views

log = logging.getLogger(__name__)


# ── Single session ────────────────────────────────────────────────────────────

@dataclass
class PresentationSession:
    user_id: str
    presentation_id: str
    base_dir: Path

    _conn: Optional[duckdb.DuckDBPyConnection] = field(default=None, init=False)
    _manifest: Optional[dict] = field(default=None, init=False)
    _last_basket_signature: Optional[str] = field(default=None, init=False)
    _last_used: float = field(default_factory=time.time, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def session_dir(self) -> Path:
        return self.base_dir / self.user_id / self.presentation_id

    @property
    def duckdb_path(self) -> Path:
        return self.session_dir / "session.duckdb"

    @property
    def manifest_path(self) -> Path:
        return self.session_dir / "manifest.json"

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def touch(self) -> None:
        self._last_used = time.time()

    def get_duck_conn(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            if self._conn is None:
                self.session_dir.mkdir(parents=True, exist_ok=True)
                self._conn = duckdb.connect(str(self.duckdb_path))
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── Manifest ─────────────────────────────────────────────────────────────

    def get_manifest(self, fallback: Optional[dict] = None) -> Optional[dict]:
        if self._manifest is not None:
            return self._manifest
        if self.manifest_path.exists():
            self._manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return self._manifest
        if fallback is not None:
            self.set_manifest(fallback)
            return self._manifest
        return None

    def set_manifest(self, manifest: dict) -> None:
        with self._lock:
            self._manifest = manifest
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        log.info(
            "session: persisted manifest %s/%s v%s",
            self.user_id, self.presentation_id, manifest.get("version"),
        )

    # ── Basket → DuckDB ──────────────────────────────────────────────────────

    def basket_signature(self, basket: list[dict]) -> str:
        """Stable JSON hash of the basket for change detection."""
        return json.dumps(basket, sort_keys=True, ensure_ascii=False)

    def needs_refetch(self, basket: list[dict]) -> bool:
        """True if the current DuckDB state doesn't match the basket signature."""
        sig = self.basket_signature(basket)
        return sig != self._last_basket_signature

    def fetch_basket(self, dc, basket: list[dict]) -> dict:
        """Run Oracle queries for every basket item, register them in DuckDB."""
        conn = self.get_duck_conn()
        loaded = populate_basket(dc, conn, basket)
        self._last_basket_signature = self.basket_signature(basket)
        self.touch()
        log.info(
            "session: basket fetched for %s/%s — %d views",
            self.user_id, self.presentation_id, len(loaded),
        )
        return loaded

    def loaded_views(self) -> list[str]:
        if self._conn is None:
            return []
        return list_views(self._conn)


# ── Registry (app-singleton) ─────────────────────────────────────────────────

class SessionRegistry:
    """Holds active PresentationSession instances. Thread-safe."""

    def __init__(self, base_dir: str | Path, idle_timeout: int = 1800):
        self.base_dir = Path(base_dir)
        self.idle_timeout = idle_timeout
        self._sessions: dict[tuple[str, str], PresentationSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, user_id: str, presentation_id: str) -> PresentationSession:
        key = (user_id, presentation_id)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                sess = PresentationSession(
                    user_id=user_id,
                    presentation_id=presentation_id,
                    base_dir=self.base_dir,
                )
                self._sessions[key] = sess
            sess.touch()
            return sess

    def cleanup_idle(self, idle_seconds: Optional[int] = None) -> int:
        """Close sessions idle for longer than `idle_seconds`. Returns count closed."""
        cutoff = time.time() - (idle_seconds if idle_seconds is not None else self.idle_timeout)
        closed = 0
        with self._lock:
            for key in list(self._sessions.keys()):
                sess = self._sessions[key]
                if sess._last_used < cutoff:
                    sess.close()
                    del self._sessions[key]
                    closed += 1
        if closed:
            log.info("session: cleaned up %d idle sessions", closed)
        return closed

    def close_all(self) -> None:
        with self._lock:
            for sess in self._sessions.values():
                sess.close()
            self._sessions.clear()

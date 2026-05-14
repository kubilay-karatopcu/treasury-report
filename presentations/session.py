"""
Per-(user, presentation) session manager — S3-backed manifest, pod-local DuckDB.

Two storage layers:
  - Manifest (durable):  S3 at `prisma-treasury/presentations/<sicil>/<pid>/manifest.json`
  - DuckDB cache (volatile): pod-local at `<tempdir>/prisma-treasury-duck/<sicil>/<pid>/session.duckdb`

Pod restart wipes DuckDB but the manifest survives. The next request rehydrates
the manifest from S3 and basket is re-fetched from Oracle on demand.

This keeps the working set (DuckDB binary file) local for fast queries while
keeping the source-of-truth (manifest) in shared storage.
"""
from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import duckdb

from presentations.duck import populate_basket, list_views

log = logging.getLogger(__name__)


# ── S3 path conventions ──────────────────────────────────────────────────────

S3_PREFIX = "prisma-treasury/presentations"


def _manifest_key(user_id: str, presentation_id: str) -> str:
    return f"{S3_PREFIX}/{user_id}/{presentation_id}/manifest.json"


def _user_prefix(user_id: str) -> str:
    return f"{S3_PREFIX}/{user_id}/"


# ── Single session ───────────────────────────────────────────────────────────

@dataclass
class PresentationSession:
    user_id: str
    presentation_id: str
    duck_base_dir: Path           # pod-local tempdir for DuckDB files
    dc: object                    # DataClient — used for S3 read/write

    _conn: Optional[duckdb.DuckDBPyConnection] = field(default=None, init=False)
    _manifest: Optional[dict] = field(default=None, init=False)
    _last_basket_signature: Optional[str] = field(default=None, init=False)
    _last_used: float = field(default_factory=time.time, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    # ── Paths ────────────────────────────────────────────────────────────────

    @property
    def duck_dir(self) -> Path:
        return self.duck_base_dir / self.user_id / self.presentation_id

    @property
    def duckdb_path(self) -> Path:
        return self.duck_dir / "session.duckdb"

    @property
    def manifest_s3_key(self) -> str:
        return _manifest_key(self.user_id, self.presentation_id)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def touch(self) -> None:
        self._last_used = time.time()

    def get_duck_conn(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            if self._conn is None:
                self.duck_dir.mkdir(parents=True, exist_ok=True)
                self._conn = duckdb.connect(str(self.duckdb_path))
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── Manifest (S3-backed) ─────────────────────────────────────────────────

    def get_manifest(self, fallback: Optional[dict] = None) -> Optional[dict]:
        """Load manifest from cache, then S3, then fallback. Migrates legacy
        flat-shape manifests to nested form on first load."""
        from presentations.migration import ensure_nested

        if self._manifest is not None:
            return self._manifest

        try:
            raw = self.dc.read_json(self.manifest_s3_key)
        except Exception as exc:
            # S3 NoSuchKey or similar → no manifest yet.
            msg = str(exc)
            if "NoSuchKey" not in msg and "404" not in msg and "not found" not in msg.lower():
                log.warning(
                    "session: unexpected S3 read error for %s: %s",
                    self.manifest_s3_key, exc,
                )
            raw = None

        if raw is not None:
            migrated = ensure_nested(raw)
            if migrated is not raw and migrated != raw:
                # Schema migration ran — persist the migrated form so we don't
                # do it again on every load.
                self.set_manifest(migrated)
            else:
                self._manifest = migrated
            return self._manifest

        if fallback is not None:
            self.set_manifest(ensure_nested(fallback))
            return self._manifest
        return None

    def set_manifest(self, manifest: dict) -> None:
        """Persist the manifest to S3 + in-memory cache."""
        with self._lock:
            self._manifest = manifest
            body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            self.dc._upload_bytes(
                self.manifest_s3_key, body, content_type="application/json"
            )
        log.info(
            "session: persisted manifest %s/%s v%s → s3://%s",
            self.user_id, self.presentation_id, manifest.get("version"),
            self.manifest_s3_key,
        )

    def delete_manifest(self) -> None:
        """Remove from S3 + in-memory cache. Also drops the DuckDB file."""
        try:
            self.dc.delete_file(self.manifest_s3_key)
        except Exception as exc:
            log.warning("session: delete S3 failed for %s: %s", self.manifest_s3_key, exc)

        self.close()
        try:
            if self.duckdb_path.exists():
                self.duckdb_path.unlink()
        except Exception as exc:
            log.warning("session: delete local DuckDB failed: %s", exc)

        self._manifest = None
        self._last_basket_signature = None

    # ── Basket → DuckDB ──────────────────────────────────────────────────────

    def basket_signature(self, basket: list[dict]) -> str:
        return json.dumps(basket, sort_keys=True, ensure_ascii=False)

    def needs_refetch(self, basket: list[dict]) -> bool:
        return self.basket_signature(basket) != self._last_basket_signature

    def fetch_basket(self, dc, basket: list[dict]) -> dict:
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
        """Return view names the LLM should know about. Filter out internal
        helpers like `block_preview_*` (created by /table/preview) which
        would otherwise leak into data_summary and confuse the LLM into
        treating them as real tables."""
        if self._conn is None:
            return []
        names = list_views(self._conn)
        return [n for n in names if not n.startswith("block_preview_")]


# ── Registry (app-singleton) ─────────────────────────────────────────────────

class SessionRegistry:
    """Holds active PresentationSession instances. Thread-safe.

    `duck_base_dir`: pod-local directory for DuckDB cache files. On Windows
    typically `C:\\Users\\<user>\\AppData\\Local\\Temp\\prisma-treasury-duck`,
    on Linux `/tmp/prisma-treasury-duck`.

    `dc`: DataClient instance — used by PresentationSession for S3 manifest I/O.
    """

    def __init__(self, dc, duck_base_dir: Optional[str | Path] = None,
                 idle_timeout: int = 1800):
        self.dc = dc
        if duck_base_dir is None:
            duck_base_dir = Path(tempfile.gettempdir()) / "prisma-treasury-duck"
        self.duck_base_dir = Path(duck_base_dir)
        self.duck_base_dir.mkdir(parents=True, exist_ok=True)

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
                    duck_base_dir=self.duck_base_dir,
                    dc=self.dc,
                )
                self._sessions[key] = sess
            sess.touch()
            return sess

    def list_user_presentations(self, user_id: str) -> list[dict]:
        """Scan S3 for all manifests owned by this user. Returns lightweight
        metadata only (no full manifest bytes) — caller can open one to get
        full content.

        S3 layout: prisma-treasury/presentations/<sicil>/<pid>/manifest.json
        """
        prefix = _user_prefix(user_id)
        try:
            keys = self.dc.list_prefix(prefix)
        except Exception as exc:
            log.warning("list_user_presentations: S3 list failed: %s", exc)
            return []

        items = []
        for key in keys:
            if not key.endswith("/manifest.json"):
                continue
            # key = "prisma-treasury/presentations/<sicil>/<pid>/manifest.json"
            parts = key.split("/")
            if len(parts) < 5:
                continue
            pid = parts[-2]
            try:
                manifest = self.dc.read_json(key)
            except Exception as exc:
                log.warning("list_user_presentations: failed to read %s: %s", key, exc)
                continue
            items.append({
                "id": pid,
                "title": manifest.get("meta", {}).get("title", ""),
                "date": manifest.get("meta", {}).get("date", ""),
                "blocks_count": _count_leaf_blocks(manifest),
                "updated_at": manifest.get("updated_at", ""),
                "version": manifest.get("version", 1),
            })
        # Newest first by updated_at
        items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return items

    def cleanup_idle(self, idle_seconds: Optional[int] = None) -> int:
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


def _count_leaf_blocks(manifest: dict) -> int:
    """Count leaf blocks (children inside sections, not the sections themselves)."""
    count = 0
    for section in manifest.get("blocks", []):
        count += len(section.get("children") or [])
    return count
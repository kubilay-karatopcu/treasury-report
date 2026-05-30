"""Phase B — Shared per-library-block result cache with serve-stale.

Where ``block_cache.py`` is *per-session* (one entry per (user, presentation)),
this cache is **library-wide** — one entry per ``(team, id, version, vars)`` —
so a popular block opened by 10 users incurs 1 SQL execution, not 10.

Only blocks that **opt in** via ``refresh_policy.kind = "lazy_ttl"`` are stored
here. Default ``on_open`` blocks bypass this layer and follow the legacy
per-session path. See ``presentations/blocks/schema.py::RefreshPolicy``.

Storage layout (single DuckDB file under
``PRESENTATIONS_SESSION_DIR/library_block_cache.duckdb``):

    library_block_entries(
        cache_key       VARCHAR PRIMARY KEY,
        team            VARCHAR,
        block_id        VARCHAR,
        block_version   INTEGER,
        vars_hash       VARCHAR,
        columns_json    VARCHAR,      -- JSON list of column names
        rows_json       VARCHAR,      -- JSON list of lists
        row_count       INTEGER,
        sql             VARCHAR,
        fetched_at      TIMESTAMP,    -- when this entry was written
        last_accessed_at TIMESTAMP
    )

The DuckDB file is created on demand. Eviction is LRU-by-last_accessed_at,
with a soft cap of ``max_entries`` (default 500); cleanup runs lazily on
``write()`` when the table grows past 110% of cap.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

_TABLE = "library_block_entries"
_DEFAULT_MAX_ENTRIES = 500


def _stable_json(value: Any) -> str:
    """Canonical JSON for hashing — sorted keys, no whitespace, ISO dates."""

    def _default(o: Any):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        if isinstance(o, set):
            return sorted(o)
        return str(o)

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_default)


def hash_vars(resolved_vars: dict[str, Any]) -> str:
    """Canonical sha256 of the resolved variable dict + concept filters."""
    payload = _stable_json(resolved_vars or {})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_cache_key(*, team: str, block_id: str, version: int, vars_hash: str) -> str:
    """Deterministic key. Same inputs across users → same key (shared cache)."""
    raw = f"{team}|{block_id}|{int(version)}|{vars_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class CachedEntry:
    cache_key: str
    team: str
    block_id: str
    block_version: int
    vars_hash: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    sql: str
    fetched_at: datetime
    last_accessed_at: datetime

    def age_seconds(self, now: datetime | None = None) -> float:
        # Both endpoints are naive-UTC by construction (see write()).
        ref = now or datetime.now(timezone.utc).replace(tzinfo=None)
        ft = self.fetched_at
        if ft.tzinfo is not None:
            ft = ft.astimezone(timezone.utc).replace(tzinfo=None)
        return (ref - ft).total_seconds()

    def freshness(
        self, fresh_for_seconds: int, max_age_seconds: int | None,
    ) -> str:
        """Return ``"fresh"``, ``"stale"`` or ``"expired"`` against a policy."""
        age = self.age_seconds()
        if age <= fresh_for_seconds:
            return "fresh"
        if max_age_seconds is not None and age > max_age_seconds:
            return "expired"
        return "stale"


class LibraryBlockCache:
    """Connection-pool friendly cache; safe to use across threads.

    Each call opens a short-lived DuckDB connection (DuckDB allows multiple
    write connections to the same file via the WAL-like multi-process mode
    starting v0.10; we don't rely on that — the lock serialises writes so
    the background dispatcher and the request thread don't collide).
    """

    def __init__(self, db_path: Path, max_entries: int = _DEFAULT_MAX_ENTRIES):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._ensure_schema()

    # ── Schema bootstrap ──────────────────────────────────────────────

    def _connect(self):
        # External access off for consistency with every other DuckDB
        # connection (see presentations.duck.connect_duckdb). This cache only
        # runs internal INSERT/SELECT/DELETE on its own table, so no capability
        # is lost.
        from presentations.duck import connect_duckdb
        return connect_duckdb(str(self.db_path))

    def _ensure_schema(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute(f"""
                    CREATE TABLE IF NOT EXISTS {_TABLE} (
                        cache_key         VARCHAR PRIMARY KEY,
                        team              VARCHAR,
                        block_id          VARCHAR,
                        block_version     INTEGER,
                        vars_hash         VARCHAR,
                        columns_json      VARCHAR,
                        rows_json         VARCHAR,
                        row_count         INTEGER,
                        sql               VARCHAR,
                        fetched_at        TIMESTAMP,
                        last_accessed_at  TIMESTAMP
                    )
                """)
                con.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_lba ON {_TABLE} (last_accessed_at)"
                )
                con.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_block ON {_TABLE} "
                    f"(team, block_id, block_version)"
                )
            finally:
                con.close()

    # ── Public API ────────────────────────────────────────────────────

    def get(
        self, *, team: str, block_id: str, version: int, vars_hash: str,
    ) -> Optional[CachedEntry]:
        """Return cache entry (touching last_accessed_at) or None on miss."""
        key = make_cache_key(team=team, block_id=block_id,
                             version=version, vars_hash=vars_hash)
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    f"SELECT cache_key, team, block_id, block_version, vars_hash, "
                    f"columns_json, rows_json, row_count, sql, "
                    f"fetched_at, last_accessed_at "
                    f"FROM {_TABLE} WHERE cache_key = ?",
                    [key],
                ).fetchone()
                if row is None:
                    return None
                # Store as naive UTC: see write() for the rationale.
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                con.execute(
                    f"UPDATE {_TABLE} SET last_accessed_at = ? WHERE cache_key = ?",
                    [now, key],
                )
                return CachedEntry(
                    cache_key=row[0],
                    team=row[1],
                    block_id=row[2],
                    block_version=int(row[3]),
                    vars_hash=row[4],
                    columns=json.loads(row[5] or "[]"),
                    rows=json.loads(row[6] or "[]"),
                    row_count=int(row[7] or 0),
                    sql=row[8] or "",
                    fetched_at=row[9],
                    last_accessed_at=now,
                )
            finally:
                con.close()

    def write(
        self, *, team: str, block_id: str, version: int, vars_hash: str,
        columns: list[str], rows: list[list[Any]], sql: str,
    ) -> CachedEntry:
        """Upsert an entry. Runs lazy eviction before the insert."""
        key = make_cache_key(team=team, block_id=block_id,
                             version=version, vars_hash=vars_hash)
        # Store as naive UTC: DuckDB strips tzinfo silently and *interprets
        # an aware datetime as local time* before flattening, so we hand it
        # a value that's already naive-UTC to keep readback unambiguous.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cols_json = _stable_json(columns)
        # Rows can be large; we serialise once.
        rows_json = json.dumps(rows, default=str, ensure_ascii=False)
        rc = len(rows)

        with self._lock:
            con = self._connect()
            try:
                self._maybe_evict(con)
                con.execute(
                    f"INSERT OR REPLACE INTO {_TABLE} "
                    f"(cache_key, team, block_id, block_version, vars_hash, "
                    f"columns_json, rows_json, row_count, sql, "
                    f"fetched_at, last_accessed_at) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        key, team, block_id, int(version), vars_hash,
                        cols_json, rows_json, rc, sql, now, now,
                    ],
                )
                log.info(
                    "library_block_cache: write team=%s id=%s v=%s rows=%s key=%s",
                    team, block_id, version, rc, key,
                )
                return CachedEntry(
                    cache_key=key, team=team, block_id=block_id,
                    block_version=int(version), vars_hash=vars_hash,
                    columns=list(columns), rows=list(rows),
                    row_count=rc, sql=sql,
                    fetched_at=now, last_accessed_at=now,
                )
            finally:
                con.close()

    def invalidate_block(
        self, *, team: str, block_id: str, version: int | None = None,
    ) -> int:
        """Drop all entries for a block (every var combo). Used when the
        block's SQL/schema changes — version bump invalidates implicitly,
        but this gives operators a manual sweep too."""
        with self._lock:
            con = self._connect()
            try:
                if version is None:
                    cur = con.execute(
                        f"DELETE FROM {_TABLE} WHERE team = ? AND block_id = ?",
                        [team, block_id],
                    )
                else:
                    cur = con.execute(
                        f"DELETE FROM {_TABLE} WHERE team = ? AND block_id = ? "
                        f"AND block_version = ?",
                        [team, block_id, int(version)],
                    )
                # DuckDB returns affected row count via the cursor.
                deleted = cur.fetchone()
                n = int(deleted[0]) if deleted else 0
                if n:
                    log.info(
                        "library_block_cache: invalidate team=%s id=%s v=%s -> %d entries",
                        team, block_id, version, n,
                    )
                return n
            finally:
                con.close()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            con = self._connect()
            try:
                total = con.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(row_count), 0) FROM {_TABLE}"
                ).fetchone()
                return {
                    "entries": int(total[0] or 0),
                    "total_rows": int(total[1] or 0),
                    "max_entries": self.max_entries,
                }
            finally:
                con.close()

    # ── Internal: eviction ────────────────────────────────────────────

    def _maybe_evict(self, con) -> None:
        """LRU drop when we exceed 110% of max — batch eviction so we don't
        run on every write."""
        ceiling = int(self.max_entries * 1.1)
        count_row = con.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()
        count = int(count_row[0] or 0) if count_row else 0
        if count < ceiling:
            return
        # Drop ~10% of entries — the oldest by last_accessed_at.
        to_drop = max(1, count - self.max_entries + (self.max_entries // 10))
        con.execute(
            f"DELETE FROM {_TABLE} WHERE cache_key IN "
            f"(SELECT cache_key FROM {_TABLE} ORDER BY last_accessed_at ASC LIMIT ?)",
            [to_drop],
        )
        log.info("library_block_cache: evicted %d entries (count was %d, ceiling %d)",
                 to_drop, count, ceiling)

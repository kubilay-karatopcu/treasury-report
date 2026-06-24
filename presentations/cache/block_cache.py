"""Per-block DuckDB result cache with subset routing + LRU eviction.

Storage layout inside the session DuckDB connection:

    cache_meta(
        cache_key       VARCHAR PRIMARY KEY,
        block_id        VARCHAR,
        block_version   INTEGER,
        resolved_json   VARCHAR,       -- JSON-serialised normalised vars
        view_name       VARCHAR,       -- DuckDB view holding the result
        row_count       INTEGER,
        size_bytes      BIGINT,        -- estimated; updated post-fetch
        created_at      TIMESTAMP,
        last_accessed_at TIMESTAMP
    )

Each cache entry creates a registered DuckDB view named
``v_cache_<sha256[:12]>``; subset-routed reads filter against the parent's
view.

Public API:

- :class:`BlockCacheKey` — namedtuple-ish (block_id, version, resolved).
- :func:`cache_key` — sha256 of the normalised resolved-vars dict.
- :func:`is_subset` — given a block's variables and two resolved-vars sets,
  return True iff the second is contained in the first.
- :class:`BlockCache` — owns the connection, manages metadata and eviction.

Phase 6.5.c routing flow (called by the block run endpoint):

    cache = BlockCache(conn, block_def)
    hit = cache.find_exact(resolved)
    if hit: return cache.read(hit)
    parent = cache.find_subset_parent(resolved)
    if parent:
        return cache.derive_from_parent(parent, resolved)   # DuckDB filter
    # Cache miss → fetch from Oracle, then:
    cache.write(resolved, dataframe)

Eviction runs inside ``write()`` before the new row is inserted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

from presentations.aggregation_gate import MAX_RAW_ROWS
from presentations.blocks.schema import Block, Variable
from presentations.sql.validator import _strip_noise
from presentations.variables.resolver import normalize_for_cache_key


log = logging.getLogger(__name__)


# ── Subset-routing safety ───────────────────────────────────────────────────
# A block's result can only be subset-filtered from a cached parent (instead of
# re-querying Oracle) when its SQL is a *pure row projection*. Applying a
# row-level WHERE to a result that was aggregated / windowed / grouped / set-
# combined / row-capped produces silently wrong numbers. We detect those shapes
# conservatively — a false "unsafe" only costs a cache miss (an Oracle refetch),
# never correctness — and disable subset routing for them.
_SUBSET_UNSAFE_RE = re.compile(
    r"\b(GROUP\s+BY|HAVING|UNION|INTERSECT|EXCEPT|MINUS|DISTINCT"
    r"|LIMIT|FETCH\s+(?:FIRST|NEXT)|ROWNUM)\b|\bOVER\s*\(",
    re.IGNORECASE,
)
_SUBSET_UNSAFE_AGG_RE = re.compile(
    r"\b(COUNT|SUM|AVG|MIN|MAX|STDDEV|VARIANCE|MEDIAN|LISTAGG|STRING_AGG|ARRAY_AGG"
    r"|PERCENTILE_CONT|PERCENTILE_DISC|FIRST_VALUE|LAST_VALUE|CORR"
    r"|COVAR_POP|COVAR_SAMP|REGR_SLOPE|REGR_INTERCEPT)\s*\(",
    re.IGNORECASE,
)


def is_subset_safe(sql: str) -> bool:
    """True only when ``sql`` is a pure row projection whose cached result can
    be correctly narrowed by a row-level WHERE (see ``_derive_from_parent`` in
    routes). Comments and string literals are stripped first so a keyword inside
    them doesn't trip the check."""
    if not sql or not sql.strip():
        return False
    cleaned = _strip_noise(sql)
    if _SUBSET_UNSAFE_RE.search(cleaned):
        return False
    if _SUBSET_UNSAFE_AGG_RE.search(cleaned):
        return False
    return True


# ── Cache key ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BlockCacheKey:
    """Stable identifier for a (block_id, version, resolved_vars) triple."""

    block_id: str
    version: int
    digest: str  # sha256 hex, first 64 chars

    @property
    def short(self) -> str:
        return self.digest[:12]


def cache_key(
    block_id: str, version: int, resolved: dict[str, Any], sql: str = "",
    concept_digest: str = "",
) -> BlockCacheKey:
    """Compute the canonical cache key.

    The resolved dict is normalised first (dates → ISO, enum_multi sorted,
    nested dict keys sorted), so equivalent value sets yield identical keys
    regardless of caller-side ordering. ``sql`` (the block's query text) is
    folded in too: an in-presentation block can have its SQL edited without a
    version bump, so two different queries with the same id/version/vars must
    NOT collide on one key — otherwise a hit would serve the other query's
    stale rows.

    ``concept_digest`` folds in the active dashboard concept-filter state
    (Phase 7 / C2b): a block carrying the ``{{concept_filters}}`` sentinel +
    variables would otherwise hit the variable-keyed cache and serve stale rows
    after a concept filter changes. It is added to the payload ONLY when
    non-empty, so a block with no active concept filter keeps a key byte-
    identical to the pre-Phase-7 form (no cache-miss storm, no regression).
    """
    norm = normalize_for_cache_key(resolved)
    payload_dict = {"block_id": block_id, "version": int(version), "resolved": norm,
                    "sql": sql or ""}
    if concept_digest:
        payload_dict["concepts"] = concept_digest
    payload = json.dumps(
        payload_dict, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return BlockCacheKey(block_id=block_id, version=int(version), digest=digest)


def concept_filters_digest(filters: list) -> str:
    """Deterministic 16-hex digest of active concept-filter state for the cache
    key (C2b). ``filters`` = ``[{"concept","operator","values"}, ...]``.

    Empty → ``""`` so a block with no active concept filter keeps its pre-
    Phase-7 cache key. Values are sorted (enum order-independent) and dates
    stringified so equivalent filter sets hash identically regardless of order.
    """
    if not filters:
        return ""
    norm = []
    for f in filters:
        vals = f.get("values")
        if isinstance(vals, (list, tuple, set)):
            try:
                vals = sorted(vals, key=lambda x: str(x))
            except Exception:
                vals = list(vals)
        norm.append({"c": f.get("concept") or "",
                     "op": f.get("operator") or "",
                     "v": vals})
    norm.sort(key=lambda d: (d["c"], d["op"],
                             json.dumps(d["v"], default=str, sort_keys=True)))
    payload = json.dumps(norm, sort_keys=True, default=str, ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ── Subset detection ──────────────────────────────────────────────────────

def is_subset(
    current: dict[str, Any],
    parent: dict[str, Any],
    block_variables: Iterable[Variable],
) -> bool:
    """Return True iff ``current`` resolves to a subset of ``parent``.

    Per-type subset rules (spec §4.3):
    - ``date``: equal (subset only when identical).
    - ``date_range``: current.from ≥ parent.from AND current.to ≤ parent.to.
    - ``enum_single``: equal.
    - ``enum_multi``: set(current) ⊆ set(parent).
    - ``number_range``: current.min ≥ parent.min AND current.max ≤ parent.max.

    A variable not present in one side aborts the comparison (False) — they
    must address the same variable schema.
    """
    for var in block_variables:
        c = current.get(var.name)
        p = parent.get(var.name)
        if c is None and p is None:
            continue  # both optional, both skipped
        if c is None or p is None:
            return False

        if var.type == "date":
            if c != p:
                return False
        elif var.type == "date_range":
            if not isinstance(c, dict) or not isinstance(p, dict):
                return False
            if not (_to_date(p["from"]) <= _to_date(c["from"])
                    and _to_date(c["to"]) <= _to_date(p["to"])):
                return False
        elif var.type == "enum_single":
            if c != p:
                return False
        elif var.type == "enum_multi":
            if not set(c).issubset(set(p)):
                return False
        elif var.type == "number_range":
            if not isinstance(c, dict) or not isinstance(p, dict):
                return False
            if not (p["min"] <= c["min"] and c["max"] <= p["max"]):
                return False
        # Any unsupported type → conservative: not a subset.
        else:
            return False
    return True


def _to_date(v: Any) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v)
    raise TypeError(f"cannot coerce to date: {v!r}")


def filter_clause_for_subset(
    current: dict[str, Any],
    block_variables: Iterable[Variable],
) -> tuple[str, dict[str, Any]]:
    """Build a DuckDB WHERE clause that derives a subset from a parent view.

    Only emits clauses for variables that *narrow* the parent — i.e., where
    the current resolved value differs from the parent's. The caller has
    already verified ``is_subset`` returned True.

    Returns ``(where_clause, bind_params)`` where ``where_clause`` either
    starts with " WHERE " or is empty.

    Note: the cache view holds the parent's raw result columns. Filtering
    requires the variables' *column* mappings to be known. Since blocks in
    v0 produce one query, we assume the variable name → column mapping is
    1:1 via the user's :name binds. For subset-filtering the cached view,
    callers must pass the column override (deferred to Phase 6.5.c's
    write-then-read pass — for now this returns parameter binds and the
    caller maps to columns).
    """
    bind_params: dict[str, Any] = {}
    clauses: list[str] = []

    for var in block_variables:
        if var.name not in current:
            continue
        if var.type == "enum_multi":
            # We cannot filter without a column name; the caller assembles.
            # Emit placeholder pattern: caller substitutes.
            placeholders = [f":{var.name}_subset_{i}" for i in range(len(current[var.name]))]
            for i, v in enumerate(current[var.name]):
                bind_params[f"{var.name}_subset_{i}"] = v
            clauses.append(f"<{var.name}_COL> IN ({', '.join(placeholders)})")
        elif var.type == "date_range":
            bind_params[f"{var.name}_from_subset"] = _to_date(current[var.name]["from"])
            bind_params[f"{var.name}_to_subset"] = _to_date(current[var.name]["to"])
            clauses.append(
                f"<{var.name}_COL> BETWEEN :{var.name}_from_subset "
                f"AND :{var.name}_to_subset"
            )
        elif var.type == "date":
            bind_params[f"{var.name}_subset"] = _to_date(current[var.name])
            clauses.append(f"<{var.name}_COL> = :{var.name}_subset")
        elif var.type == "enum_single":
            bind_params[f"{var.name}_subset"] = current[var.name]
            clauses.append(f"<{var.name}_COL> = :{var.name}_subset")
        elif var.type == "number_range":
            bind_params[f"{var.name}_min_subset"] = current[var.name]["min"]
            bind_params[f"{var.name}_max_subset"] = current[var.name]["max"]
            clauses.append(
                f"<{var.name}_COL> BETWEEN :{var.name}_min_subset "
                f"AND :{var.name}_max_subset"
            )
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, bind_params


# ── BlockCache ────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    """Metadata for one cached result inside the session DuckDB connection."""

    key: BlockCacheKey
    resolved: dict[str, Any]      # normalised
    sql: str                      # the block query that produced this result
    view_name: str
    row_count: int
    size_bytes: int
    created_at: datetime
    last_accessed_at: datetime


class BlockCache:
    """In-process DuckDB-backed cache for one (user, session) lifetime.

    The DuckDB connection is borrowed from the existing per-session DuckDB
    file (``session.get_duck_conn()``); we only add tables / views — no
    schema migration of existing user data.

    Thread-safety: a single connection is used. Callers must serialise
    access externally (the existing SessionRegistry holds a lock for the
    presentation's DuckDB conn).
    """

    # 2 GB soft cap per spec §4.4. Used by maybe_evict to trigger LRU drops.
    SOFT_CAP_BYTES = 2 * 1024 * 1024 * 1024

    _META_TABLE = "_phase65_block_cache_meta"

    def __init__(self, conn):
        self.conn = conn
        self._ensure_meta_table()

    # ── Bootstrap ────────────────────────────────────────────────────
    def _ensure_meta_table(self) -> None:
        self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._META_TABLE} (
                cache_key         VARCHAR PRIMARY KEY,
                block_id          VARCHAR,
                block_version     INTEGER,
                resolved_json     VARCHAR,
                sql               VARCHAR,
                view_name         VARCHAR,
                row_count         INTEGER,
                size_bytes        BIGINT,
                created_at        TIMESTAMP,
                last_accessed_at  TIMESTAMP
            )
            """
        )
        # Defensive: a session DuckDB file created before the `sql` column was
        # added still carries the old shape — add it so reads/writes don't fail.
        try:
            self.conn.execute(
                f"ALTER TABLE {self._META_TABLE} ADD COLUMN IF NOT EXISTS sql VARCHAR"
            )
        except Exception:
            pass

    # ── Lookup ───────────────────────────────────────────────────────
    def find_exact(self, key: BlockCacheKey) -> Optional[CacheEntry]:
        rows = self.conn.execute(
            f"SELECT cache_key, block_id, block_version, resolved_json, sql, view_name, "
            f"row_count, size_bytes, created_at, last_accessed_at "
            f"FROM {self._META_TABLE} WHERE cache_key = ?",
            [key.digest],
        ).fetchall()
        if not rows:
            return None
        entry = _row_to_entry(rows[0])
        self._touch(entry.key)
        return entry

    def find_subset_parent(
        self,
        block: Block,
        resolved: dict[str, Any],
    ) -> Optional[CacheEntry]:
        """Walk cached entries for this block_id/version; return the most
        recent one that contains ``resolved`` as a subset.

        Returns None — forcing a fresh Oracle fetch — when the block's SQL is
        not subset-safe (aggregation / window / grouping / set op / row cap), or
        when the only candidate parents were themselves row-capped (possibly
        truncated, so not a sound superset). Both guards prevent serving
        silently-wrong numbers from a row-filtered cached result.
        """
        if not is_subset_safe(block.query):
            return None
        norm = normalize_for_cache_key(resolved)
        # Only parents produced by the SAME SQL are valid supersets — a different
        # query with the same vars is unrelated data.
        candidates = self.conn.execute(
            f"SELECT cache_key, block_id, block_version, resolved_json, sql, view_name, "
            f"row_count, size_bytes, created_at, last_accessed_at "
            f"FROM {self._META_TABLE} "
            f"WHERE block_id = ? AND block_version = ? AND sql = ? "
            f"ORDER BY last_accessed_at DESC",
            [block.id, block.version, block.query],
        ).fetchall()
        for row in candidates:
            entry = _row_to_entry(row)
            if entry.row_count >= MAX_RAW_ROWS:
                # Parent hit the row cap → possibly truncated; a narrower filter
                # could legitimately include rows the parent dropped.
                continue
            if is_subset(norm, entry.resolved, block.variables):
                self._touch(entry.key)
                return entry
        return None

    def list_all(self) -> list[CacheEntry]:
        rows = self.conn.execute(
            f"SELECT cache_key, block_id, block_version, resolved_json, sql, view_name, "
            f"row_count, size_bytes, created_at, last_accessed_at "
            f"FROM {self._META_TABLE}"
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    # ── Write ────────────────────────────────────────────────────────
    def write(
        self,
        block: Block,
        resolved: dict[str, Any],
        df,
    ) -> CacheEntry:
        """Persist ``df`` under a new cache entry. Evicts first if the new
        entry would push the session above the soft cap."""
        # Reserve room for the incoming entry: evict enough so total + new ≤ cap.
        new_size = _estimate_df_size(df)
        self.maybe_evict(reserve=new_size)

        key = cache_key(block.id, block.version, resolved, block.query)
        view_name = f"v_cache_{key.short}"
        norm = normalize_for_cache_key(resolved)
        size_bytes = new_size
        row_count = int(len(df))
        now = datetime.now(timezone.utc)

        # Register the dataframe as a DuckDB view. We use CREATE OR REPLACE
        # so re-runs of the same key don't blow up on collision.
        # `df` must be a pandas DataFrame; the caller converts upstream.
        try:
            self.conn.unregister(view_name)
        except Exception:
            pass
        self.conn.register(view_name, df)
        # Promote to a TABLE so the result survives subsequent df mutations.
        self.conn.execute(
            f'CREATE OR REPLACE TABLE "{view_name}" AS SELECT * FROM "{view_name}"'
        )

        self.conn.execute(
            f"INSERT OR REPLACE INTO {self._META_TABLE} "
            f"(cache_key, block_id, block_version, resolved_json, sql, view_name, "
            f" row_count, size_bytes, created_at, last_accessed_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                key.digest, block.id, int(block.version),
                json.dumps(norm, ensure_ascii=False, sort_keys=True, default=str),
                block.query,
                view_name, row_count, size_bytes, now, now,
            ],
        )
        log.info(
            "block_cache: wrote key=%s block=%s/v%d rows=%d size=%d MB",
            key.short, block.id, block.version, row_count, size_bytes // (1024 * 1024),
        )
        return CacheEntry(
            key=key, resolved=norm, sql=block.query, view_name=view_name,
            row_count=row_count, size_bytes=size_bytes,
            created_at=now, last_accessed_at=now,
        )

    # ── Eviction ─────────────────────────────────────────────────────
    def total_size_bytes(self) -> int:
        row = self.conn.execute(
            f"SELECT COALESCE(SUM(size_bytes), 0) FROM {self._META_TABLE}"
        ).fetchone()
        return int(row[0])

    def maybe_evict(self, reserve: int = 0) -> int:
        """Evict LRU entries until ``total + reserve`` is under the soft cap.

        ``reserve`` is the projected size of an about-to-be-written entry —
        callers pass it from :meth:`write` so eviction fires *before* the
        new entry pushes us past the limit.

        Returns the number of entries evicted.
        """
        total = self.total_size_bytes()
        if total + reserve <= self.SOFT_CAP_BYTES:
            return 0
        evicted = 0
        rows = self.conn.execute(
            f"SELECT cache_key, view_name, size_bytes FROM {self._META_TABLE} "
            f"ORDER BY last_accessed_at ASC"
        ).fetchall()
        for cache_key_digest, view_name, size_bytes in rows:
            if total + reserve <= self.SOFT_CAP_BYTES:
                break
            self._drop_entry(cache_key_digest, view_name)
            total -= int(size_bytes)
            evicted += 1
        log.info("block_cache: evicted %d entries to fit %d MB cap (reserve=%d)",
                 evicted, self.SOFT_CAP_BYTES // (1024 * 1024), reserve)
        return evicted

    def _drop_entry(self, cache_key_digest: str, view_name: str) -> None:
        try:
            self.conn.execute(f'DROP TABLE IF EXISTS "{view_name}"')
        except Exception:
            try:
                self.conn.unregister(view_name)
            except Exception:
                pass
        self.conn.execute(
            f"DELETE FROM {self._META_TABLE} WHERE cache_key = ?",
            [cache_key_digest],
        )

    def evict_all(self) -> int:
        """Force-clear every entry. Used by tests + manual session reset."""
        rows = self.conn.execute(
            f"SELECT cache_key, view_name FROM {self._META_TABLE}"
        ).fetchall()
        for k, v in rows:
            self._drop_entry(k, v)
        return len(rows)

    # ── Maintenance ──────────────────────────────────────────────────
    def _touch(self, key: BlockCacheKey) -> None:
        self.conn.execute(
            f"UPDATE {self._META_TABLE} SET last_accessed_at = ? WHERE cache_key = ?",
            [datetime.now(timezone.utc), key.digest],
        )


# ── Helpers ───────────────────────────────────────────────────────────────

def _row_to_entry(row) -> CacheEntry:
    (cache_key_digest, block_id, block_version, resolved_json, sql, view_name,
     row_count, size_bytes, created_at, last_accessed_at) = row
    return CacheEntry(
        key=BlockCacheKey(
            block_id=block_id,
            version=int(block_version),
            digest=cache_key_digest,
        ),
        resolved=json.loads(resolved_json) if resolved_json else {},
        sql=sql or "",
        view_name=view_name,
        row_count=int(row_count or 0),
        size_bytes=int(size_bytes or 0),
        created_at=created_at,
        last_accessed_at=last_accessed_at,
    )


def _estimate_df_size(df) -> int:
    """Cheap byte estimate of a pandas DataFrame's payload."""
    try:
        return int(df.memory_usage(deep=True, index=True).sum())
    except Exception:
        # Worst-case fallback: row_count * column_count * 32 bytes/cell.
        return int(len(df) * max(len(df.columns), 1) * 32)

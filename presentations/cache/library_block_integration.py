"""Phase B — Bridge between the apply-filters loop and the library cache.

The apply-filters loop in ``presentations/routes.py`` calls
:func:`try_serve_from_library_cache` BEFORE its existing per-session cache
lookup. The helper returns:

  * a **result dict** (matches the loop's per-block result schema) when the
    cache served the row (fresh or stale-with-background-refetch), so the
    loop can ``continue`` to the next block;
  * ``None`` when the loop should fall through to the normal fetch path
    (cache miss, or block isn't library-cache eligible).

After a successful normal fetch, the loop also calls
:func:`maybe_write_library_cache` so the next viewer is a hit.

Eligibility:

- block has ``imported_from.library_id`` (set by ``add_library_block_to_draft``)
- block has ``refresh_policy.kind == "lazy_ttl"``
- query doesn't contain a per-user predicate (heuristic: ``:sicil``,
  ``:owner``, or ``OWNER_ID =`` literal). Per-user queries can't share a
  cache entry across viewers safely.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# Heuristic: queries that filter by the caller's identity must not be
# shared across users. Reject lazy_ttl for these blocks (fall back to
# per-session cache); a future phase can add an explicit
# ``per_user_safe: bool`` opt-out on the block side.
_PER_USER_PATTERNS = (
    re.compile(r":sicil\b", re.IGNORECASE),
    re.compile(r":owner\b", re.IGNORECASE),
    re.compile(r"\bOWNER_ID\s*=\s*'", re.IGNORECASE),
    re.compile(r"\bCURRENT_USER\b", re.IGNORECASE),
)


def is_library_cacheable(block: dict) -> Optional[dict]:
    """Return the block's ``refresh_policy`` dict iff this block is
    eligible for the shared library cache; else ``None``."""
    imp = block.get("imported_from") or {}
    if not imp.get("library_id"):
        return None
    rp = block.get("refresh_policy") or {}
    if rp.get("kind") != "lazy_ttl":
        return None
    query = (
        block.get("query")
        or (block.get("data_source") or {}).get("original_sql")
        or ""
    )
    for pat in _PER_USER_PATTERNS:
        if pat.search(query):
            log.debug(
                "library cache: block %s rejected (per-user predicate)",
                block.get("id"),
            )
            return None
    return rp


def _library_block_identity(block: dict) -> tuple[str, str, int]:
    """Return ``(team, library_id, synthetic_version)`` for cache keying.

    Legacy library blocks have no team or integer version. We map:

    - team = ``"library"`` (constant) so all legacy blocks share a namespace
    - library_id stays as-is
    - synthetic version = an integer derived from
      ``imported_from.library_updated_at`` so editing the source block
      automatically invalidates downstream caches.
    """
    imp = block.get("imported_from") or {}
    lib_id = str(imp.get("library_id") or "")
    updated = str(imp.get("library_updated_at") or "")
    # Encode the timestamp into a stable int. Two clones from the same
    # source revision share the same version → cache key collision (which
    # is exactly what we want: dedup).
    if updated:
        try:
            ts = _dt.datetime.fromisoformat(updated.replace("Z", "+00:00"))
            version = int(ts.timestamp())
        except ValueError:
            version = abs(hash(updated)) % (10**9)
    else:
        version = 1
    return ("library", lib_id, version)


def _result_status_from_freshness(
    freshness: str, fetched_at: _dt.datetime, age_seconds: float, row_count: int,
    refreshing: bool,
) -> dict:
    """Common per-block result envelope used by both cache-hit branches.

    The apply-filters loop's existing result shape includes ``id``,
    ``status``, ``row_count``; we add freshness fields the React badge
    consumes."""
    return {
        "status": "library_cache_hit",     # one canonical status for both fresh/stale
        "freshness": freshness,             # "fresh" | "stale" | "expired"
        "fetched_at": fetched_at.isoformat() if fetched_at else None,
        "age_seconds": int(age_seconds),
        "row_count": int(row_count),
        "library_refreshing": bool(refreshing),
        "source": "library_cache",
    }


def try_serve_from_library_cache(
    *,
    block: dict,
    resolved_vars: dict,
    apply_df_to_block: Callable[..., None],  # _apply_df_to_block(block, df, *, engine, query)
    cache,                     # LibraryBlockCache
    dispatcher,                # RefreshDispatcher
    fetch_fn: Callable[[], Any],  # closure to refetch on miss (used by dispatcher)
    sql: str,
) -> Optional[dict]:
    """Try to serve the block from the shared cache. Returns the per-block
    result envelope if served, ``None`` if the caller should fall through
    to the normal fetch path.

    On stale hit (``serve_stale``) the call enqueues a background refetch
    via ``dispatcher`` so the next reader is fresh.
    """
    from .library_block_cache import hash_vars  # local import to avoid cycle
    import pandas as pd

    rp = is_library_cacheable(block)
    if rp is None:
        return None

    team, lib_id, version = _library_block_identity(block)
    if not lib_id:
        return None

    vh = hash_vars(resolved_vars)
    entry = cache.get(team=team, block_id=lib_id, version=version, vars_hash=vh)

    if entry is None:
        return None  # miss → caller does normal fetch (and writes after)

    fresh_for = int(rp.get("fresh_for_seconds", 600))
    max_age = rp.get("max_age_seconds")
    if max_age is not None:
        max_age = int(max_age)
    freshness = entry.freshness(fresh_for, max_age)
    serve_stale = bool(rp.get("serve_stale", True))

    if freshness == "expired" or (freshness == "stale" and not serve_stale):
        # Too old to serve; caller does a sync fetch.
        return None

    # Materialise the cached rows back into block.config so the renderer
    # picks them up unchanged. We reuse the existing apply_df_to_block
    # helper instead of re-implementing the per-type config writeback.
    # NOTE: keyword args required — _apply_df_to_block signature is
    # ``(block, df, *, engine, query)``.
    df = pd.DataFrame(entry.rows, columns=entry.columns)
    apply_df_to_block(block, df, engine="library_cache", query=sql)

    refreshing = False
    if freshness == "stale" and serve_stale:
        # Enqueue background refetch — dispatcher deduplicates concurrent
        # hits, so 10 viewers of the same stale block trigger 1 query.
        cache_key = entry.cache_key

        def _on_success(df_new):
            try:
                cols = [str(c) for c in df_new.columns]
                rows = [
                    [
                        (v.isoformat() if hasattr(v, "isoformat") else v)
                        for v in row
                    ]
                    for row in df_new.itertuples(index=False, name=None)
                ]
                cache.write(
                    team=team, block_id=lib_id, version=version,
                    vars_hash=vh, columns=cols, rows=rows, sql=sql,
                )
            except Exception:
                log.warning(
                    "library cache background write failed for %s",
                    cache_key, exc_info=True,
                )

        refreshing = dispatcher.enqueue(
            cache_key=cache_key, fetch=fetch_fn, on_success=_on_success,
        )

    return _result_status_from_freshness(
        freshness=freshness,
        fetched_at=entry.fetched_at,
        age_seconds=entry.age_seconds(),
        row_count=entry.row_count,
        refreshing=refreshing,
    )


def maybe_write_library_cache(
    *,
    block: dict,
    resolved_vars: dict,
    df,
    sql: str,
    cache,
) -> bool:
    """Write a fresh fetch result into the shared cache, if the block is
    library-cache eligible. Safe to call after every normal-path fetch —
    the eligibility check is the gate.

    Returns True if a cache entry was written, False otherwise."""
    from .library_block_cache import hash_vars

    if is_library_cacheable(block) is None:
        return False
    team, lib_id, version = _library_block_identity(block)
    if not lib_id:
        return False
    vh = hash_vars(resolved_vars)
    try:
        cols = [str(c) for c in df.columns]
        rows = [
            [
                (v.isoformat() if hasattr(v, "isoformat") else v)
                for v in row
            ]
            for row in df.itertuples(index=False, name=None)
        ]
        cache.write(
            team=team, block_id=lib_id, version=version,
            vars_hash=vh, columns=cols, rows=rows, sql=sql,
        )
        return True
    except Exception:
        log.warning("library cache write failed for block %s lib=%s",
                    block.get("id"), lib_id, exc_info=True)
        return False

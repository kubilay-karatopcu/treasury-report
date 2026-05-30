"""Phase B+ — Background warm-cache scheduler for library blocks.

A single daemon thread that wakes every ``poll_interval_seconds`` (default 60s),
walks the library store for blocks tagged ``refresh_policy.kind = "scheduled"``,
and enqueues a refetch via the :class:`RefreshDispatcher` whenever the cached
entry is older than the block's ``interval_seconds``.

Design choices (kept intentionally simple):

- **Default variables only**: scheduled refresh warms the cache for the
  *default* variable set of each block. Any user-specific filter combination
  still hits the on-demand lazy_ttl path (see ``library_block_integration``).
  Trying to enumerate "every possible variable combination" is unbounded; the
  lazy path handles tail combos with serve-stale.
- **No APScheduler dependency**: a single ``threading.Thread`` with a sleep
  loop is enough for one-pod deployments. If we go multi-pod we'll need a
  distributed lock (Redis) to avoid duplicate triggers, at which point
  APScheduler or Celery beat is the natural upgrade.
- **Failure isolation**: a per-block scan failure (missing table doc, broken
  SQL, transient Oracle outage) is logged and skipped; the loop continues.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta as _td, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


class LibraryRefreshScheduler:
    """Single-thread polling scheduler for ``refresh_policy.kind=scheduled`` blocks."""

    def __init__(
        self, *,
        library_store,            # LocalLibraryStore / S3LibraryStore (or duck-typed)
        cache,                    # LibraryBlockCache
        dispatcher,               # RefreshDispatcher
        data_client,              # DataClient (Oracle/fake)
        poll_interval_seconds: int = 60,
        name: str = "lib-refresh-scheduler",
    ):
        self._lib_store = library_store
        self._cache = cache
        self._dispatcher = dispatcher
        self._dc = data_client
        self._poll = max(10, int(poll_interval_seconds))
        self._name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("scheduler already started")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=self._name, daemon=True,
        )
        self._thread.start()
        log.info("library refresh scheduler started (poll=%ss)", self._poll)

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=5)
        log.info("library refresh scheduler stopped")

    # ── Loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        # Spread the first scan a bit so the worker isn't slammed at boot.
        time.sleep(min(5, self._poll))
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.warning("scheduler tick crashed", exc_info=True)
            # Wait up to poll_interval, exiting early if stop() is called.
            self._stop.wait(self._poll)

    def _tick(self) -> None:
        """One scan over the library store."""
        try:
            blocks = self._list_scheduled_blocks()
        except Exception:
            log.warning("scheduler: failed to list library blocks", exc_info=True)
            return
        if not blocks:
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for entry in blocks:
            try:
                self._maybe_refresh(entry, now=now)
            except Exception:
                log.warning(
                    "scheduler: refresh failed for library_id=%s",
                    (entry.get("meta") or {}).get("library_id"),
                    exc_info=True,
                )

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _is_due(rp: dict, existing, *, now: datetime) -> bool:
        """Return True iff a refetch should be enqueued.

        ``existing`` is the cached entry (None on first run) and ``now`` is
        naive-UTC. Supports both interval mode and schedule (time-of-day)
        mode; the validator guarantees at most one is set.
        """
        sched = rp.get("schedule") or None
        interval = rp.get("interval_seconds")

        # Time-of-day mode (preferred). We compute the most-recent target
        # firing time and compare to the cache's fetched_at.
        if isinstance(sched, dict) and sched.get("times"):
            last_target = LibraryRefreshScheduler._last_scheduled_target(
                times=list(sched.get("times") or []),
                days=list(sched.get("days") or []) or None,
                tz_name=str(sched.get("timezone") or "Europe/Istanbul"),
                now=now,
            )
            if last_target is None:
                return False
            if existing is None:
                return True
            return existing.fetched_at < last_target

        # Interval mode (fallback). Defaults to fresh_for_seconds if no
        # explicit interval was given.
        period = int(
            interval if interval is not None
            else rp.get("fresh_for_seconds") or 600
        )
        if existing is None:
            return True
        return existing.age_seconds(now) >= period

    @staticmethod
    def _last_scheduled_target(
        *, times: list[str], days: list[str] | None,
        tz_name: str, now: datetime,
    ) -> datetime | None:
        """Most-recent target firing time at or before ``now`` (naive-UTC).

        Walks back up to 8 days and picks the latest matching slot. Returns
        None if no slot matches in that window — shouldn't happen if
        ``times`` is non-empty and at least one weekday is allowed."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = None

        # 0=Mon..6=Sun
        _DAY_CODES = ("MON","TUE","WED","THU","FRI","SAT","SUN")
        allow_set = set(_DAY_CODES if not days else days)
        allow_idx = {i for i, d in enumerate(_DAY_CODES) if d in allow_set}
        if not allow_idx:
            return None

        # Convert "now" to the policy tz for slot matching.
        from datetime import datetime as _dt, timezone as _tz
        now_utc_aware = now.replace(tzinfo=_tz.utc)
        now_local = now_utc_aware.astimezone(tz) if tz else now_utc_aware

        # Parse HH:MM list into (hh, mm) tuples once.
        slots: list[tuple[int, int]] = []
        for t in times:
            try:
                hh, _, mm = (t or "").partition(":")
                slots.append((int(hh), int(mm)))
            except ValueError:
                continue
        if not slots:
            return None
        slots.sort()

        # Walk back up to 8 days finding the most-recent valid (day, time).
        for offset in range(0, 9):
            day_local = now_local - _td(days=offset)
            if day_local.weekday() not in allow_idx:
                continue
            # For today, only times <= now_local are candidates.
            candidate_times = slots if offset > 0 else [
                (h, m) for (h, m) in slots
                if (h, m, 0) <= (now_local.hour, now_local.minute, now_local.second)
            ]
            if not candidate_times:
                continue
            hh, mm = candidate_times[-1]
            target_local = day_local.replace(
                hour=hh, minute=mm, second=0, microsecond=0,
            )
            target_utc = (
                target_local.astimezone(_tz.utc).replace(tzinfo=None)
                if tz else target_local.replace(tzinfo=None)
            )
            return target_utc
        return None

    def _list_scheduled_blocks(self) -> list[dict]:
        """Return ``[{block, meta}]`` for every library block whose
        ``refresh_policy.kind == "scheduled"``.

        The legacy LIBRARY_STORE exposes ``list_blocks`` returning meta
        summaries; we load each one fully and filter. This is fine at the
        scale we expect (~100 blocks). At 1k+ blocks we'd index policies."""
        out: list[dict] = []
        if not hasattr(self._lib_store, "list_blocks"):
            return out
        try:
            summaries = self._lib_store.list_blocks()
        except TypeError:
            # Some stores require an owner / dept filter; pass empty.
            try:
                summaries = self._lib_store.list_blocks(
                    user_sicil="", user_department="",
                )
            except Exception:
                return out
        for s in summaries or []:
            lib_id = (
                s.get("library_id") if isinstance(s, dict) else getattr(s, "id", None)
            )
            if not lib_id:
                continue
            try:
                payload = self._lib_store.load(lib_id)
            except Exception:
                continue
            if not payload:
                continue
            block = payload.get("block") or {}
            rp = block.get("refresh_policy") or {}
            if rp.get("kind") != "scheduled":
                continue
            out.append(payload)
        return out

    def _maybe_refresh(self, payload: dict, *, now: datetime) -> None:
        from .library_block_cache import hash_vars, make_cache_key
        from .library_block_integration import _library_block_identity  # noqa: PLC2701
        import pandas as _pd

        block = payload.get("block") or {}
        meta = payload.get("meta") or {}
        rp = block.get("refresh_policy") or {}

        lib_id = meta.get("library_id") or block.get("id")
        if not lib_id:
            return

        # Build the synthetic Phase B identity tuple. We need
        # ``imported_from.library_updated_at`` to derive the version stamp; if
        # the block doesn't have one (it lives ONLY in the library store), use
        # meta.updated_at directly.
        block_with_provenance = dict(block)
        block_with_provenance["imported_from"] = {
            "library_id": lib_id,
            "library_updated_at": meta.get("updated_at") or meta.get("created_at") or "",
        }
        team, _, version = _library_block_identity(block_with_provenance)

        # Resolve "default vars": iterate declared variables and pick
        # ``default`` (or ``allowed_values`` for enum_multi). Mirrors the
        # resolver but stays minimal — we don't have a Block instance here.
        resolved: dict[str, Any] = {}
        for v in (block.get("variables") or []):
            name = v.get("name")
            if not name:
                continue
            if v.get("default") is not None:
                resolved[name] = v["default"]
            elif v.get("type") == "enum_multi" and v.get("allowed_values"):
                resolved[name] = list(v["allowed_values"])

        vh = hash_vars(resolved)
        existing = self._cache.get(
            team=team, block_id=lib_id, version=version, vars_hash=vh,
        )

        # Decide whether a refetch is due. Two modes:
        #
        # 1) interval_seconds — fixed period since the last fetch.
        # 2) schedule.times + days + tz — fire at HH:MM on the listed
        #    weekdays. We pick the most-recent target time <= now (catching
        #    up on misses across app restarts) and refetch iff the cache
        #    entry is older than that target.
        if not self._is_due(rp, existing, now=now):
            return

        cache_key = make_cache_key(
            team=team, block_id=lib_id, version=version, vars_hash=vh,
        )
        sql = (
            block.get("query")
            or (block.get("data_source") or {}).get("original_sql")
            or ""
        )
        if not sql:
            return

        # Build the fetch closure — runs in dispatcher's worker thread.
        def _fetch():
            # Default-vars warm: no bind expansion needed unless the SQL
            # contains ``:bind`` references. The legacy LIBRARY_STORE blocks
            # mostly carry inlined SQL; supporting bind expansion here would
            # duplicate the apply-filters pipeline. Future: refactor a shared
            # `resolve_and_fetch` helper.
            df = self._dc.get_data(
                base_prefix=None,
                dataset=f"library_block::{lib_id}",
                query=sql,
            )
            return df if df is not None else _pd.DataFrame()

        def _store(df):
            try:
                cols = [str(c) for c in df.columns]
                rows = [
                    [
                        (v.isoformat() if hasattr(v, "isoformat") else v)
                        for v in row
                    ]
                    for row in df.itertuples(index=False, name=None)
                ]
                self._cache.write(
                    team=team, block_id=lib_id, version=version,
                    vars_hash=vh, columns=cols, rows=rows, sql=sql,
                )
                log.info(
                    "scheduler: warmed cache for %s (rows=%d, vars=%s)",
                    lib_id, len(rows), list(resolved.keys()),
                )
            except Exception:
                log.warning("scheduler: cache write failed for %s", lib_id, exc_info=True)

        self._dispatcher.enqueue(
            cache_key=cache_key, fetch=_fetch, on_success=_store,
        )

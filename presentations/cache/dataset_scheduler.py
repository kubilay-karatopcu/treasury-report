"""Faz A — dataset-level refresh scheduler (Hazırlık'a taşınan cron).

Where the legacy ``LibraryRefreshScheduler`` warms one cache entry *per block*,
this scheduler refreshes one parquet *per dataset*: it walks every presentation's
latest scope, finds basket datasets with ``refresh.kind == "scheduled"`` and
``routing.decision == "cached"``, and — when due — materialises the dataset's
Oracle result to S3 parquet via :func:`presentations.scope.materialize.materialize_dataset`.

Because N Sunum charts reference ONE dataset alias (not N block queries), the
expensive query runs once per interval regardless of how many charts draw from
it — the deduplication the block-level model lacked.

Single-pod daemon thread (same model as the library scheduler); materialisations
run on a :class:`RefreshDispatcher` thread pool so a slow (multi-minute) query
doesn't block the poll loop, and the dispatcher dedups concurrent runs of the
same dataset by key.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from presentations.cache.scheduler import LibraryRefreshScheduler
from presentations.scope.materialize import materialize_dataset, read_dataset_meta

log = logging.getLogger(__name__)


def _dataset_due(refresh, last_refreshed: datetime | None, *, now: datetime) -> bool:
    """True iff a dataset with policy ``refresh`` should be re-materialised now.

    ``last_refreshed`` / ``now`` are naive-UTC (matching ``DatasetMeta.refreshed_at``).
    Schedule (time-of-day) mode takes precedence; interval mode is the fallback.
    """
    sched = refresh.schedule
    if sched is not None:
        target = LibraryRefreshScheduler._last_scheduled_target(  # noqa: SLF001
            times=list(sched.times),
            days=list(sched.days) or None,
            tz_name=sched.timezone,
            now=now,
        )
        if target is None:
            return False
        return last_refreshed is None or last_refreshed < target

    interval = refresh.interval_seconds
    if interval is None:
        return False
    if last_refreshed is None:
        return True
    return (now - last_refreshed).total_seconds() >= interval


class DatasetScheduler:
    """Single-thread polling scheduler for cached scope datasets."""

    def __init__(
        self, *,
        scope_store,              # S3ScopeStore / LocalScopeStore
        data_client,              # DataClient (Oracle/fake)
        dispatcher,               # RefreshDispatcher (thread pool + dedup)
        catalog=None,             # optional scope Catalog (partition pushdown)
        concept_registry=None,    # optional (concept pushdown)
        binding_catalog=None,     # optional (concept pushdown)
        poll_interval_seconds: int = 60,
        name: str = "dataset-refresh-scheduler",
    ):
        self._scopes = scope_store
        self._dc = data_client
        self._dispatcher = dispatcher
        self._catalog = catalog
        self._registry = concept_registry
        self._binding = binding_catalog
        self._poll = max(10, int(poll_interval_seconds))
        self._name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            log.warning("dataset scheduler already started")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        log.info("dataset refresh scheduler started (poll=%ss)", self._poll)

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=5)
        log.info("dataset refresh scheduler stopped")

    # ── Loop ──────────────────────────────────────────────────────────

    def _run(self) -> None:
        time.sleep(min(5, self._poll))
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.warning("dataset scheduler tick crashed", exc_info=True)
            self._stop.wait(self._poll)

    def tick(self) -> int:
        """One scan over all presentations' latest scopes. Returns the number of
        dataset materialisations enqueued this tick (useful for tests)."""
        try:
            pids = self._scopes.list_presentations()
        except Exception:
            log.warning("dataset scheduler: list_presentations failed", exc_info=True)
            return 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        enqueued = 0
        for pid in pids:
            try:
                scope = self._scopes.load_latest(pid)
            except Exception:
                log.warning("dataset scheduler: load_latest failed for %s", pid, exc_info=True)
                continue
            if scope is None:
                continue
            for item in scope.basket:
                if self._maybe_materialize(scope, item, now=now):
                    enqueued += 1
        return enqueued

    # ── Internals ─────────────────────────────────────────────────────

    def _maybe_materialize(self, scope, item, *, now: datetime) -> bool:
        rp = item.refresh
        # All three source kinds (table_ref / sql / derived aggregate) are
        # materialisable: a derived dataset's aggregate result is persisted to
        # its own parquet, resolving its sources from their parquet (or pulling
        # them once if absent). So any cached + scheduled item is eligible.
        if (
            rp is None
            or rp.kind != "scheduled"
            or item.routing.decision != "cached"
        ):
            return False

        meta = read_dataset_meta(self._dc, scope.presentation_id, item.alias)
        last = meta.refreshed_dt() if meta else None
        if not _dataset_due(rp, last, now=now):
            return False

        # Bind loop vars now — the closure runs later in a worker thread.
        _scope, _item = scope, item

        def _fetch():
            return materialize_dataset(
                self._dc, _scope, _item,
                catalog=self._catalog,
                concept_registry=self._registry,
                binding_catalog=self._binding,
            )

        self._dispatcher.enqueue(
            cache_key=f"dataset:{scope.presentation_id}:{item.alias}",
            fetch=_fetch,
        )
        return True

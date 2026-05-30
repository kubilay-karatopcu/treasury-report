"""Phase B — Background refetch dispatcher for the library block cache.

When a request hits the apply-filters loop and finds a *stale* library cache
entry, it returns the stale rows immediately AND enqueues a background fetch
here. The next reader of the same block sees the fresh data.

Design:

- ``ThreadPoolExecutor`` with a small worker pool (default 2) — Oracle/DuckDB
  fetches are I/O-bound, so threads are fine; we don't need processes.
- **Dedup**: a single ``in_flight`` set keyed by ``cache_key`` ensures that
  10 concurrent stale hits for the same block trigger exactly one fetch.
- **Failure isolation**: a fetch exception is logged, never raised back. The
  stale entry stays in the cache so the next viewer still gets *something*;
  the next fetch attempt retries.

The dispatcher is created once per Flask app and attached via
``app.config["LIBRARY_REFRESH_DISPATCHER"]``. ``shutdown()`` is called on
app teardown so worker threads don't leak between gunicorn reloads.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# Type aliases — keep them light, no Pydantic import here to avoid cycles.
FetchFn = Callable[[], Any]   # zero-arg closure that performs the Oracle/DuckDB fetch
StoreFn = Callable[[Any], None]  # callback invoked with the fetch result


class RefreshDispatcher:
    """Thread-pool wrapper that deduplicates concurrent fetches per key."""

    def __init__(self, *, max_workers: int = 2, name: str = "lib-block-refresh"):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=name,
        )
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._stats = {"enqueued": 0, "skipped_dup": 0, "failed": 0, "succeeded": 0}

    def enqueue(
        self, *, cache_key: str, fetch: FetchFn, on_success: StoreFn | None = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> bool:
        """Schedule a fetch unless one is already in flight for this key.

        Returns True if a fresh job was enqueued, False if it was deduped.
        """
        with self._lock:
            if cache_key in self._in_flight:
                self._stats["skipped_dup"] += 1
                log.debug("refresh_dispatcher: dedup skip key=%s", cache_key)
                return False
            self._in_flight.add(cache_key)
            self._stats["enqueued"] += 1

        def _worker():
            try:
                result = fetch()
                if on_success is not None:
                    on_success(result)
                with self._lock:
                    self._stats["succeeded"] += 1
                log.info("refresh_dispatcher: refresh done key=%s", cache_key)
            except BaseException as exc:  # noqa: BLE001 — log everything, swallow
                with self._lock:
                    self._stats["failed"] += 1
                log.warning(
                    "refresh_dispatcher: refresh failed key=%s err=%s",
                    cache_key, exc, exc_info=True,
                )
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception:
                        log.warning("refresh_dispatcher: on_error handler failed",
                                    exc_info=True)
            finally:
                with self._lock:
                    self._in_flight.discard(cache_key)

        self._executor.submit(_worker)
        return True

    def in_flight(self, cache_key: str) -> bool:
        with self._lock:
            return cache_key in self._in_flight

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats) | {"in_flight": len(self._in_flight)}

    def shutdown(self, *, wait: bool = False) -> None:
        """Stop accepting new work. ``wait=True`` blocks for outstanding jobs.

        Default is non-blocking so a development reload doesn't hang for a
        2-minute query to finish."""
        try:
            self._executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            # Python < 3.9 → no cancel_futures kwarg.
            self._executor.shutdown(wait=wait)
        log.info("refresh_dispatcher: shutdown wait=%s stats=%s", wait, self.stats())

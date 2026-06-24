"""Cooperative cancellation for a build job (Oturum 2, Karar B — REAL abort).

A single :class:`CancelToken` per build job is created in ``build_scope_async``
and threaded through ``_run_build_core`` → ``fetch_cached_tables`` (both fetch
passes) → each ``dc.get_data`` / ``_pull_source_into_duck`` / python node. Two
cancellation levels:

* **Loop level** — the fetch passes call :meth:`check` at item/pass boundaries;
  a cancelled token raises :class:`BuildCancelled`, which unwinds the worker and
  (critically) releases ``session._exec_lock`` so the user can immediately
  re-enter / re-build (the B2/B3 freeze was an orphaned worker holding that lock).
* **Connection level** — :meth:`bind` registers a live DB connection so
  :meth:`cancel` can abort an in-flight Oracle query mid-fetch
  (``Connection.cancel()`` then ``close()``), covering the "one 5-minute table"
  case. If the driver doesn't expose ``cancel()``, ``close()`` still aborts most
  blocked calls — both are best-effort and guarded.

:meth:`cancel` is idempotent and thread-safe: it runs on the cancel-endpoint
thread while the worker thread is inside the fetch loop.
"""
from __future__ import annotations

import threading


class BuildCancelled(Exception):
    """Raised inside the build/fetch loop when its CancelToken is cancelled."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._conns: list = []

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Raise :class:`BuildCancelled` if cancelled. Cheap — call at loop bounds."""
        if self._event.is_set():
            raise BuildCancelled()

    def bind(self, conn) -> None:
        """Register a live DB connection so :meth:`cancel` can abort its in-flight
        query. If the token is ALREADY cancelled, abort this connection at once
        (it was opened during a cancel race)."""
        with self._lock:
            self._conns.append(conn)
            already = self._event.is_set()
        if already:
            self._abort_conn(conn)

    def unbind(self, conn) -> None:
        """Drop a connection from the registry (its fetch finished normally)."""
        with self._lock:
            try:
                self._conns.remove(conn)
            except ValueError:
                pass

    def cancel(self) -> None:
        """Set the flag and abort every bound connection. Idempotent, thread-safe."""
        self._event.set()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            self._abort_conn(c)

    @staticmethod
    def _abort_conn(conn) -> None:
        # Best-effort: cancel the in-flight call, then close. oracledb exposes
        # Connection.cancel() (safe cross-thread) + close(); guard both since the
        # bound object may be a stub / already-closed / a non-Oracle connection.
        for meth in ("cancel", "close"):
            try:
                fn = getattr(conn, meth, None)
                if callable(fn):
                    fn()
            except Exception:
                pass

"""Oturum 2.1 — CancelToken primitive (cooperative build cancellation)."""
from __future__ import annotations

import pytest

from presentations.scope.cancel import BuildCancelled, CancelToken


def test_check_raises_only_after_cancel():
    t = CancelToken()
    assert t.cancelled is False
    t.check()                      # no-op before cancel
    t.cancel()
    assert t.cancelled is True
    with pytest.raises(BuildCancelled):
        t.check()


def test_cancel_aborts_bound_connections():
    class _Conn:
        def __init__(self):
            self.cancelled = self.closed = False
        def cancel(self):
            self.cancelled = True
        def close(self):
            self.closed = True

    t = CancelToken()
    c = _Conn()
    t.bind(c)
    t.cancel()
    assert c.cancelled and c.closed


def test_bind_after_cancel_aborts_immediately():
    class _Conn:
        def __init__(self):
            self.cancelled = False
        def cancel(self):
            self.cancelled = True
        def close(self):
            pass

    t = CancelToken()
    t.cancel()
    c = _Conn()
    t.bind(c)                      # opened during a cancel race → aborted at once
    assert c.cancelled


def test_cancel_is_idempotent_and_guards_bad_conns():
    class _BadConn:
        def cancel(self):
            raise RuntimeError("boom")   # must be swallowed
        def close(self):
            raise RuntimeError("boom2")

    t = CancelToken()
    t.bind(_BadConn())
    t.cancel()
    t.cancel()                     # second cancel is a no-op, no raise
    assert t.cancelled


def test_unbind_removes_connection():
    class _Conn:
        def __init__(self):
            self.cancelled = False
        def cancel(self):
            self.cancelled = True
        def close(self):
            pass

    t = CancelToken()
    c = _Conn()
    t.bind(c)
    t.unbind(c)
    t.cancel()
    assert c.cancelled is False    # already unbound (fetch finished) → not touched

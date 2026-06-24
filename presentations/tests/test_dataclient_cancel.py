"""Oturum 2.4 — DataClient.get_data: cancel-token + connection close (leak fix).

PROD fetch yolu artık (a) HER zaman conn.close() (finally — önceden sızıyordu),
(b) cancel_token verilirse conn'u token'a bind eder; iptalde bound conn.cancel()
+ in-flight kesilirse BuildCancelled yükselir.
"""
from __future__ import annotations

import pytest

pytest.importorskip("oracledb")          # DataClient importu oracledb gerektirir

from DataClient import DataClient                        # noqa: E402
from presentations.scope.cancel import BuildCancelled, CancelToken  # noqa: E402


class _Cur:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.description = [("A",)]

    def execute(self, q, p):
        pass

    def fetchmany(self, n):
        return self._chunks.pop(0) if self._chunks else []


class _Conn:
    def __init__(self, cur):
        self._cur = cur
        self.cancelled = False
        self.closed = False

    def cursor(self):
        return self._cur

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


def _dc(conn):
    dc = DataClient.__new__(DataClient)      # __init__'i atla (Oracle config yok)
    dc.APP_ENV = "PROD"
    dc.get_connection = lambda: conn
    return dc


def test_get_data_always_closes_connection():
    conn = _Conn(_Cur([[(1,), (2,)]]))
    df = _dc(conn).get_data(dataset="x", query="SELECT A FROM T")
    assert list(df["A"]) == [1, 2]
    assert conn.closed is True               # leak fix: finally close


def test_get_data_cancel_raises_buildcancelled_and_closes():
    conn = _Conn(_Cur([[(1,)]]))
    tok = CancelToken()
    tok.cancel()                              # pre-cancelled
    with pytest.raises(BuildCancelled):
        _dc(conn).get_data(dataset="x", query="SELECT A FROM T", cancel_token=tok)
    assert conn.cancelled is True             # bind-after-cancel aborted it
    assert conn.closed is True                # finally still closed

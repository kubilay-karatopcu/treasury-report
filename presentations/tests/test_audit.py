"""Oturum 8 (H1) — audit log (PRISMA_AUDIT_LOG): queue + batch Oracle insert,
best-effort, CLOB clipping, lazy singleton."""
from __future__ import annotations

from presentations.audit import AuditLogger, _MAX_CLOB


class _Cur:
    def __init__(self):
        self.sql = None
        self.rows = None

    def executemany(self, sql, rows):
        self.sql = sql
        self.rows = list(rows)


class _Conn:
    def __init__(self, cur):
        self._c = cur
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._c

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class _DC:
    def __init__(self):
        self.cur = _Cur()
        self.conn = _Conn(self.cur)
        self.calls = 0

    def get_connection(self):
        self.calls += 1
        return self.conn


def _logger(dc, **kw):
    return AuditLogger(dc, enabled=True, start_worker=False, **kw)  # no worker → flush senkron


def test_log_then_flush_writes_row():
    dc = _DC()
    lg = _logger(dc)
    lg.log("llm_chat", user_sicil="A16438", presentation_id="p1", request_id="t1",
           stage="sunum", prompt="merhaba", llm_response="oldu", duration_ms=123)
    assert lg.flush() == 1
    assert dc.conn.committed and dc.conn.closed
    row = dc.cur.rows[0]
    assert row["user_sicil"] == "A16438" and row["event_type"] == "llm_chat"
    assert row["prompt"] == "merhaba" and row["llm_response"] == "oldu"
    assert row["duration_ms"] == 123
    # tüm insert kolonları bind'de olmalı (executemany named binds)
    from presentations.audit import _INSERT_COLS
    assert set(row.keys()) == set(_INSERT_COLS)


def test_batches_multiple_events_in_one_insert():
    dc = _DC()
    lg = _logger(dc)
    for i in range(5):
        lg.log("evt", user_sicil="A", prompt=f"p{i}")
    assert lg.flush() == 5
    assert dc.calls == 1                     # tek bağlantı, tek executemany
    assert len(dc.cur.rows) == 5


def test_huge_clob_is_clipped():
    dc = _DC()
    lg = _logger(dc)
    lg.log("evt", prompt="x" * (_MAX_CLOB + 5000))
    lg.flush()
    assert len(dc.cur.rows[0]["prompt"]) == _MAX_CLOB


def test_meta_dict_serialised_to_json():
    dc = _DC()
    lg = _logger(dc)
    lg.log("evt", meta={"k": 1, "v": [2, 3]})
    lg.flush()
    import json
    assert json.loads(dc.cur.rows[0]["meta_json"]) == {"k": 1, "v": [2, 3]}


def test_best_effort_no_connection_does_not_raise():
    class _NoConnDC:  # get_connection yok (DEV/stub)
        pass
    lg = _logger(_NoConnDC())
    lg.log("evt", prompt="y")
    assert lg.flush() == 1                    # drained; yazım sessiz no-op
    lg.close()


def test_best_effort_swallows_insert_error():
    class _BoomDC:
        def get_connection(self):
            class _C:
                def cursor(self):
                    raise RuntimeError("ORA-boom")
                def close(self):
                    pass
            return _C()
    lg = _logger(_BoomDC())
    lg.log("evt", prompt="y")
    lg.flush()                                # raise ETMEMELİ
    lg.close()


def test_log_event_never_raises_without_app_context():
    from presentations import audit
    audit._reset_for_test(None)
    audit.log_event("evt", prompt="y")        # current_app yok → yutulur, raise yok


def test_sql_text_logged_for_generated_code():
    """B1 (N3) — LLM'in ürettiği kod (patch'ler/öneriler) sql_text'e yazılır."""
    dc = _DC()
    lg = _logger(dc)
    lg.log("llm_chat", user_sicil="A1", prompt="grafik ekle",
           llm_response="2 blok eklendi",
           sql_text='[{"op":"add","path":"/blocks/-","value":{}}]')
    lg.flush()
    row = dc.cur.rows[0]
    assert row["sql_text"] == '[{"op":"add","path":"/blocks/-","value":{}}]'
    assert row["llm_response"] == "2 blok eklendi"

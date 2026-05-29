import pandas as pd
import pytest

from presentations.session import PresentationSession, SessionRegistry


# ── PresentationSession ──────────────────────────────────────────────────────

class TestPresentationSession:
    def test_session_dirs(self, tmp_path):
        sess = PresentationSession("u1", "p1", tmp_path)
        assert sess.session_dir == tmp_path / "u1" / "p1"
        assert sess.duckdb_path == tmp_path / "u1" / "p1" / "session.duckdb"

    def test_manifest_persistence_roundtrip(self, tmp_path):
        sess = PresentationSession("u1", "p1", tmp_path)
        # Nested-shape manifest (post-Phase 8 schema).
        manifest = {
            "id": "p1", "version": 1,
            "blocks": [{
                "id": "h1", "type": "section_header", "title": "T",
                "locked": False, "children": [],
            }],
        }
        sess.set_manifest(manifest)

        sess2 = PresentationSession("u1", "p1", tmp_path)
        loaded = sess2.get_manifest()
        assert loaded == manifest

    def test_get_manifest_fallback(self, tmp_path):
        sess = PresentationSession("u1", "p1", tmp_path)
        fallback = {"id": "p1", "version": 1}
        m = sess.get_manifest(fallback=fallback)
        assert m == fallback
        # Fallback was persisted
        assert sess.manifest_path.exists()

    def test_basket_signature_change_detection(self, tmp_path):
        sess = PresentationSession("u1", "p1", tmp_path)
        b1 = [{"table": "T1", "columns": ["a"], "row_filter": None}]
        b2 = [{"table": "T2", "columns": ["b"], "row_filter": None}]

        assert sess.needs_refetch(b1) is True
        assert sess.needs_refetch([]) is True

        sess._last_basket_signature = sess.basket_signature(b1)
        assert sess.needs_refetch(b1) is False
        assert sess.needs_refetch(b2) is True

    def test_fetch_basket_registers_views(self, tmp_path):
        sess = PresentationSession("u1", "p1", tmp_path)

        class FakeDC:
            def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
                return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

        loaded = sess.fetch_basket(
            FakeDC(),
            [{"table": "EDW.DEMO_TABLE", "columns": ["a", "b"], "row_filter": None}],
        )
        assert "demo_table" in loaded
        assert loaded["demo_table"]["rows"] == 3
        assert "demo_table" in sess.loaded_views()
        sess.close()


# ── SessionRegistry ──────────────────────────────────────────────────────────

class TestSessionRegistry:
    def test_get_or_create_returns_same_instance(self, tmp_path):
        reg = SessionRegistry(tmp_path)
        s1 = reg.get_or_create("u1", "p1")
        s2 = reg.get_or_create("u1", "p1")
        assert s1 is s2

    def test_different_users_get_separate_sessions(self, tmp_path):
        reg = SessionRegistry(tmp_path)
        s1 = reg.get_or_create("u1", "p1")
        s2 = reg.get_or_create("u2", "p1")
        assert s1 is not s2
        assert s1.user_id != s2.user_id

    def test_cleanup_idle_closes_old_sessions(self, tmp_path):
        reg = SessionRegistry(tmp_path, idle_timeout=1)
        sess = reg.get_or_create("u1", "p1")
        # Touch in the past
        sess._last_used = 0
        closed = reg.cleanup_idle()
        assert closed == 1
        assert ("u1", "p1") not in reg._sessions

    def test_close_all_clears_registry(self, tmp_path):
        reg = SessionRegistry(tmp_path)
        reg.get_or_create("u1", "p1")
        reg.get_or_create("u2", "p1")
        reg.close_all()
        assert reg._sessions == {}


# ── Per-session DuckDB execution lock (thread-safety) ────────────────────────

def test_duck_conn_reentrant_and_thread_safe(tmp_path):
    """duck_conn() must be reentrant (nested acquire on one thread) and must
    serialise concurrent access to the non-thread-safe DuckDB connection so many
    threads can't corrupt each other's in-flight results."""
    import threading

    sess = PresentationSession("u1", "p1", tmp_path, dc=object())

    # Reentrancy: a nested acquire on the same thread must not deadlock, and the
    # yielded connection is the same shared object both times.
    with sess.duck_conn() as c1:
        with sess.duck_conn() as c2:
            assert c1 is c2
            c1.execute("CREATE TABLE t (x INTEGER)")
            c1.execute("INSERT INTO t VALUES (1), (2), (3)")

    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(25):
                with sess.duck_conn() as conn:
                    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    sess.close()

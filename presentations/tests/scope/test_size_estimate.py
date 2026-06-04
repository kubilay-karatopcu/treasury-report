"""Tests for filter-aware EXPLAIN PLAN size estimation (madde 4).

Covers the pure helpers (fingerprint, store, cardinality→bytes) plus the
``/scope/refine-sizes`` endpoint wiring with a fake DataClient + a synchronous
dispatcher, so no Oracle is touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.scope import size_estimate as se
from presentations.scope.schema import load_scope_yaml, scope_to_dict
from presentations.scope.store import LocalScopeStore
from presentations.table_docs.store import CachedTableDocStore, LocalTableDocStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CONCEPTS_DIR = REPO_ROOT / "presentations" / "catalog" / "concepts"
TABLES_DIR = REPO_ROOT / "presentations" / "catalog" / "tables"
SAMPLE_SCOPE = REPO_ROOT / "examples" / "phase_8" / "sample_scope.yaml"


# ── fingerprint ─────────────────────────────────────────────────────────────

def test_fingerprint_is_stable_and_value_sensitive():
    sql = "SELECT * FROM EDW.T WHERE C = :a"
    assert se.fingerprint(sql, {"a": 1}) == se.fingerprint(sql, {"a": 1})
    # Bind value changes the fingerprint…
    assert se.fingerprint(sql, {"a": 1}) != se.fingerprint(sql, {"a": 2})
    # …and so does the SQL (predicate columns / projection live in the SQL text).
    assert se.fingerprint("SELECT 1", {}) != se.fingerprint("SELECT 2", {})
    # Surrounding whitespace is normalised away (strip), so it doesn't matter.
    assert se.fingerprint(sql, {"a": 1}) == se.fingerprint("  " + sql + "  ", {"a": 1})


def test_fingerprint_ignores_bind_order():
    sql = "SELECT * FROM T WHERE A = :a AND B = :b"
    assert se.fingerprint(sql, {"a": 1, "b": 2}) == se.fingerprint(sql, {"b": 2, "a": 1})


# ── SizeEstimateStore ───────────────────────────────────────────────────────

def test_store_get_put_roundtrip():
    store = se.SizeEstimateStore()
    assert store.get("k") is None
    store.put("k", rows=10, estimated_bytes=160, source="explain_plan")
    got = store.get("k")
    assert got["rows"] == 10 and got["estimated_bytes"] == 160
    assert got["source"] == "explain_plan"


def test_store_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(se.time, "time", lambda: clock["t"])
    store = se.SizeEstimateStore(ttl_seconds=60)
    store.put("k", rows=1, estimated_bytes=1, source="explain_plan")
    clock["t"] = 1059.0
    assert store.get("k") is not None        # still fresh
    clock["t"] = 1061.0
    assert store.get("k") is None            # expired → dropped


def test_store_eviction_drops_oldest(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(se.time, "time", lambda: clock["t"])
    store = se.SizeEstimateStore(max_entries=2)
    clock["t"] = 1.0; store.put("a", rows=1, estimated_bytes=1, source="x")
    clock["t"] = 2.0; store.put("b", rows=1, estimated_bytes=1, source="x")
    clock["t"] = 3.0; store.put("c", rows=1, estimated_bytes=1, source="x")
    assert store.get("a") is None            # oldest evicted
    assert store.get("b") is not None
    assert store.get("c") is not None


# ── explain_plan_rows / estimate_bytes_via_explain ──────────────────────────

class _FakeCursor:
    def __init__(self, cardinality):
        self._card = cardinality
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self._card,) if self._card is not None else None


class _FakeConn:
    def __init__(self, cardinality):
        self._cur = _FakeCursor(cardinality)
        self.closed = False

    def cursor(self):
        return self._cur

    def close(self):
        self.closed = True


class _FakeDC:
    def __init__(self, cardinality):
        self._card = cardinality
        self.last_conn = None

    def get_connection(self):
        self.last_conn = _FakeConn(self._card)
        return self.last_conn


def test_explain_plan_rows_reads_root_cardinality():
    dc = _FakeDC(4200)
    rows = se.explain_plan_rows(dc, "SELECT * FROM T WHERE C = :a", {"a": 1})
    assert rows == 4200
    # Two statements ran (EXPLAIN PLAN + PLAN_TABLE read) and the connection was
    # closed (dropping the session-private GTT rows — no manual cleanup needed).
    assert len(dc.last_conn._cur.executed) == 2
    assert "EXPLAIN PLAN" in dc.last_conn._cur.executed[0][0]
    assert dc.last_conn.closed is True


def test_explain_plan_rows_none_without_get_connection():
    class _Stub:  # DEV / fake DataClient — no Oracle path.
        pass
    assert se.explain_plan_rows(_Stub(), "SELECT 1", {}) is None


def test_explain_plan_rows_none_when_cardinality_null():
    assert se.explain_plan_rows(_FakeDC(None), "SELECT 1", {}) is None


def test_explain_plan_rows_swallows_errors():
    class _BoomDC:
        def get_connection(self):
            raise RuntimeError("ORA-12541")
    assert se.explain_plan_rows(_BoomDC(), "SELECT 1", {}) is None


def test_estimate_bytes_multiplies_rows_by_width():
    dc = _FakeDC(100)
    out = se.estimate_bytes_via_explain(dc, "SELECT * FROM T", {}, bytes_per_row=50)
    assert out == {"rows": 100, "estimated_bytes": 5000}


def test_estimate_bytes_none_when_no_cardinality():
    assert se.estimate_bytes_via_explain(_FakeDC(None), "SELECT 1", {}, 50) is None


# ── /scope/refine-sizes endpoint ────────────────────────────────────────────

class _SyncDispatcher:
    """Runs jobs inline so the test sees results without thread timing."""

    def __init__(self):
        self.calls = []

    def enqueue(self, *, cache_key, fetch, on_success=None, on_error=None):
        self.calls.append(cache_key)
        res = fetch()
        if on_success is not None:
            on_success(res)
        return True


class _FakeUser(UserMixin):
    name = "kubilay"
    sicil = "A16438"
    department = "Treasury"

    def get_id(self):
        return self.sicil


@pytest.fixture
def app(tmp_path: Path):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        LOGIN_DISABLED=True,
        SCOPE_STORE=LocalScopeStore(tmp_path / "scopes"),
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(CONCEPTS_DIR),
        TABLE_DOC_STORE=CachedTableDocStore(LocalTableDocStore(base_dir=TABLES_DIR)),
        DATA_CLIENT=_FakeDC(777),
        LIBRARY_REFRESH_DISPATCHER=_SyncDispatcher(),
        SIZE_ESTIMATE_STORE=se.SizeEstimateStore(),
    )
    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):
        return _FakeUser()

    @app.before_request
    def _force_login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_body():
    return scope_to_dict(load_scope_yaml(SAMPLE_SCOPE.read_text(encoding="utf-8")))


def test_refine_sizes_enqueues_then_serves_cached(client, sample_body):
    # First call: store is cold → both raw tables reported as pending. The
    # synchronous dispatcher populated the store during the request.
    r1 = client.post("/presentations/p_abc123/scope/refine-sizes", json=sample_body)
    assert r1.status_code == 200, r1.get_data(as_text=True)
    d1 = r1.get_json()
    assert d1["ok"] is True
    assert set(d1["pending"]) == {"positions", "branch_dim"}
    assert d1["estimates"] == {}

    # Second call: the same fingerprints now hit the cache → estimates filled,
    # nothing pending.
    r2 = client.post("/presentations/p_abc123/scope/refine-sizes", json=sample_body)
    d2 = r2.get_json()
    assert d2["pending"] == []
    assert set(d2["estimates"]) == {"positions", "branch_dim"}
    for est in d2["estimates"].values():
        assert est["rows"] == 777
        assert est["source"] == "explain_plan"
        assert est["estimated_bytes"] > 0


def test_refine_sizes_requires_scope(client):
    r = client.post("/presentations/p_abc123/scope/refine-sizes", json={})
    assert r.status_code == 400


def test_refine_sizes_no_oracle_path_is_graceful(app):
    # A DataClient without get_connection (DEV stub) → nothing enqueued, no
    # estimates, no pending, but a clean 200.
    app.config["DATA_CLIENT"] = object()
    client = app.test_client()
    body = scope_to_dict(load_scope_yaml(SAMPLE_SCOPE.read_text(encoding="utf-8")))
    r = client.post("/presentations/p_abc123/scope/refine-sizes", json=body)
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert d["pending"] == [] and d["estimates"] == {}

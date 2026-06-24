"""Faz 4 — /scope/source-sql (uniform Kaynak) + union-over-SQL-node preview fix.

- source-sql HER node tipi için kanonik kaynağı döndürür (table_ref → Oracle
  SELECT, derived → derlenmiş SQL, sql → ham SQL, python → script).
- _preview_sample_into_duck artık SQL-node kaynaklarını aggregation_gate'ten
  geçirir → trailing ';' / boş gövde sarmadan kaynaklı ')' hatası giderildi.
"""
from __future__ import annotations

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


class _RecordingDC:
    """Sabit örnek döndürür ama çalıştırılan query'leri kaydeder (wrap doğrulama)."""

    def __init__(self, df):
        self._df = df
        self.queries: list[str] = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        self.queries.append(query or "")
        return self._df.copy()


@pytest.fixture
def dc():
    return _RecordingDC(pd.DataFrame({"X": [1, 2], "Y": [3, 4]}))


@pytest.fixture
def client(dc, tmp_path):
    from presentations.session import SessionRegistry
    app = Flask(__name__)
    # Oturum 1: preview-derivation uses the session SAMPLE DuckDB → SESSION_REGISTRY
    # (isolated tmp dir per test so cached samples don't bleed across runs).
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True, DATA_CLIENT=dc,
                      SESSION_REGISTRY=SessionRegistry(dc, duck_base_dir=tmp_path))
    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):
        return _FakeUser()

    @app.before_request
    def _login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app.test_client()


def _scope_with_sql_union(sql):
    """deposits (main) + a manual-SQL node + a union of the two."""
    return {
        "presentation_id": "p_t", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-01T00:00:00Z",
        "basket": [
            {"alias": "deposits", "table_ref": {"schema": "EDW", "name": "DEPOSITS"},
             "projection": {"columns": ["X", "Y"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 10}},
            {"alias": "extra", "sql": sql,
             "projection": {"columns": ["X", "Y"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 10}},
            {"alias": "merged", "derivation": {
                "kind": "union", "source_aliases": ["deposits", "extra"], "union_all": True},
             "projection": {"columns": [], "include_all": True},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    }


# ── Bug fix: union over a manual-SQL node with a trailing ';' ────────────────

def test_union_over_sql_node_strips_semicolon(client, dc):
    # Trailing ';' eskiden `SELECT * FROM (SELECT ... ;) _src` → parse error verirdi.
    scope = _scope_with_sql_union("SELECT X, Y FROM SOME_TABLE;")
    r = client.post("/presentations/p_t/scope/preview-derivation",
                    json={"scope": scope, "alias": "merged"})
    data = r.get_json()
    assert data["ok"] is True, data
    # SQL-node örneklemesi gate'ten geçti → çalıştırılan query'de trailing ';' yok
    # ve boş subquery '()' yok.
    sql_q = next((q for q in dc.queries if "SOME_TABLE" in q), "")
    assert sql_q, dc.queries
    assert ";" not in sql_q
    assert "()" not in sql_q


def test_union_over_empty_sql_node_gives_clear_error(client):
    scope = _scope_with_sql_union("   ")  # boş SQL
    r = client.post("/presentations/p_t/scope/preview-derivation",
                    json={"scope": scope, "alias": "merged"})
    data = r.get_json()
    assert data["ok"] is False
    # Kriptik ')' yerine net mesaj.
    assert any("SQL" in e for e in (data.get("errors") or []))


# ── source-sql: uniform Kaynak ──────────────────────────────────────────────

def _post_source(client, scope, alias):
    return client.post("/presentations/p_t/scope/source-sql",
                       json={"scope": scope, "alias": alias}).get_json()


def test_source_sql_table_ref_returns_oracle_select(client):
    scope = _scope_with_sql_union("SELECT X, Y FROM T")
    d = _post_source(client, scope, "deposits")
    assert d["ok"] and d["kind"] == "oracle" and d["editable"] is False
    assert "SELECT" in d["sql"].upper() and "DEPOSITS" in d["sql"].upper()


def test_source_sql_sql_node_returns_authored_sql_editable(client):
    scope = _scope_with_sql_union("SELECT X, Y FROM MYTABLE")
    d = _post_source(client, scope, "extra")
    assert d["ok"] and d["kind"] == "sql" and d["editable"] is True
    assert "MYTABLE" in d["sql"].upper()


def test_source_sql_union_returns_compiled(client):
    scope = _scope_with_sql_union("SELECT X, Y FROM T")
    d = _post_source(client, scope, "merged")
    assert d["ok"] and d["kind"] == "union" and d["editable"] is False
    assert "UNION" in d["sql"].upper()


def test_source_sql_python_returns_code(client):
    scope = _scope_with_sql_union("SELECT X, Y FROM T")
    scope["basket"].append({
        "alias": "py1", "derivation": {
            "kind": "python", "source_alias": "deposits",
            "python_code": "output_node_df = input_node_df", "output_columns": []},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    d = _post_source(client, scope, "py1")
    assert d["ok"] and d["kind"] == "python" and d["editable"] is True
    assert "output_node_df" in d["code"]


def test_source_sql_unknown_alias_400(client):
    scope = _scope_with_sql_union("SELECT X FROM T")
    r = client.post("/presentations/p_t/scope/source-sql",
                    json={"scope": scope, "alias": "nope"})
    assert r.status_code == 400


# ── Önizleme cache (yavaşlık fix) ───────────────────────────────────────────

def test_preview_sql_caches_repeat_calls(client, dc):
    from presentations.routes_scope import _PREVIEW_CACHE
    _PREVIEW_CACHE.clear()
    body = {"sql": "SELECT X, Y FROM CACHE_ME"}
    r1 = client.post("/presentations/p_t/scope/preview-sql", json=body).get_json()
    assert r1["ok"] is True and not r1.get("cached")
    n_after_first = len([q for q in dc.queries if "CACHE_ME" in q])
    r2 = client.post("/presentations/p_t/scope/preview-sql", json=body).get_json()
    assert r2["ok"] is True and r2.get("cached") is True
    # İkinci çağrı Oracle'a GİTMEDİ — query sayısı artmadı.
    assert len([q for q in dc.queries if "CACHE_ME" in q]) == n_after_first

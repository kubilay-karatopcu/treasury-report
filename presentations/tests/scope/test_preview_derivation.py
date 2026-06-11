"""HTTP test for /scope/preview-derivation.

Regression: a `calculated` derivation (LLM `create_calculation` — window
functions, multi-source joins) used to error "Kaynak tablo basketta yok" in the
Hazırlık drawer because the in-browser path only handled `aggregate`
(source_alias). The server now samples the sources into DuckDB and runs the
compiled SQL, so window functions evaluate correctly.
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


class _FakeDC:
    """Returns a fixed sample for any source query (no Oracle round-trip)."""

    def __init__(self, df):
        self._df = df

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        return self._df.copy()


@pytest.fixture
def client():
    df = pd.DataFrame({
        "RES_ID":  [1, 1, 1, 2, 2],
        "CUST_ID": [10, 10, 10, 20, 20],
        "REV_NO":  [1, 2, 3, 5, 7],
        "AMT":     [100, 200, 300, 400, 500],
    })
    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
                      DATA_CLIENT=_FakeDC(df))
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


def _scope():
    return {
        "presentation_id": "p_test", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-01T00:00:00Z",
        "basket": [
            {"alias": "res",
             "table_ref": {"schema": "ODS_TREASURY", "name": "RES"},
             "projection": {"columns": ["RES_ID", "CUST_ID", "REV_NO", "AMT"],
                            "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system",
                         "estimated_bytes": 1000}},
            {"alias": "res_max",
             "derivation": {
                 "kind": "calculated", "source_aliases": ["res"],
                 "columns": [
                     {"name": "RES_ID", "expr": "RES_ID"},
                     {"name": "REV_NO", "expr": "REV_NO"},
                     {"name": "RN", "expr":
                      "ROW_NUMBER() OVER (PARTITION BY RES_ID, CUST_ID "
                      "ORDER BY REV_NO DESC)"},
                 ]},
             "projection": {"columns": ["RES_ID", "REV_NO", "RN"],
                            "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system",
                         "estimated_bytes": 0}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    }


def test_calculated_preview_runs_via_duckdb(client):
    r = client.post("/presentations/p_test/scope/preview-derivation",
                    json={"scope": _scope(), "alias": "res_max"})
    data = r.get_json()
    assert data["ok"] is True, data
    assert data["derived"] is True
    assert data["data_columns"] == ["RES_ID", "REV_NO", "RN"]
    # Window function evaluated server-side: the max REV_NO per
    # (RES_ID, CUST_ID) gets RN=1 — proving this is real SQL, not a raw dump.
    by = {(row[0], row[1]): row[2] for row in data["rows"]}
    assert by[(1, 3)] == 1
    assert by[(2, 7)] == 1


def test_unknown_alias_is_400(client):
    r = client.post("/presentations/p_test/scope/preview-derivation",
                    json={"scope": _scope(), "alias": "nope"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


# ── join / union preview (Hazırlık ER) ───────────────────────────────────────
# Regression: join/union nodes used to fall into the in-browser *aggregate*
# branch (expects singular source_alias) → "Kaynak tablo basketta yok". They now
# route to this endpoint, which samples sources (recursively for derived ones)
# and runs the compiled SQL. The response also carries `sql` for the "Kaynak
# Query" drawer tab.

def _two_main_scope(deriv):
    return {
        "presentation_id": "p_test", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-01T00:00:00Z",
        "basket": [
            {"alias": "res", "table_ref": {"schema": "ODS_TREASURY", "name": "RES"},
             "projection": {"columns": ["RES_ID", "CUST_ID", "REV_NO", "AMT"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000}},
            {"alias": "res2", "table_ref": {"schema": "ODS_TREASURY", "name": "RES2"},
             "projection": {"columns": ["RES_ID", "CUST_ID", "REV_NO", "AMT"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000}},
            deriv,
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    }


def test_join_preview_runs_and_returns_sql(client):
    scope = _two_main_scope({
        "alias": "res_join", "derivation": {
            "kind": "join", "source_aliases": ["res", "res2"],
            "join_keys": [{"left_alias": "res", "left_column": "RES_ID",
                           "right_alias": "res2", "right_column": "RES_ID"}],
            "join_type": "inner"},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    data = client.post("/presentations/p_test/scope/preview-derivation",
                       json={"scope": scope, "alias": "res_join"}).get_json()
    assert data["ok"] is True, data
    assert data.get("sql")  # Kaynak Query tab
    # Right-side collisions prefixed with the alias (compile_join_sql rule).
    assert "res2_RES_ID" in data["data_columns"]


def test_union_preview_runs(client):
    scope = _two_main_scope({
        "alias": "res_union", "derivation": {
            "kind": "union", "source_aliases": ["res", "res2"], "union_all": True},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    data = client.post("/presentations/p_test/scope/preview-derivation",
                       json={"scope": scope, "alias": "res_union"}).get_json()
    assert data["ok"] is True, data
    assert data["row_count"] == 10  # 5 + 5, UNION ALL


def test_join_on_derived_source_resolves(client):
    # One source (res_max) is itself a calculated node → preview must RECURSIVELY
    # sample it. The old shallow loop rejected derived sources outright.
    scope = _scope()  # res (main) + res_max (calculated)
    scope["basket"].append({
        "alias": "res3", "table_ref": {"schema": "ODS_TREASURY", "name": "RES3"},
        "projection": {"columns": ["RES_ID", "CUST_ID", "REV_NO", "AMT"], "include_all": False},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000}})
    scope["basket"].append({
        "alias": "nested_join", "derivation": {
            "kind": "join", "source_aliases": ["res_max", "res3"],
            "join_keys": [{"left_alias": "res_max", "left_column": "RES_ID",
                           "right_alias": "res3", "right_column": "RES_ID"}],
            "join_type": "inner"},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    data = client.post("/presentations/p_test/scope/preview-derivation",
                       json={"scope": scope, "alias": "nested_join"}).get_json()
    assert data["ok"] is True, data
    assert data.get("sql")


def test_filter_on_union_source_returns_rows(client):
    # Regression: filtering a union/join node returned 0 rows — filter-preview sent
    # the DuckDB filter SQL to Oracle (no such view) → empty. It now samples the
    # union into DuckDB and filters there.
    scope = _two_main_scope({
        "alias": "res_union", "derivation": {
            "kind": "union", "source_aliases": ["res", "res2"], "union_all": True},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    scope["basket"].append({
        "alias": "res_union_f", "derivation": {
            "kind": "filter", "source_alias": "res_union",
            "filters": {"pinned": [], "raw": [
                {"id": "rf_amt", "alias": "res_union", "column": "AMT", "op": "gt", "value": 250}]}},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0}})
    data = client.post("/presentations/p_test/scope/filter-preview",
                       json={"scope": scope, "alias": "res_union_f"}).get_json()
    assert data["ok"] is True, data
    # 5 rows/source (AMT 100..500) → UNION ALL = 10; AMT > 250 keeps 300/400/500
    # from each source = 6. Non-empty proves the filter ran in DuckDB, not Oracle.
    assert data["row_count"] == 6, data

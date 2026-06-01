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

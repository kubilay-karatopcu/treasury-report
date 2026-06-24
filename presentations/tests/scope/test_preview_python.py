"""HTTP test for /scope/preview-python (Faz P-2).

Bir python node'unu tasarım anında çalıştırır: source örneklenir, sandbox'ta
`input_node_df` → `output_node_df` koşulur, örnek satırlar döner. Hatalar (statik
validate / runtime) phase etiketiyle 400 döner.
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
    def __init__(self, df):
        self._df = df

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        return self._df.copy()


@pytest.fixture
def client(tmp_path):
    from presentations.session import SessionRegistry
    df = pd.DataFrame({
        "BRANCH_CODE": ["A", "A", "B"],
        "BALANCE_TRY": [1000, 2000, 5000],
    })
    fake_dc = _FakeDC(df)
    app = Flask(__name__)
    # Oturum 1.6: python preview kaynağı kalıcı session sample DuckDB'sine
    # örneklenir → SESSION_REGISTRY (izole tmp dir per test).
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
                      DATA_CLIENT=fake_dc,
                      SESSION_REGISTRY=SessionRegistry(fake_dc, duck_base_dir=tmp_path))
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
            {"alias": "deposits",
             "table_ref": {"schema": "EDW", "name": "DEPOSITS"},
             "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    }


def _post(client, **body):
    return client.post("/presentations/p_test/scope/preview-python", json=body)


def test_preview_python_success(client):
    code = "output_node_df = input_node_df.groupby('BRANCH_CODE', as_index=False)['BALANCE_TRY'].sum()"
    data = _post(client, scope=_scope(), source_alias="deposits", python_code=code).get_json()
    assert data["ok"] is True, data
    assert data["derived"] is True
    assert set(data["data_columns"]) == {"BRANCH_CODE", "BALANCE_TRY"}
    by = {row[0]: row[1] for row in data["rows"]}
    assert by["A"] == 3000 and by["B"] == 5000


def test_preview_python_validation_error(client):
    r = _post(client, scope=_scope(), source_alias="deposits",
              python_code="import os\noutput_node_df = input_node_df")
    assert r.status_code == 400
    data = r.get_json()
    assert data["ok"] is False
    assert data["phase"] == "validate"
    assert any("import yasak" in e for e in data["errors"])


def test_preview_python_runtime_error(client):
    r = _post(client, scope=_scope(), source_alias="deposits",
              python_code="output_node_df = input_node_df['NOPE']")
    assert r.status_code == 400
    data = r.get_json()
    assert data["phase"] == "python"
    assert any("KeyError" in e for e in data["errors"])


def test_preview_python_missing_output(client):
    r = _post(client, scope=_scope(), source_alias="deposits",
              python_code="x = input_node_df.head()")
    assert r.status_code == 400
    assert r.get_json()["phase"] == "validate"


def test_preview_python_unknown_source_is_400(client):
    r = _post(client, scope=_scope(), source_alias="nope",
              python_code="output_node_df = input_node_df")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_preview_python_isolation_no_other_node(client):
    # Başka bir node adına erişim NameError vermeli (yalnız input_node_df enjekte).
    r = _post(client, scope=_scope(), source_alias="deposits",
              python_code="output_node_df = some_other_node")
    data = r.get_json()
    assert data["phase"] == "python"
    assert any("NameError" in e for e in data["errors"])


def test_preview_python_caches_result(client):
    from presentations.routes_scope import _PREVIEW_CACHE
    _PREVIEW_CACHE.clear()
    body = dict(scope=_scope(), source_alias="deposits",
                python_code="output_node_df = input_node_df.head(2)")
    r1 = _post(client, **body).get_json()
    assert r1["ok"] is True and not r1.get("cached")
    r2 = _post(client, **body).get_json()
    assert r2["ok"] is True and r2.get("cached") is True
    assert r2["data_columns"] == r1["data_columns"]

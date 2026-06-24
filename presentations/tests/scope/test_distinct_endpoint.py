"""F2 — get_distinct column flag + /scope/distinct endpoint.

The Filtreleme tab offers checkbox lists for string columns flagged
``get_distinct`` in the table doc. The endpoint serves the nightly sample when
present (no Oracle hit) and otherwise a capped live SELECT DISTINCT; it refuses
columns that aren't flagged.
"""
from __future__ import annotations

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.routes_library import _form_to_table_doc_dict, _table_doc_to_form
from presentations.table_docs.schema import load_table_doc_from_dict


DOC = load_table_doc_from_dict({
    "table": "RES", "schema": "ODS_TREASURY",
    "columns": {
        "SEG": {"type": "VARCHAR2(10)", "filterable": True, "filter_role": "dimension",
                "get_distinct": True,
                "distinct_values_sample": ["RETAIL", "SME", "CORP"],
                "distinct_values_sampled_at": "2026-05-01T00:00:00"},
        "SEG2": {"type": "VARCHAR2(10)", "filterable": True, "filter_role": "dimension",
                 "get_distinct": True},
        "NAME": {"type": "VARCHAR2(40)"},
    },
})


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


class _DocStore:
    def load(self, schema, table):
        return DOC


class _FakeDC:
    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        return pd.DataFrame({"V": ["A", "B", "C"]})


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
                      TABLE_DOC_STORE=_DocStore(), DATA_CLIENT=_FakeDC())
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


def _get(client, column):
    return client.get(f"/presentations/p/scope/distinct"
                      f"?schema=ODS_TREASURY&table=RES&column={column}")


def test_distinct_prefers_sample(client):
    data = _get(client, "SEG").get_json()
    assert data["ok"] is True
    assert data["source"] == "sample"
    assert data["values"] == ["RETAIL", "SME", "CORP"]


def test_distinct_live_when_no_sample(client):
    data = _get(client, "SEG2").get_json()
    assert data["ok"] is True
    assert data["source"] == "live"
    assert data["values"] == ["A", "B", "C"]


def test_distinct_refused_when_flag_off(client):
    r = _get(client, "NAME")
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_get_distinct_roundtrips_through_form():
    form = _table_doc_to_form(DOC)
    seg = next(c for c in form["columns"] if c["name"] == "SEG")
    assert seg["get_distinct"] is True
    back = _form_to_table_doc_dict("ODS_TREASURY", "RES", form)
    doc2 = load_table_doc_from_dict(back)
    assert doc2.columns["SEG"].get_distinct is True
    assert doc2.columns["NAME"].get_distinct is False

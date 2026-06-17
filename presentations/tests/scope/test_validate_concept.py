"""#4 — /scope/validate-concept: kullanıcının kolona bağladığı concept'in
distinct değerlerle uygunluğunu test eder (enum örtüşmesi / tarih / sayısallık)."""
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


class _FakeConcept:
    def __init__(self, cid, ctype, codes):
        self.id = cid
        self.type = ctype
        self.name = cid
        self._codes = codes

    def canonical_codes(self):
        return self._codes


class _FakeRegistry:
    def __init__(self, concepts):
        self._c = concepts

    def all_concepts(self):
        return self._c


@pytest.fixture
def client():
    df = pd.DataFrame({
        "CCY": ["TRY", "USD", "EUR", "TRY"],
        "AMT": [100, 200, 300, 400],
        "JUNK": ["zzz", "qqq", "www", "vvv"],
    })
    reg = _FakeRegistry([
        _FakeConcept("currency", "enum", ["TRY", "USD", "EUR", "GBP"]),
        _FakeConcept("amount", "scalar", []),
    ])
    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
                      DATA_CLIENT=_FakeDC(df), CONCEPT_REGISTRY=reg)
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
        "presentation_id": "p_t", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-01T00:00:00Z",
        "basket": [
            {"alias": "deposits", "table_ref": {"schema": "EDW", "name": "DEPOSITS"},
             "projection": {"columns": ["CCY", "AMT", "JUNK"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 10}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    }


def _post(client, **b):
    return client.post("/presentations/p_t/scope/validate-concept", json=b).get_json()


def test_good_enum_match_is_ok(client):
    d = _post(client, scope=_scope(), alias="deposits", column="CCY", concept="currency")
    assert d["ok"] is True
    assert d["level"] == "ok"
    assert d["match_ratio"] == 1.0


def test_bad_enum_match_warns(client):
    # JUNK değerleri currency canonical setinde yok → uyarı.
    d = _post(client, scope=_scope(), alias="deposits", column="JUNK", concept="currency")
    assert d["ok"] is True
    assert d["level"] == "warn"
    assert d["match_ratio"] < 0.5
    assert "uyumsuz" in d["message"].lower() or "eşleş" in d["message"].lower()


def test_scalar_numeric_ok(client):
    d = _post(client, scope=_scope(), alias="deposits", column="AMT", concept="amount")
    assert d["ok"] is True and d["level"] == "ok"


def test_scalar_on_text_warns(client):
    d = _post(client, scope=_scope(), alias="deposits", column="CCY", concept="amount")
    assert d["level"] == "warn"


def test_unknown_concept_400(client):
    r = client.post("/presentations/p_t/scope/validate-concept",
                    json={"scope": _scope(), "alias": "deposits", "column": "CCY", "concept": "nope"})
    assert r.status_code == 400


def test_bad_column_ident_rejected(client):
    r = client.post("/presentations/p_t/scope/validate-concept",
                    json={"scope": _scope(), "alias": "deposits", "column": "C; DROP", "concept": "currency"})
    assert r.status_code == 400

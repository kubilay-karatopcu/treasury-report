"""Phase 7.a — /concepts/api/* route tests (Flask test client)."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


def _make_app(registry) -> Flask:
    app = Flask(__name__)
    app.config.update(SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
                      CONCEPT_REGISTRY=registry)
    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):
        return _FakeUser()

    @app.before_request
    def _force_login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    # Blueprint may already be registered by another test app in the same
    # process — guard against the double-register error.
    if "presentations" not in app.blueprints:
        app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def registry() -> ConceptRegistry:
    return ConceptRegistry.from_dicts([
        {"version": 1, "scope": "global", "concepts": [
            {"id": "currency", "name": "Para Birimi", "type": "enum",
             "canonical_values": [{"code": "USD", "aliases": ["US Dollar"]}]},
            {"id": "as_of_time", "name": "Snapshot", "type": "time",
             "granularity_default": "day"},
        ]},
        {"version": 1, "scope": "dept:treasury", "concepts": [
            {"id": "maturity", "name": "Vade", "type": "bucket"},
        ]},
    ])


@pytest.fixture
def client(registry):
    return _make_app(registry).test_client()


def test_list_returns_all_concepts(client):
    resp = client.get("/presentations/concepts/api/list")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 3
    ids = {c["id"] for c in body["concepts"]}
    assert ids == {"currency", "as_of_time", "maturity"}


def test_list_orders_global_before_dept(client):
    body = client.get("/presentations/concepts/api/list").get_json()
    scopes = [c["scope"] for c in body["concepts"]]
    # global concepts come before dept ones.
    assert scopes.index("global") < scopes.index("dept:treasury")


def test_list_scope_filter(client):
    body = client.get("/presentations/concepts/api/list?scope=dept:treasury").get_json()
    assert body["count"] == 1
    assert body["concepts"][0]["id"] == "maturity"


def test_get_concept_ok(client):
    body = client.get("/presentations/concepts/api/currency").get_json()
    c = body["concept"]
    assert c["id"] == "currency"
    assert c["canonical_values"][0]["code"] == "USD"


def test_get_concept_404(client):
    resp = client.get("/presentations/concepts/api/nope")
    assert resp.status_code == 404


def test_list_no_registry_configured():
    app = _make_app(None)
    resp = app.test_client().get("/presentations/concepts/api/list")
    assert resp.status_code == 200
    assert resp.get_json() == {"concepts": [], "count": 0}

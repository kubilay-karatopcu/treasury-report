"""HTTP tests for the temporary scope endpoints (Phase 8.a, DoD #4).

Spins up a tiny Flask app with the blueprint, LOGIN_DISABLED, a fake user, a
filesystem scope store, and the *real* concept registry + table-doc catalog so
``examples/phase_8/sample_scope.yaml`` is validated against production-shaped
metadata.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.scope.schema import load_scope_yaml, scope_to_dict
from presentations.scope.store import LocalScopeStore
from presentations.table_docs.store import CachedTableDocStore, LocalTableDocStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CONCEPTS_DIR = REPO_ROOT / "presentations" / "catalog" / "concepts"
TABLES_DIR = REPO_ROOT / "presentations" / "catalog" / "tables"
SAMPLE_SCOPE = REPO_ROOT / "examples" / "phase_8" / "sample_scope.yaml"


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
    # JSON-safe shape (ISO date strings) of the sample scope.
    return scope_to_dict(load_scope_yaml(SAMPLE_SCOPE.read_text(encoding="utf-8")))


def test_post_accepts_and_validates_sample(client, sample_body):
    resp = client.post("/presentations/p_abc123/scope", json=sample_body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["version"] == 1
    assert data["presentation_id"] == "p_abc123"


def test_get_latest_after_save(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    resp = client.get("/presentations/p_abc123/scope")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["scope"]["presentation_id"] == "p_abc123"
    assert body["scope"]["version"] == 1


def test_get_specific_version(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    resp = client.get("/presentations/p_abc123/scope/1")
    assert resp.status_code == 200
    assert resp.get_json()["scope"]["version"] == 1


def test_post_bumps_version(client, sample_body):
    v1 = client.post("/presentations/p_abc123/scope", json=sample_body).get_json()["version"]
    v2 = client.post("/presentations/p_abc123/scope", json=sample_body).get_json()["version"]
    assert (v1, v2) == (1, 2)


def test_get_missing_returns_404(client):
    assert client.get("/presentations/p_nope/scope").status_code == 404


def test_get_missing_version_returns_404(client, sample_body):
    client.post("/presentations/p_abc123/scope", json=sample_body)
    assert client.get("/presentations/p_abc123/scope/9").status_code == 404


def test_invalid_scope_rejected(client, sample_body):
    bad = json.loads(json.dumps(sample_body))
    # Duplicate the basket alias → rule 1 error.
    bad["scope"]["basket"][1]["alias"] = bad["scope"]["basket"][0]["alias"]
    resp = client.post("/presentations/p_abc123/scope", json=bad)
    assert resp.status_code == 400
    errors = resp.get_json()["errors"]
    assert any("Duplicate basket alias" in e for e in errors)


def test_malformed_body_rejected(client):
    resp = client.post("/presentations/p_abc123/scope", json={"scope": {"version": "not-an-int"}})
    assert resp.status_code == 400

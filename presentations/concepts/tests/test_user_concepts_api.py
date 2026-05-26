"""Phase 7.d — per-presentation user concept endpoints (route-level)."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

import presentations
from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


class _StubSession:
    def __init__(self, manifest):
        self._m = manifest

    def get_manifest(self):
        return self._m

    def set_manifest(self, m):
        self._m = m


class _StubRegistry:
    """Per-pid session store — separate manifests prove P↔Q isolation."""
    def __init__(self):
        self._by_pid: dict[str, _StubSession] = {}

    def get_or_create(self, user, pid):
        return self._by_pid.setdefault(pid, _StubSession({"id": pid, "version": 1}))


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        SESSION_REGISTRY=_StubRegistry(),
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(
            Path(presentations.__file__).parent / "catalog" / "concepts"),
        CONCEPT_CATALOG_ROOT=str(tmp_path),
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

    if "presentations" not in app.blueprints:
        app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


_DESK = {"id": "desk", "name": "Masa", "type": "enum",
         "canonical_values": [{"code": "FX"}, {"code": "RATES"}]}


def test_list_includes_base(client):
    body = client.get("/presentations/p_1/concepts").get_json()
    ids = {c["id"] for c in body["concepts"]}
    assert "currency" in ids and "maturity" in ids
    assert body["user_concepts"] == []


def test_add_user_concept(client):
    resp = client.post("/presentations/p_1/concepts", json=_DESK)
    assert resp.status_code == 200, resp.data
    body = client.get("/presentations/p_1/concepts").get_json()
    assert "desk" in {c["id"] for c in body["concepts"]}
    desk = next(c for c in body["concepts"] if c["id"] == "desk")
    assert desk["scope"] == "user"
    assert body["user_concepts"][0]["id"] == "desk"


def test_collision_rejected(client):
    resp = client.post("/presentations/p_1/concepts",
                       json={"id": "currency", "name": "X", "type": "enum"})
    assert resp.status_code == 400
    assert resp.get_json()["kind"] == "user_concept"


def test_user_concept_invisible_across_presentations(client):
    client.post("/presentations/p_1/concepts", json=_DESK)
    # Presentation p_2 must NOT see p_1's user concept (§11.d acceptance).
    body = client.get("/presentations/p_2/concepts").get_json()
    assert "desk" not in {c["id"] for c in body["concepts"]}


def test_delete_user_concept(client):
    client.post("/presentations/p_1/concepts", json=_DESK)
    resp = client.delete("/presentations/p_1/concepts/desk")
    assert resp.status_code == 200
    body = client.get("/presentations/p_1/concepts").get_json()
    assert "desk" not in {c["id"] for c in body["concepts"]}


def test_delete_unknown_404(client):
    assert client.delete("/presentations/p_1/concepts/nope").status_code == 404


def test_promote_records_intent(client, app):
    client.post("/presentations/p_1/concepts", json=_DESK)
    resp = client.post("/presentations/p_1/concepts/desk/promote", json={})
    assert resp.status_code == 200
    entry = resp.get_json()["promotion"]
    assert entry["concept_id"] == "desk"
    assert entry["status"] == "pending"
    # Ledger written under the catalog root.
    from presentations.concepts.promotions import load_promotions
    ledger = load_promotions(Path(app.config["CONCEPT_CATALOG_ROOT"]))
    assert any(e["concept_id"] == "desk" for e in ledger)


def test_promote_unknown_404(client):
    assert client.post("/presentations/p_1/concepts/ghost/promote", json={}).status_code == 404

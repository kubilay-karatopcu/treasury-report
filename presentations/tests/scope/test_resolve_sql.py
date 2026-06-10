"""Faz R4/#1 — /scope/resolve-sql: parse a query into a source-table node plan."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.table_docs.store import CachedTableDocStore, LocalTableDocStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CONCEPTS_DIR = REPO_ROOT / "presentations" / "catalog" / "concepts"
TABLES_DIR = REPO_ROOT / "presentations" / "catalog" / "tables"


class _FakeUser(UserMixin):
    name = "kubilay"; sicil = "A16438"; department = "Treasury"
    def get_id(self):
        return self.sicil


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test", TESTING=True, LOGIN_DISABLED=True,
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(CONCEPTS_DIR),
        TABLE_DOC_STORE=CachedTableDocStore(LocalTableDocStore(base_dir=TABLES_DIR)),
    )
    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):
        return _FakeUser()

    @app.before_request
    def _force():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app.test_client()


def test_resolve_extracts_source_tables(client):
    r = client.post("/presentations/p_x/scope/resolve-sql",
                    json={"sql": "SELECT a, b FROM EDW.SOME_TABLE WHERE a = 1"})
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["ok"] is True
    ids = {t["id"] for t in d["source_tables"]}
    assert "EDW.SOME_TABLE" in ids


def test_resolve_flags_undocumented(client):
    r = client.post("/presentations/p_x/scope/resolve-sql",
                    json={"sql": "SELECT * FROM EDW.NOT_DOCUMENTED_XYZ"})
    d = r.get_json()
    t = next(t for t in d["source_tables"] if t["id"] == "EDW.NOT_DOCUMENTED_XYZ")
    assert t["documented"] is False
    assert any("dökümante değil" in w for w in d["warnings"])


def test_resolve_rejects_non_select(client):
    r = client.post("/presentations/p_x/scope/resolve-sql",
                    json={"sql": "DELETE FROM EDW.T"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_resolve_requires_sql(client):
    r = client.post("/presentations/p_x/scope/resolve-sql", json={})
    assert r.status_code == 400

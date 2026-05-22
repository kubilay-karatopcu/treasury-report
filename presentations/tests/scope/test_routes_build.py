"""HTTP tests for the Phase 8.b Hazırlık route + 'Sunum'a geç' build flow."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.session import SessionRegistry
from presentations.scope.store import LocalScopeStore
from presentations.table_docs.store import CachedTableDocStore, LocalTableDocStore

REPO_ROOT = Path(__file__).resolve().parents[3]
CONCEPTS_DIR = REPO_ROOT / "presentations" / "catalog" / "concepts"
TABLES_DIR = REPO_ROOT / "presentations" / "catalog" / "tables"
SAMPLE_CATALOG = REPO_ROOT / "examples" / "sample_catalog.json"


class _FakeUser(UserMixin):
    sicil = "A16438"
    department = "Treasury"
    def get_id(self): return self.sicil


class FakeDC:
    """Doubles as the S3 client (manifest I/O) and the Oracle DataClient."""
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    # S3 surface used by PresentationSession.
    def read_json(self, key):
        if key not in self.objects:
            raise FileNotFoundError(f"NoSuchKey: {key}")
        import json
        return json.loads(self.objects[key])
    def _upload_bytes(self, key, body, content_type=None):
        self.objects[key] = body
    def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]
    def delete_file(self, key):
        self.objects.pop(key, None)

    # Oracle surface used by fetch_cached_tables.
    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None):
        return pd.DataFrame({"AS_OF_DATE": ["2025-10-01"], "CCY": ["TRY"], "BRANCH_ID": ["402"]})


@pytest.fixture
def app(tmp_path):
    dc = FakeDC()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test", TESTING=True, LOGIN_DISABLED=True,
        SCOPE_STORE=LocalScopeStore(tmp_path / "scopes"),
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
        DATA_CLIENT=dc,
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(CONCEPTS_DIR),
        TABLE_DOC_STORE=CachedTableDocStore(LocalTableDocStore(base_dir=TABLES_DIR)),
        CATALOG_PATH=str(SAMPLE_CATALOG),
    )
    lm = LoginManager(app)
    @lm.user_loader
    def _load(_id): return _FakeUser()
    @app.before_request
    def _force():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())
    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _scope_body():
    return {"scope": {
        "presentation_id": "p_build", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["AS_OF_DATE", "BRANCH_ID", "CCY"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
        }],
        "filters": {"pinned": [], "interactive": []},
    }}


# ── Hazırlık page ────────────────────────────────────────────────────────────

def test_hazirlik_page_renders(client):
    resp = client.get("/presentations/p_demo/scope")  # no scope yet → 404 on raw GET
    assert resp.status_code == 404
    page = client.get("/presentations/hazirlik/p_new")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "hazirlik-data" in body
    assert "hazirlik.bundle.js" in body


# ── Build flow ───────────────────────────────────────────────────────────────

def test_build_validates_fetches_saves_and_redirects(client, app):
    resp = client.post("/presentations/p_build/scope/build", json=_scope_body())
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["scope_version"] == 1
    assert data["cached_tables"] == ["positions"]
    assert data["lazy_tables"] == []
    assert data["redirect"].endswith("/presentations/p_build")

    # Scope persisted, status ready, cached view recorded.
    saved = app.config["SCOPE_STORE"].load_latest("p_build")
    assert saved.status.state == "ready"
    assert saved.status.cached_tables == ["positions"]

    # Manifest now carries scope_ref.
    sess = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p_build")
    manifest = sess.get_manifest()
    assert manifest["scope_ref"] == {"presentation_id": "p_build", "scope_version": 1}


def test_build_rejects_invalid_scope(client):
    body = _scope_body()
    body["scope"]["basket"].append(dict(body["scope"]["basket"][0]))  # duplicate alias
    resp = client.post("/presentations/p_build/scope/build", json=body)
    assert resp.status_code == 400
    assert any("Duplicate basket alias" in e for e in resp.get_json()["errors"])


def test_editor_shows_scope_banner_after_build(client):
    client.post("/presentations/p_build/scope/build", json=_scope_body())
    page = client.get("/presentations/p_build").get_data(as_text=True)
    assert 'class="scope-banner"' in page
    assert "Scope v1" in page
    assert "/presentations/hazirlik/p_build" in page

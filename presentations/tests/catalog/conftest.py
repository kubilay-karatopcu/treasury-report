"""Shared fixtures for Phase 9.a catalog tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from presentations.catalog.loader import CatalogLoader
from presentations.table_docs.store import LocalTableDocStore


REPO_ROOT = Path(__file__).resolve().parents[3]
TABLE_DOCS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "table_docs"


@pytest.fixture(scope="session")
def table_docs_dir() -> Path:
    return TABLE_DOCS_DIR


@pytest.fixture
def fixture_store(tmp_path) -> LocalTableDocStore:
    """A clean LocalTableDocStore copy of the example fixtures.

    We copy into ``tmp_path`` so individual tests can mutate the store
    without polluting siblings.
    """
    target = tmp_path / "table_docs"
    target.mkdir()
    for schema_dir in TABLE_DOCS_DIR.iterdir():
        if not schema_dir.is_dir():
            continue
        dst = target / schema_dir.name
        dst.mkdir()
        for yaml_file in schema_dir.glob("*.yaml"):
            (dst / yaml_file.name).write_bytes(yaml_file.read_bytes())
    return LocalTableDocStore(base_dir=target)


@pytest.fixture
def empty_store(tmp_path) -> LocalTableDocStore:
    """An empty store for negative-path tests."""
    target = tmp_path / "empty_docs"
    target.mkdir()
    return LocalTableDocStore(base_dir=target)


class _FakeDC:
    """Filesystem-backed minimal stub mirroring the FakeDataClient surface
    used by run_local.py — enough for uploads-related code paths.
    """

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def list_prefix(self, prefix: str) -> list[str]:
        base = self.root / prefix.lstrip("/")
        if not base.exists():
            return []
        out = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(f.relative_to(self.root).as_posix())
        return out

    def read_json(self, key: str):
        p = self._path(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return json.loads(p.read_text(encoding="utf-8"))

    def read_yaml(self, key: str):
        import yaml
        p = self._path(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return yaml.safe_load(p.read_text(encoding="utf-8"))

    def _upload_bytes(self, key: str, body: bytes, content_type: str | None = None):
        self._path(key).write_bytes(body)

    def delete_file(self, key: str):
        p = self._path(key)
        if p.exists():
            p.unlink()


@pytest.fixture
def fake_dc(tmp_path) -> _FakeDC:
    return _FakeDC(root=tmp_path / "fake_s3")


@pytest.fixture
def loader(fixture_store, fake_dc) -> CatalogLoader:
    return CatalogLoader(
        table_doc_store=fixture_store,
        data_client=fake_dc,
        ttl_seconds=300,  # long TTL in tests; explicit invalidate when needed
    )


@pytest.fixture
def empty_loader(empty_store, fake_dc) -> CatalogLoader:
    return CatalogLoader(table_doc_store=empty_store, data_client=fake_dc)


# ── Mini Flask app helpers ────────────────────────────────────────────────


@pytest.fixture
def flask_app(fixture_store, fake_dc):
    """Build a minimal Flask app with the catalog API blueprint loaded.

    Uses LOGIN_DISABLED + a before_request that pins ``current_user`` to a
    fake A16438 user. Mirrors examples/run_local.py's pattern.
    """
    from flask import Flask
    from flask_login import LoginManager, UserMixin, login_user

    # Import the blueprint package — this triggers route registration via
    # presentations/__init__.py which imports catalog.api and routes_kesif.
    # Side-effect: requires SESSION_REGISTRY in app.config for the Keşif
    # routes (not used by the catalog endpoints themselves).
    from presentations import presentations_bp
    from presentations.session import SessionRegistry
    # Phase 11.wrap: kesif.html now extends home/_base_prisma.html, so any
    # test that hits a route rendering that template needs prisma_home_bp
    # registered too.
    from prisma_home import prisma_home_bp

    class FakeUser(UserMixin):
        sicil = "A16438"
        name = "kubilay"
        department = "Treasury"

        def get_id(self):
            return self.sicil

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        LOGIN_DISABLED=True,
        TABLE_DOC_STORE=fixture_store,
        DATA_CLIENT=fake_dc,
        PRESENTATIONS_CATALOG_TTL_SECONDS=300,
        PRESENTATIONS_SESSION_DIR=str(fake_dc.root / "sessions"),
    )
    app.config["SESSION_REGISTRY"] = SessionRegistry(
        dc=fake_dc,
        duck_base_dir=app.config["PRESENTATIONS_SESSION_DIR"],
        idle_timeout=1800,
    )

    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(uid):
        return FakeUser()

    @app.before_request
    def force_login():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    app.register_blueprint(prisma_home_bp)
    return app


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()

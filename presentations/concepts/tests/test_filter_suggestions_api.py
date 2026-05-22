"""Phase 7 — /<pid>/concepts/filter-suggestions (concept-aware filter proposals)."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

import presentations
from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import CachedBindingCatalog


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
    def __init__(self, manifest):
        self._s = _StubSession(manifest)

    def get_or_create(self, user, pid):
        return self._s


def _make_app(manifest):
    catalog_dir = Path(presentations.__file__).parent / "catalog"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        SESSION_REGISTRY=_StubRegistry(manifest),
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(catalog_dir / "concepts"),
        CONCEPT_BINDING_CATALOG=CachedBindingCatalog(catalog_dir / "tables",
                                                     check_interval_s=0.0),
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


def _block(source_tables):
    return {"id": "b1", "type": "bar_chart", "query": "SELECT 1 WHERE {{concept_filters}}",
            "source_tables": source_tables}


def test_suggests_concepts_from_source_tables():
    # FX_SWAP_DEALS binds currency, maturity, trade_time, value_time, ... (catalog).
    manifest = {"id": "p1", "version": 1, "filters": [],
                "blocks": [{"id": "s", "type": "section_header",
                            "children": [_block([{"schema": "ODS_TREASURY", "table": "FX_SWAP_DEALS"}])]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    tags = {s["semantic_tag"] for s in body["suggestions"]}
    assert "currency" in tags
    assert "maturity" in tags


def test_currency_proposal_shape():
    manifest = {"id": "p1", "version": 1, "filters": [],
                "blocks": [{"id": "s", "type": "section_header",
                            "children": [_block([{"schema": "ODS_TREASURY", "table": "FX_SWAP_DEALS"}])]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    cur = next(s for s in body["suggestions"] if s["semantic_tag"] == "currency")
    assert cur["type"] == "enum_multi"
    assert "USD" in cur["allowed_values"]
    assert cur["source"] == "concept"


def test_time_concept_is_date_range():
    manifest = {"id": "p1", "version": 1, "filters": [],
                "blocks": [{"id": "s", "type": "section_header",
                            "children": [_block([{"schema": "ODS_TREASURY", "table": "FX_SWAP_DEALS"}])]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    tt = next(s for s in body["suggestions"] if s["semantic_tag"] == "trade_time")
    assert tt["type"] == "date_range"
    assert tt["default"] == {"from": "today - 30d", "to": "today"}


def test_excludes_existing_filters():
    manifest = {"id": "p1", "version": 1,
                "filters": [{"id": "f_currency", "semantic_tag": "currency", "type": "enum_multi"}],
                "blocks": [{"id": "s", "type": "section_header",
                            "children": [_block([{"schema": "ODS_TREASURY", "table": "FX_SWAP_DEALS"}])]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    tags = {s["semantic_tag"] for s in body["suggestions"]}
    assert "currency" not in tags     # already on the dashboard
    assert "maturity" in tags


def test_no_source_tables_empty():
    manifest = {"id": "p1", "version": 1, "filters": [],
                "blocks": [{"id": "s", "type": "section_header",
                            "children": [{"id": "b1", "type": "bar_chart", "query": "SELECT 1"}]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    assert body["suggestions"] == []


def test_from_clause_fallback_when_no_source_tables():
    # Block omitted source_tables but its SQL has FROM ODS_TREASURY.FX_SWAP_DEALS
    # → derived from the FROM clause, suggestions still appear.
    manifest = {"id": "p1", "version": 1, "filters": [],
                "blocks": [{"id": "s", "type": "section_header", "children": [{
                    "id": "b1", "type": "bar_chart",
                    "query": "SELECT CCY, SUM(NOTIONAL_TRY) FROM ODS_TREASURY.FX_SWAP_DEALS WHERE {{concept_filters}} GROUP BY CCY",
                }]}]}
    client = _make_app(manifest).test_client()
    body = client.get("/presentations/p1/concepts/filter-suggestions").get_json()
    tags = {s["semantic_tag"] for s in body["suggestions"]}
    assert "currency" in tags          # derived FX_SWAP_DEALS → currency binding

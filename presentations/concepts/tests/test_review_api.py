"""Phase 7.c.3 — review API route tests (full HTTP path incl. catalog reload)."""
from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

import presentations
from presentations import presentations_bp
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import CachedBindingCatalog
from presentations.table_docs.schema import load_table_doc_from_dict
from presentations.llm import FakeLLM


class _FakeUser(UserMixin):
    sicil = "A16438"

    def get_id(self):
        return self.sicil


class _StubDocStore:
    def __init__(self, doc):
        self._doc = doc

    def load(self, schema, table):
        return self._doc

    def list_tables(self, schema=None):
        return [("ODS_X", "T")]


@pytest.fixture
def app(tmp_path):
    (tmp_path / "tables").mkdir()
    doc = load_table_doc_from_dict({
        "table": "T", "schema": "ODS_X",
        "columns": {
            "CCY": {"type": "CHAR(3)", "filterable": True, "filter_role": "dimension",
                    "suggested_variable": "ccy", "suggested_semantic_tag": "currency",
                    "distinct_values_sample": ["TRY", "USD", "EUR"],
                    "distinct_values_sampled_at": "2026-05-19T03:00:00Z"},
            "NET_POSITION": {"type": "NUMBER", "aggregatable": True},
        },
    })
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        CONCEPT_REGISTRY=ConceptRegistry.from_dir(
            Path(presentations.__file__).parent / "catalog" / "concepts"),
        CONCEPT_BINDING_CATALOG=CachedBindingCatalog(tmp_path / "tables",
                                                     check_interval_s=0.0),
        CONCEPT_CATALOG_ROOT=str(tmp_path),
        TABLE_DOC_STORE=_StubDocStore(doc),
        LLM_CLIENT=FakeLLM(),
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


def test_queue_lists_currency_proposal(client):
    resp = client.get("/presentations/concepts/review/api/queue?schema=ODS_X&table=T")
    assert resp.status_code == 200
    body = resp.get_json()
    cols = {row["column"] for row in body["queue"]}
    assert "CCY" in cols
    assert "NET_POSITION" not in cols


def test_inference_run_post(client):
    resp = client.post("/presentations/concepts/inference/run",
                       json={"schema": "ODS_X", "table": "T"})
    assert resp.status_code == 200
    assert resp.get_json()["count"] >= 1


def test_approve_then_queue_drops_column(client, app, tmp_path):
    # Approve CCY → currency.
    resp = client.post("/presentations/concepts/review/api/approve", json={
        "schema": "ODS_X", "table": "T",
        "bindings": [{"column": "CCY", "concept": "currency",
                      "transform": {"kind": "identity"}}],
    })
    assert resp.status_code == 200
    assert resp.get_json()["written"] == 1

    # YAML written under the catalog root.
    yaml_path = Path(app.config["CONCEPT_CATALOG_ROOT"]) / "tables" / "ODS_X" / "T.yaml"
    assert yaml_path.exists()

    # Catalog reloaded → compiler now sees CCY as human_verified.
    cat = app.config["CONCEPT_BINDING_CATALOG"]
    assert cat.get_binding("ODS_X", "T", "currency") is not None

    # Queue no longer offers CCY (it's bound now).
    body = client.get("/presentations/concepts/review/api/queue?schema=ODS_X&table=T").get_json()
    assert "CCY" not in {row["column"] for row in body["queue"]}


def test_reject_then_queue_drops_proposal(client):
    client.post("/presentations/concepts/review/api/reject", json={
        "schema": "ODS_X", "table": "T",
        "items": [{"column": "CCY", "concept": "currency"}],
    })
    body = client.get("/presentations/concepts/review/api/queue?schema=ODS_X&table=T").get_json()
    assert "CCY" not in {row["column"] for row in body["queue"]}


def test_approve_validation_error(client):
    resp = client.post("/presentations/concepts/review/api/approve", json={
        "schema": "ODS_X", "table": "T",
        "bindings": [{"column": "CCY", "concept": "currency",
                      "transform": {"kind": "telepathy"}}],
    })
    assert resp.status_code == 400


def test_missing_params(client):
    assert client.post("/presentations/concepts/inference/run", json={}).status_code == 400
    assert client.get("/presentations/concepts/review/api/queue").status_code == 400

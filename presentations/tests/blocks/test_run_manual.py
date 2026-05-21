"""Tests for the in-presentation /<pid>/block/<bid>/run-manual endpoint.

This is the path the new in-Properties ManualSqlEditor uses. End-to-end:
manifest leaf with manual_sql=true → POST run-manual → variables resolve →
binds expand → DataClient.get_data is called with the expanded SQL +
positional bind params → apply_data_to_config writes the result into
block.config so the renderer picks it up.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.session import SessionRegistry


class _FakeUser(UserMixin):
    name = "kubilay"
    sicil = "A16438"
    department = "Treasury"

    def get_id(self):
        return self.sicil


class _DataClientStub:
    """Stub DataClient. Captures the last (sql, params) call and returns a
    deterministic 3-row bar_chart-friendly DataFrame."""

    def __init__(self):
        self.last_call = None

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
        self.last_call = {"dataset": dataset, "query": query, "query_params": query_params}
        return pd.DataFrame(
            [
                {"SEGMENT": "RETAIL",    "TOTAL": 438_632_544.06},
                {"SEGMENT": "CORPORATE", "TOTAL": 195_305_286.46},
                {"SEGMENT": "SME",       "TOTAL":  50_565_661.44},
            ]
        )

    # Required by SessionRegistry's S3-aware code paths in DEV stubs.
    def read_bytes(self, key):
        return b""
    def _upload_bytes(self, *a, **kw):
        return None
    def read_json(self, key):
        raise FileNotFoundError(key)
    def list_prefix(self, prefix):
        return []


@pytest.fixture
def app(tmp_path: Path):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[2] / "templates"))
    dc = _DataClientStub()
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        LOGIN_DISABLED=True,
        DATA_CLIENT=dc,
        SESSION_REGISTRY=SessionRegistry(
            dc=dc, duck_base_dir=tmp_path / "duck", idle_timeout=300,
        ),
        CATALOG_PATH=str(Path(__file__).resolve().parents[3] / "examples" / "sample_catalog.json"),
    )
    lm = LoginManager(app)
    lm.user_loader(lambda _id: _FakeUser())

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


def _seed_manual_block(client) -> str:
    """Create a presentation with one section + an empty data-bound block.
    Returns the presentation id."""
    r = client.post("/presentations/", data="{}", content_type="application/json")
    pid = r.get_json()["id"]
    patches = [{
        "op": "add", "path": "/blocks/-",
        "value": {
            "id": "sec_t", "type": "section_header", "title": "T",
            "children": [{
                "id": "b_test", "type": "bar_chart", "title": "Manuel Test",
                "locked": False,
                "query": "", "variables": [],
                "config": {"categories": [], "series": [{"name": "S1", "values": []}]},
                "data_source": {"original_sql": ""},
            }],
        },
    }]
    r = client.post(
        f"/presentations/{pid}/patch",
        data=json.dumps({"patches": patches}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.data
    return pid


# ── Tests ─────────────────────────────────────────────────────────────────

class TestRunManual:
    def test_happy_path_returns_block_with_data(self, client):
        pid = _seed_manual_block(client)
        body = {
            "query": (
                "SELECT SEGMENT, SUM(BALANCE_TRY) AS TOTAL FROM EDW.DEPOSITS_DAILY "
                "WHERE DATE BETWEEN :as_of_from AND :as_of_to "
                "AND PRODUCT_CODE IN (:products) "
                "GROUP BY SEGMENT ORDER BY TOTAL DESC"
            ),
            "variables": [
                {"name": "as_of_from", "type": "date", "semantic_tag": "as_of_time",
                 "required": True, "default": "today - 30d"},
                {"name": "as_of_to", "type": "date", "semantic_tag": "as_of_time",
                 "required": True, "default": "today"},
                {"name": "products", "type": "enum_multi", "semantic_tag": "product_group",
                 "required": True, "allowed_values": ["TR", "VD", "FX", "DD"],
                 "default": ["TR", "VD"]},
            ],
        }
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 200, r.data
        rj = r.get_json()
        assert rj["ok"] is True
        block = rj["block"]

        # Data plumbed into block.config so the renderer picks it up unchanged.
        assert block["config"]["categories"] == ["RETAIL", "CORPORATE", "SME"]
        assert block["config"]["series"][0]["values"][0] == pytest.approx(438_632_544.06)
        assert block["query"] == body["query"]
        assert len(block["variables"]) == 3
        assert block["data_source"]["engine"] == "manual_sql"
        # Legacy manual_sql flag, if it ever was on the block, is dropped post-run.
        assert "manual_sql" not in block
        # data_stale is cleared on a successful run.
        assert block.get("data_stale") is None

        # DataClient saw the expanded enum_multi binds + the original :as_of_from.
        last = client.application.config["DATA_CLIENT"].last_call
        assert ":products_0" in last["query"]
        assert ":products_1" in last["query"]
        assert last["query_params"]["products_0"] == "TR"
        assert last["query_params"]["products_1"] == "VD"
        assert "as_of_from" in last["query_params"]

    def test_invalid_sql_rejected(self, client):
        pid = _seed_manual_block(client)
        body = {"query": "DROP TABLE foo", "variables": []}
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 400
        rj = r.get_json()
        assert rj["kind"] == "sql"
        assert "DROP" in rj["error"]

    def test_undeclared_bind_rejected(self, client):
        pid = _seed_manual_block(client)
        body = {
            "query": "SELECT 1 FROM dual WHERE x = :nope",
            "variables": [],
        }
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 400
        rj = r.get_json()
        assert rj["kind"] == "sql"
        assert "nope" in rj["error"]

    def test_empty_query_rejected(self, client):
        pid = _seed_manual_block(client)
        body = {"query": "  ", "variables": []}
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 400
        assert r.get_json()["kind"] == "no_sql"

    def test_invalid_semantic_tag_rejected(self, client):
        pid = _seed_manual_block(client)
        body = {
            "query": "SELECT 1 FROM dual WHERE x = :a_var",
            "variables": [
                {"name": "a_var", "type": "date",
                 "semantic_tag": "not_a_tag",  # not in allow-list
                 "required": True, "default": "today"},
            ],
        }
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 400
        # Pydantic surfaces the schema failure as "variable_schema" kind.
        rj = r.get_json()
        assert rj["kind"] in ("variable_schema", "block_schema")

    def test_missing_block_404(self, client):
        pid = _seed_manual_block(client)
        body = {"query": "SELECT 1 FROM dual", "variables": []}
        r = client.post(
            f"/presentations/{pid}/block/nope_id/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 404

    def test_variable_override(self, client):
        pid = _seed_manual_block(client)
        body = {
            "query": (
                "SELECT 1 FROM dual WHERE x IN (:products)"
            ),
            "variables": [
                {"name": "products", "type": "enum_multi",
                 "semantic_tag": "product_group", "required": True,
                 "allowed_values": ["TR", "VD", "FX", "DD"],
                 "default": ["TR", "VD"]},
            ],
            "variable_overrides": {"products": ["FX", "DD"]},
        }
        r = client.post(
            f"/presentations/{pid}/block/b_test/run-manual",
            data=json.dumps(body), content_type="application/json",
        )
        assert r.status_code == 200, r.data
        last = client.application.config["DATA_CLIENT"].last_call
        assert last["query_params"]["products_0"] == "FX"
        assert last["query_params"]["products_1"] == "DD"

"""End-to-end Phase 6.5.a integration test.

Exercises the full path: load fixture → save through the store → reload →
resolve variables → expand binds → execute against a stubbed DataClient →
verify the result shape.

Routes-level test uses the Flask test client with a tiny app factory so we
don't need to bootstrap the full production ``app.py`` (which pulls Oracle).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

import yaml

from presentations import presentations_bp
from presentations.blocks.schema import load_block_from_dict
from presentations.blocks.store import LocalBlockStore


# ── Stubs ─────────────────────────────────────────────────────────────────

class _FakeUser(UserMixin):
    name = "kubilay"
    sicil = "A16438"
    # Department slugifies to "retail_banking" — the sample block's team — so
    # the block-write auth gate (_block_write_denied) admits the legitimate
    # same-team save. See test_e2e_cross_team_save_denied for the deny path.
    department = "Retail Banking"

    def get_id(self):  # noqa: D401
        return self.sicil


class _RecordingDataClient:
    """Captures the last get_data call and returns a deterministic DataFrame."""

    def __init__(self):
        self.last_call: dict | None = None

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        self.last_call = {
            "dataset": dataset,
            "query": query,
            "query_params": query_params,
        }
        # Mirror the bind list to produce a one-row DataFrame.
        return pd.DataFrame(
            [{"BRANCH_NAME": "Levent", "TOTAL_POS": 1234.5}],
        )


# ── App factory ───────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path: Path):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[2] / "templates"))
    app.config.update(
        SECRET_KEY="test",
        TESTING=True,
        LOGIN_DISABLED=True,
        BLOCK_STORE=LocalBlockStore(tmp_path / "blocks"),
        DATA_CLIENT=_RecordingDataClient(),
    )

    lm = LoginManager(app)

    @lm.user_loader
    def _load(_id):  # noqa: D401
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


# ── Tests ─────────────────────────────────────────────────────────────────

def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def _post_json(client, url, body):
    return client.post(
        url,
        data=json.dumps(body, default=_json_default),
        content_type="application/json",
    )


def test_e2e_save_and_run(client, sample_block_dict):
    # Save the sample block.
    resp = _post_json(client, "/presentations/blocks/api/save", sample_block_dict)
    assert resp.status_code == 200, resp.data
    saved = resp.get_json()
    assert saved["ok"] is True
    assert saved["team"] == "retail_banking"
    assert saved["id"] == "branch_position_kpi"
    assert saved["version"] == 1

    # Run the saved block.
    run_url = f"/presentations/blocks/{saved['team']}/{saved['id']}/{saved['version']}/run"
    resp = _post_json(client, run_url, {})
    assert resp.status_code == 200, resp.data
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["columns"] == ["BRANCH_NAME", "TOTAL_POS"]
    assert payload["rows"][0]["BRANCH_NAME"] == "Levent"

    # Headers carry observability info.
    assert resp.headers["X-Row-Count"] == "1"
    assert "X-Query-Duration-Ms" in resp.headers

    # The bind dict reached the DataClient via query_params, with positional
    # enum_multi expansion.
    last = client.application.config["DATA_CLIENT"].last_call
    assert "currency_list_0" in last["query_params"]
    assert ":currency_list_0" in last["query"]


def test_e2e_invalid_sql_rejected(client, sample_block_dict):
    bad = dict(sample_block_dict)
    bad["block"] = dict(sample_block_dict["block"])
    bad["block"]["query"] = "DROP TABLE foo"
    resp = _post_json(client, "/presentations/blocks/api/save", bad)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert any("DROP" in e for e in body["errors"])


def test_e2e_undeclared_bind_rejected(client, sample_block_dict):
    bad = dict(sample_block_dict)
    bad["block"] = dict(sample_block_dict["block"])
    bad["block"]["query"] = "SELECT 1 FROM dual WHERE x = :nope"
    resp = _post_json(client, "/presentations/blocks/api/save", bad)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert any("nope" in e for e in body["errors"])


def test_e2e_missing_semantic_tag_rejected(client, sample_block_dict):
    bad = dict(sample_block_dict)
    bad["block"] = dict(sample_block_dict["block"])
    bad["block"]["variables"] = list(sample_block_dict["block"]["variables"])
    # Drop the semantic_tag on the first variable.
    bad["block"]["variables"][0] = {
        k: v for k, v in bad["block"]["variables"][0].items()
        if k != "semantic_tag"
    }
    resp = _post_json(client, "/presentations/blocks/api/save", bad)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False


def test_e2e_version_bump(client, sample_block_dict):
    _post_json(client, "/presentations/blocks/api/save", sample_block_dict)
    resp = _post_json(
        client, "/presentations/blocks/api/save_new_version", sample_block_dict,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["version"] == 2


def test_e2e_cross_team_save_denied(client, sample_block_dict):
    """The auth gate rejects writing a block under another team's namespace."""
    other = dict(sample_block_dict)
    other["block"] = dict(sample_block_dict["block"])
    other["block"]["team"] = "some_other_team"

    resp = _post_json(client, "/presentations/blocks/api/save", other)
    assert resp.status_code == 403, resp.data
    body = resp.get_json()
    assert body["ok"] is False
    assert body["phase"] == "auth"

    # save_new_version is gated the same way.
    resp = _post_json(client, "/presentations/blocks/api/save_new_version", other)
    assert resp.status_code == 403, resp.data

    # Nothing was persisted under the foreign team.
    store = client.application.config["BLOCK_STORE"]
    assert store.list_blocks(team="some_other_team", include_deprecated=True) == []


def test_e2e_owner_stamped_server_side(client, sample_block_dict):
    """owner is always the authenticated caller, never the payload value."""
    spoof = dict(sample_block_dict)
    spoof["block"] = dict(sample_block_dict["block"])
    spoof["block"]["owner"] = "Z99999"  # attacker-supplied owner

    resp = _post_json(client, "/presentations/blocks/api/save", spoof)
    assert resp.status_code == 200, resp.data
    saved = resp.get_json()
    store = client.application.config["BLOCK_STORE"]
    block = store.load(saved["team"], saved["id"], saved["version"])
    assert block.owner == "A16438"  # current_user.sicil, not the spoofed value


def test_e2e_preview_runs_without_persistence(client, sample_block_dict):
    """The /api/preview endpoint runs a payload without writing to the store."""
    resp = _post_json(client, "/presentations/blocks/api/preview", {
        "block": sample_block_dict["block"],
    })
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["ok"] is True
    assert body["rows"][0]["BRANCH_NAME"] == "Levent"
    # Store remains empty.
    store = client.application.config["BLOCK_STORE"]
    assert store.list_blocks() == []


def test_e2e_semantic_tags_endpoint(client):
    resp = client.get("/presentations/blocks/api/semantic_tags")
    assert resp.status_code == 200
    body = resp.get_json()
    tags = {t["tag"] for t in body["tags"]}
    assert "currency" in tags
    assert "other" in tags


def test_e2e_overrides_change_resolved_values(client, sample_block_dict):
    resp = _post_json(client, "/presentations/blocks/api/save", sample_block_dict)
    saved = resp.get_json()

    run_url = f"/presentations/blocks/{saved['team']}/{saved['id']}/{saved['version']}/run"
    resp = _post_json(client, run_url, {
        "variable_overrides": {"currency_list": ["GBP", "CHF"]},
    })
    assert resp.status_code == 200
    last = client.application.config["DATA_CLIENT"].last_call
    assert last["query_params"]["currency_list_0"] == "GBP"
    assert last["query_params"]["currency_list_1"] == "CHF"

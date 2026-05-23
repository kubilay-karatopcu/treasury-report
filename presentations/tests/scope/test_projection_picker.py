"""Phase 8.c tests: projection picker endpoint + suggested-edge concept field.

Acceptance §10.c:
  - User can select/deselect columns per basket table ✓
  - Removing a column referenced by a join is rejected with explanation ✓
  - Join suggestion engine proposes joins for tables with declared lookups ✓
  - User can confirm or decline joins (decline = just don't apply) ✓

The `routes_scope.scope_projection_update` endpoint is exercised through a
Flask test client so we cover the actual HTTP shape: schema validation, the
catalog-existence check, the blocked-by-join guard, and the projection
routing refresh (an over-threshold projection bumps the alias to lazy).
"""
from __future__ import annotations

import json
from typing import Any

import pytest


# ── Helper: build a Flask app with the routes blueprint + auth bypass ──────

@pytest.fixture
def app():
    import flask
    from flask_login import LoginManager, UserMixin

    from presentations import presentations_bp
    from presentations.scope.catalog import (
        ColumnMeta, DictCatalog, TableMeta,
    )

    class _User(UserMixin):
        id = "A16438"
        sicil = "A16438"

    # Minimal app with login bypass so @login_required passes.
    app = flask.Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        LOGIN_DISABLED=True,
        # Inline AppCatalog-shaped object — the routes use _catalog() which
        # builds AppCatalog(TABLE_DOC_STORE, CONCEPT_REGISTRY, BINDING_CATALOG).
        # We provide a DictCatalog-shaped stand-in via monkeypatching below.
    )

    lm = LoginManager()
    lm.init_app(app)
    lm.anonymous_user = _User
    @lm.user_loader
    def _load(uid):
        return _User()

    @app.before_request
    def _force_login():
        from flask_login import login_user
        login_user(_User())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def dict_catalog():
    return DictCatalog(
        tables={
            "DEPOSITS_DAILY": TableMeta(
                schema_name="EDW", name="DEPOSITS_DAILY",
                estimated_daily_rows=12_400_000,
                partition_column="DATE",
                columns={
                    "DATE": ColumnMeta(type="DATE", concept="as_of_time"),
                    "BRANCH_CODE": ColumnMeta(type="VARCHAR2(10)", concept="branch"),
                    "SEGMENT": ColumnMeta(type="VARCHAR2(20)", concept="segment"),
                    "BALANCE_TRY": ColumnMeta(type="NUMBER"),
                },
            ),
            "BRANCH_DIM": TableMeta(
                schema_name="EDW", name="BRANCH_DIM",
                estimated_total_rows=2000,
                columns={
                    "BRANCH_CODE": ColumnMeta(type="VARCHAR2(10)", concept="branch"),
                    "BRANCH_NAME": ColumnMeta(type="VARCHAR2(100)"),
                },
            ),
        },
        concepts={},
    )


# ── Direct unit tests on the suggested-edges + projection mutator ──────────

def _scope_dict(basket_columns: list[str] | None = None) -> dict:
    return {
        "presentation_id": "p_test",
        "version": 1,
        "created_by": "A16438",
        "created_at": "2026-05-24T00:00:00Z",
        "basket": [{
            "alias": "deposits_daily",
            "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
            "projection": {
                "columns": basket_columns or ["DATE", "BRANCH_CODE", "SEGMENT", "BALANCE_TRY"],
                "include_all": False,
            },
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1000},
        }],
        "filters": {"pinned": [], "interactive": [], "raw": []},
        "joins": [],
    }


from presentations.scope.catalog import ColumnMeta, DictCatalog, TableMeta  # noqa: E402
from presentations.scope.schema import load_scope_from_dict  # noqa: E402


class TestSuggestedEdgeConcept:
    """The _suggested_edges helper now emits a `concept` field that the
    React Flow edge label uses to surface *why* a join is being suggested."""

    def test_shared_concept_edge_carries_concept_name(self):
        from presentations.routes_scope import _suggested_edges

        # Two basket items both binding the 'branch' concept on BRANCH_CODE.
        scope = load_scope_from_dict({"scope": {
            "presentation_id": "p_test", "version": 1,
            "created_by": "A16438", "created_at": "2026-05-24T00:00:00Z",
            "basket": [
                {"alias": "deposits_daily",
                 "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
                 "projection": {"columns": ["BRANCH_CODE"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100}},
                {"alias": "branch_dim",
                 "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
                 "projection": {"columns": ["BRANCH_CODE"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100}},
            ],
            "filters": {"pinned": [], "interactive": [], "raw": []},
            "joins": [],
        }})
        cols_by_alias = {
            "deposits_daily": [{"name": "BRANCH_CODE", "concept": "branch", "join_key": True}],
            "branch_dim": [{"name": "BRANCH_CODE", "concept": "branch", "join_key": True}],
        }
        edges = _suggested_edges(scope, cols_by_alias)
        # One shared-concept edge expected.
        assert any(
            e["source"].startswith("shared_concept:") and e["concept"] == "branch"
            for e in edges
        )

    def test_lookup_edge_carries_source_column_concept(self):
        from presentations.routes_scope import _suggested_edges

        scope = load_scope_from_dict({"scope": {
            "presentation_id": "p_test", "version": 1,
            "created_by": "A16438", "created_at": "2026-05-24T00:00:00Z",
            "basket": [
                {"alias": "deposits_daily",
                 "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
                 "projection": {"columns": ["BRANCH_CODE"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100}},
                {"alias": "branch_dim",
                 "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
                 "projection": {"columns": ["BRANCH_CODE"], "include_all": False},
                 "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100}},
            ],
            "filters": {"pinned": [], "interactive": [], "raw": []},
            "joins": [],
        }})
        cols_by_alias = {
            "deposits_daily": [{
                "name": "BRANCH_CODE",
                "concept": "branch",
                "join_key": True,
                "lookup": {"table": "BRANCH_DIM", "key": "BRANCH_CODE", "display": "BRANCH_NAME"},
            }],
            "branch_dim": [{"name": "BRANCH_CODE", "concept": "branch", "join_key": True}],
        }
        edges = _suggested_edges(scope, cols_by_alias)
        lookup_edges = [e for e in edges if e["source"] == "catalog_lookup"]
        assert len(lookup_edges) == 1
        assert lookup_edges[0]["concept"] == "branch"
        assert lookup_edges[0]["kind"] == "lookup"


# ── Endpoint tests via Flask test client ───────────────────────────────────

def _request(client, path, payload):
    r = client.post(path, data=json.dumps(payload), content_type="application/json")
    return r.status_code, r.get_json()


class TestProjectionEndpoint:
    """The /scope/projection-update endpoint is reached via the Flask test
    client. Auth is bypassed by the conftest's LOGIN_DISABLED setup."""

    @pytest.fixture(autouse=True)
    def _patch_catalog(self, app, dict_catalog, monkeypatch):
        """Wire the routes' _catalog() / _columns_for() / _catalog_json()
        helpers to a DictCatalog so the test doesn't need a real
        TABLE_DOC_STORE. ``monkeypatch`` restores the originals after each
        test so we don't leak into the wider suite."""
        import presentations.routes_scope as rs

        monkeypatch.setattr(rs, "_catalog", lambda: dict_catalog)
        monkeypatch.setattr(rs, "_catalog_json", lambda: {
            "domains": [{
                "id": "dom_mevduat", "label": "Mevduat",
                "tables": [{
                    "id": "EDW.DEPOSITS_DAILY",
                    "columns": [
                        {"name": "DATE", "type": "DATE", "concept": "as_of_time"},
                        {"name": "BRANCH_CODE", "type": "VARCHAR2(10)", "concept": "branch", "key": True},
                        {"name": "SEGMENT", "type": "VARCHAR2(20)", "concept": "segment"},
                        {"name": "BALANCE_TRY", "type": "NUMBER"},
                    ],
                }],
            }],
        })
        monkeypatch.setattr(rs, "_columns_for", lambda schema, name: ([
            {"name": "DATE", "concept": "as_of_time"},
            {"name": "BRANCH_CODE", "concept": "branch"},
            {"name": "SEGMENT", "concept": "segment"},
            {"name": "BALANCE_TRY", "concept": None},
        ] if name == "DEPOSITS_DAILY" else []))

    def test_drop_a_column_succeeds(self, app, dict_catalog):
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "deposits_daily",
                "columns": ["DATE", "BRANCH_CODE", "BALANCE_TRY"],
                "include_all": False,
            })
        assert status == 200
        assert body["ok"] is True
        new_cols = next(b for b in body["scope"]["basket"] if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert "SEGMENT" not in new_cols
        assert set(new_cols) == {"DATE", "BRANCH_CODE", "BALANCE_TRY"}

    def test_unknown_column_rejected(self, app, dict_catalog):
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "deposits_daily",
                "columns": ["FAKE_COLUMN"],
                "include_all": False,
            })
        assert status == 400
        assert "FAKE_COLUMN" in body["error"]

    def test_empty_projection_rejected(self, app, dict_catalog):
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "deposits_daily",
                "columns": [],
                "include_all": False,
            })
        assert status == 400
        assert "en az 1 kolon" in body["error"]

    def test_blocked_by_join(self, app, dict_catalog):
        """Dropping a column that's referenced by a confirmed join returns 400
        with a structured `blocked_by_joins` payload."""
        scope = _scope_dict()
        # Add a second basket entry + a join on BRANCH_CODE.
        scope["basket"].append({
            "alias": "branch_dim",
            "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
            "projection": {"columns": ["BRANCH_CODE", "BRANCH_NAME"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100},
        })
        scope["joins"].append({
            "id": "j_existing",
            "left": {"alias": "deposits_daily", "column": "BRANCH_CODE"},
            "right": {"alias": "branch_dim", "column": "BRANCH_CODE"},
            "kind": "lookup",
        })
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": scope,
                "alias": "deposits_daily",
                "columns": ["DATE", "BALANCE_TRY"],   # drops BRANCH_CODE + SEGMENT
                "include_all": False,
            })
        assert status == 400
        assert "join" in body["error"].lower()
        assert body["blocked_by_joins"]
        affected = body["blocked_by_joins"][0]
        assert affected["join_id"] == "j_existing"
        assert affected["column"] == "BRANCH_CODE"

    def test_unknown_alias_rejected(self, app, dict_catalog):
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "ghost",
                "columns": ["X"],
                "include_all": False,
            })
        assert status == 400
        assert "ghost" in body["error"]

    def test_include_all_resets_columns(self, app, dict_catalog):
        with app.test_client() as client:
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "deposits_daily",
                "columns": [],
                "include_all": True,
            })
        assert status == 200
        proj = next(b for b in body["scope"]["basket"] if b["alias"] == "deposits_daily")["projection"]
        assert proj["include_all"] is True
        assert proj["columns"] == []

    def test_routing_refreshes_after_projection(self, app, dict_catalog):
        """A wider projection bumps the bytes/row estimate. The endpoint
        re-runs _refresh_routing so the returned scope carries the new
        decision."""
        with app.test_client() as client:
            # Reduce projection to one tiny column.
            status, body = _request(client, "/presentations/p_test/scope/projection-update", {
                "scope": _scope_dict(),
                "alias": "deposits_daily",
                "columns": ["DATE"],
                "include_all": False,
            })
        assert status == 200
        # 12.4M rows × 1 DATE column (8 bytes) × 365 days = ~36 GB → lazy.
        assert body["scope"]["basket"][0]["routing"]["decision"] == "lazy"

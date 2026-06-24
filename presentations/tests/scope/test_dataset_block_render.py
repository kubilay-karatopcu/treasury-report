"""Faz B — a dataset-bound Sunum block renders from materialised parquet via
apply-filters, with NO Oracle (viewer-read-only)."""
from __future__ import annotations

import json

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from presentations.scope.materialize import write_dataset
from presentations.scope.schema import load_scope_from_dict
from presentations.scope.store import LocalScopeStore
from presentations.session import SessionRegistry


class _FakeUser(UserMixin):
    sicil = "A16438"
    name = "kubilay"
    department = "Treasury"

    def get_id(self):
        return self.sicil


class _FakeDC:
    """S3 surface (manifest + parquet) + an Oracle get_data that MUST NOT be
    called for dataset-bound blocks."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.get_data_calls: list[dict] = []

    def _upload_bytes(self, key, data, content_type=None, *, if_none_match=False):
        self.objects[key] = bytes(data)

    def read_bytes(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]

    def read_json(self, key):
        if key not in self.objects:
            raise FileNotFoundError(key)
        return json.loads(self.objects[key].decode("utf-8"))

    def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_file(self, key):
        self.objects.pop(key, None)

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        self.get_data_calls.append({"query": query})
        return pd.DataFrame()


def _scope():
    return load_scope_from_dict({
        "presentation_id": "p1", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["CCY", "TOTAL"], "include_all": False},
            "routing": {"decision": "cached", "estimated_bytes": 1000},
            "refresh": {"kind": "scheduled", "interval_seconds": 600},
        }],
        "filters": {"pinned": [], "interactive": []},
    })


def _manifest():
    return {
        "id": "p1", "version": 1,
        "scope_ref": {"presentation_id": "p1", "scope_version": 1},
        "filters": [],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "Bölüm", "children": [
                {"id": "b_bar", "type": "bar_chart", "title": "Pozisyon",
                 "dataset_binding": {"alias": "positions", "columns": ["CCY", "TOTAL"]},
                 "config": {"categories": [], "series": [{"name": "Toplam", "values": []}]}},
                {"id": "b_kpi", "type": "kpi", "title": "Toplam",
                 "dataset_binding": {"alias": "positions", "columns": ["TOTAL"]},
                 "config": {"value": 0, "unit": "", "delta": 0, "delta_label": "", "period": ""}},
            ],
        }],
    }


@pytest.fixture
def app(tmp_path):
    dc = _FakeDC()
    scope_store = LocalScopeStore(tmp_path / "scopes")
    scope_store.save(_scope())                       # scope_v1
    # Cron already materialised the dataset → parquet in the fake S3.
    write_dataset(dc, "p1", "positions",
                  pd.DataFrame({"CCY": ["TRY", "USD"], "TOTAL": [100.0, 50.0]}),
                  sql="SELECT CCY, TOTAL FROM ODS_TREASURY.TRD_BRANCH_POSITION")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        DATA_CLIENT=dc, SCOPE_STORE=scope_store,
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
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
    # Seed the manifest into the session (what the editor would have saved).
    with app.app_context():
        app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1").set_manifest(_manifest())
    app.config["_DC"] = dc
    return app


def test_dataset_blocks_render_from_parquet_no_oracle(app):
    client = app.test_client()
    resp = client.post("/presentations/p1/apply-filters", json={"filter_state": {}})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    blocks = {b["id"]: b for b in resp.get_json()["blocks"]}

    # Both charts served from the ONE materialised dataset — status 'dataset'.
    assert blocks["b_bar"]["status"] == "dataset"
    assert blocks["b_bar"]["alias"] == "positions"
    assert blocks["b_kpi"]["status"] == "dataset"

    # Crucially: NO Oracle round-trip — viewer-read-only.
    assert app.config["_DC"].get_data_calls == []

    # The chart config was populated from the parquet rows.
    sess = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1")
    manifest = sess.get_manifest()
    children = manifest["blocks"][0]["children"]
    bar = next(b for b in children if b["id"] == "b_bar")
    assert bar["config"]["categories"] == ["TRY", "USD"]
    assert bar["config"]["series"][0]["values"] == [100.0, 50.0]
    kpi = next(b for b in children if b["id"] == "b_kpi")
    assert kpi["config"]["value"] == 100.0  # first numeric of the projection


def test_unmaterialised_dataset_block_renders_empty_not_oracle(tmp_path):
    # Dataset NOT materialised (cron hasn't run) → block reports empty, no Oracle.
    dc = _FakeDC()
    scope_store = LocalScopeStore(tmp_path / "scopes")
    scope_store.save(_scope())
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        DATA_CLIENT=dc, SCOPE_STORE=scope_store,
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
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
    with app.app_context():
        app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1").set_manifest(_manifest())

    resp = app.test_client().post("/presentations/p1/apply-filters", json={"filter_state": {}})
    assert resp.status_code == 200
    blocks = {b["id"]: b for b in resp.get_json()["blocks"]}
    assert blocks["b_bar"]["status"] == "empty"
    assert dc.get_data_calls == []


class _OracleDC(_FakeDC):
    """Like _FakeDC but get_data returns a fixed source DataFrame — models the
    one permitted on-demand pull of a lazy source feeding a derivation."""

    def __init__(self, source_df):
        super().__init__()
        self._src = source_df

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kwargs):
        self.get_data_calls.append({"query": query})
        return self._src.copy()


def test_duckdb_preview_hydrates_python_node_over_lazy_source(tmp_path):
    """Regression for the reported bug: a *cached* python node sitting on a
    *lazy* main. The Sunum produced-table docs panel calls
    /duckdb/preview/<alias>; with no parquet (build couldn't materialise it,
    because Pass 1 skipped the lazy source) the endpoint 500'd with
    "Table … does not exist". The hydration fallback must now pull the lazy
    source on demand, run the derivation, and register the view."""
    dc = _OracleDC(pd.DataFrame({"BRANCH_CODE": ["A", "B"], "BALANCE_TRY": [1000, 5000]}))
    scope_store = LocalScopeStore(tmp_path / "scopes")
    scope_store.save(load_scope_from_dict({
        "presentation_id": "p_pylazy", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [
            {
                "table_ref": {"schema": "EDW", "name": "DEPOSITS"}, "alias": "deposits",
                "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "lazy", "estimated_bytes": 500_000_001,
                            "threshold_bytes": 500_000_000},
            },
            {
                "derivation": {"kind": "python", "source_alias": "deposits",
                               "python_code": "output_node_df = input_node_df.assign("
                                              "BALANCE_K=input_node_df['BALANCE_TRY'] / 1000)"},
                "alias": "deposits_py",
                "projection": {"columns": [], "include_all": True},
                "routing": {"decision": "cached", "estimated_bytes": 0},
            },
        ],
        "filters": {"pinned": [], "interactive": []},
    }))
    # NOTE: no write_dataset() — the parquet is absent on purpose (this is the
    # broken-build state). The endpoint must still hydrate via the fetch fallback.

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        DATA_CLIENT=dc, SCOPE_STORE=scope_store,
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
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
    with app.app_context():
        sess = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p_pylazy")
        basket = [{"table": "deposits_py", "alias": "deposits_py", "columns": [],
                   "source": "derived"}]
        sess.set_manifest({
            "id": "p_pylazy", "version": 1,
            "scope_ref": {"presentation_id": "p_pylazy", "scope_version": 1},
            "basket": basket, "blocks": [],
        })
        # Prime the signature so the legacy populate_basket path is skipped and the
        # preview exercises the scope hydration fallback (the path under test).
        sess._last_basket_signature = sess.basket_signature(basket)

    resp = app.test_client().get("/presentations/p_pylazy/duckdb/preview/deposits_py")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert "BALANCE_K" in (data.get("columns") or []), data
    assert data.get("row_count") == 2, data


def test_duckdb_preview_hydrates_produced_view(tmp_path):
    """Regression: the Sunum produced-table docs panel calls /duckdb/preview/<alias>.
    Produced views (manuel SQL / join / filter) aren't persisted in the session
    DuckDB file, so the endpoint must hydrate them on demand (from materialised
    parquet or via the scope fetch) — else it 500s with "Table … does not exist"."""
    dc = _FakeDC()
    scope_store = LocalScopeStore(tmp_path / "scopes")
    scope_store.save(load_scope_from_dict({
        "presentation_id": "p3", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{
            "alias": "my_sql", "sql": "SELECT CCY, TOTAL FROM ODS_TREASURY.TRD_BRANCH_POSITION",
            "projection": {"columns": ["CCY", "TOTAL"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "user", "estimated_bytes": 0},
        }],
        "filters": {"pinned": [], "interactive": []},
    }))
    # Cron-materialised the produced dataset to parquet (no Oracle on preview).
    write_dataset(dc, "p3", "my_sql",
                  pd.DataFrame({"CCY": ["TRY", "USD"], "TOTAL": [100.0, 50.0]}),
                  sql="SELECT CCY, TOTAL FROM ODS_TREASURY.TRD_BRANCH_POSITION")

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        DATA_CLIENT=dc, SCOPE_STORE=scope_store,
        SESSION_REGISTRY=SessionRegistry(dc=dc, duck_base_dir=tmp_path / "duck"),
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
    with app.app_context():
        app.config["SESSION_REGISTRY"].get_or_create("A16438", "p3").set_manifest({
            "id": "p3", "version": 1,
            "scope_ref": {"presentation_id": "p3", "scope_version": 1},
            "basket": [{"table": "my_sql", "alias": "my_sql", "columns": [], "source": "sql"}],
            "blocks": [],
        })

    resp = app.test_client().get("/presentations/p3/duckdb/preview/my_sql")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data.get("columns") == ["CCY", "TOTAL"], data
    assert data.get("row_count") == 2, data

"""Phase 7 — apply-filters end-to-end for a CONCEPT-NATIVE block (no variables).

Regression guard for the bug where the apply-filters loop skipped blocks
without a `variables` array (concept-native blocks have none), so `blocks`
came back empty and the chart never changed.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
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


class _RecordingDC:
    def __init__(self):
        self.calls = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
        self.calls.append({"query": query, "params": query_params})
        return pd.DataFrame([{"SEGMENT": "RETAIL", "TOTAL": 100.0},
                             {"SEGMENT": "SME", "TOTAL": 50.0}])


class _StubSession:
    def __init__(self, manifest):
        self._m = manifest
        self._conn = duckdb.connect(":memory:")

    def get_manifest(self):
        return self._m

    def set_manifest(self, m):
        self._m = m

    def get_duck_conn(self):
        return self._conn


class _StubRegistry:
    def __init__(self, session):
        self._s = session

    def get_or_create(self, user, pid):
        return self._s


def _make_app(manifest, dc):
    catalog_dir = Path(presentations.__file__).parent / "catalog"
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="t", TESTING=True, LOGIN_DISABLED=True,
        SESSION_REGISTRY=_StubRegistry(_StubSession(manifest)),
        DATA_CLIENT=dc,
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


def _manifest():
    # Concept-native block: query + FROM (no source_tables, no variables) +
    # NO sentinel — exercises FROM-derivation + sentinel-less injection.
    return {
        "id": "p1", "version": 1,
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "x", "children": [{
                "id": "b_seg", "type": "bar_chart", "title": "Segment",
                "query": "SELECT SEGMENT, SUM(BALANCE_TRY) AS TOTAL FROM EDW.DEPOSITS_DAILY GROUP BY SEGMENT",
                "config": {"categories": [], "series": [{"name": "T", "values": []}]},
            }],
        }],
    }


def _post(client, pid, filter_state):
    return client.post(f"/presentations/{pid}/apply-filters", json={"filter_state": filter_state})


def test_concept_native_block_is_processed():
    dc = _RecordingDC()
    client = _make_app(_manifest(), dc).test_client()
    resp = _post(client, "p1", {"f_segment": ["RETAIL", "SME"]})
    assert resp.status_code == 200
    body = resp.get_json()
    # The block must appear in the response (was [] before the fix).
    assert len(body["blocks"]) == 1
    blk = body["blocks"][0]
    assert blk["id"] == "b_seg"
    assert blk["concept_injected"] is True
    assert any(p["concept"] == "segment" for p in blk["applied_predicates"])


def test_injected_sql_reached_dataclient():
    dc = _RecordingDC()
    client = _make_app(_manifest(), dc).test_client()
    _post(client, "p1", {"f_segment": ["RETAIL", "SME"]})
    assert dc.calls, "DataClient was never called"
    sql = dc.calls[-1]["query"]
    assert "SEGMENT IN" in sql
    assert "GROUP BY SEGMENT" in sql           # injected before GROUP BY
    # map binding leaves RETAIL/SME as-is (they're canonical == table value).
    assert set(dc.calls[-1]["params"].values()) == {"RETAIL", "SME"}


def test_corp_translates_to_corporate():
    dc = _RecordingDC()
    client = _make_app(_manifest(), dc).test_client()
    _post(client, "p1", {"f_segment": ["CORP"]})
    # canonical CORP → table value CORPORATE via the map binding.
    assert "CORPORATE" in dc.calls[-1]["params"].values()

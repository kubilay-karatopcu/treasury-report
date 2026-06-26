"""Phase 7 — apply-filters end-to-end for a CONCEPT-NATIVE block (no variables).

Regression guard for the bug where the apply-filters loop skipped blocks
without a `variables` array (concept-native blocks have none), so `blocks`
came back empty and the chart never changed.
"""
from __future__ import annotations

from contextlib import contextmanager
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

    @contextmanager
    def duck_conn(self):
        yield self._conn


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


def _legacy_manifest():
    # The shape gpt-4o-mini actually produced: SQL on data_source.original_sql,
    # no `query`, no `source_tables`, no `variables`.
    return {
        "id": "p1", "version": 1,
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "x", "children": [{
                "id": "b_seg", "type": "bar_chart", "title": "Segment",
                "data_source": {"original_sql": "SELECT SEGMENT, SUM(BALANCE_TRY)/1e9 AS total FROM EDW.DEPOSITS_DAILY GROUP BY SEGMENT"},
                "config": {"categories": [], "series": [{"name": "T", "values": []}]},
            }],
        }],
    }


def test_legacy_data_source_shape_is_processed():
    """SQL on data_source.original_sql (no `query`) must still get concept
    filtering — the exact case that returned blocks: [] before the fix."""
    dc = _RecordingDC()
    client = _make_app(_legacy_manifest(), dc).test_client()
    resp = _post(client, "p1", {"f_segment": ["RETAIL", "SME"]})
    body = resp.get_json()
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["concept_injected"] is True
    sql = dc.calls[-1]["query"]
    assert "SEGMENT IN" in sql and "GROUP BY SEGMENT" in sql


def _multi_table_manifest():
    # Block joining TWO tables. An unqualified concept predicate flattened into
    # the outer WHERE would be ORA-00918 (or filter the wrong table), so it must
    # NOT be injected — the block renders concept-blind instead (#12).
    return {
        "id": "p1", "version": 1,
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "x", "children": [{
                "id": "b_join", "type": "bar_chart", "title": "Join",
                "query": ("SELECT d.SEGMENT, SUM(d.BALANCE_TRY) AS TOTAL "
                          "FROM EDW.DEPOSITS_DAILY d "
                          "JOIN EDW.BRANCH_DIM b ON d.BRANCH_CODE = b.BRANCH_CODE "
                          "GROUP BY d.SEGMENT"),
                "config": {"categories": [], "series": [{"name": "T", "values": []}]},
            }],
        }],
    }


def test_multi_table_block_is_concept_blind():
    """#12: a 2-table join must not get an unqualified predicate injected; the
    concept filter is surfaced as blind and the original SQL runs intact."""
    dc = _RecordingDC()
    client = _make_app(_multi_table_manifest(), dc).test_client()
    resp = _post(client, "p1", {"f_segment": ["RETAIL", "SME"]})
    assert resp.status_code == 200
    blk = resp.get_json()["blocks"][0]
    assert blk["id"] == "b_join"
    assert blk.get("concept_injected") is False
    assert "segment" in (blk.get("blind_filters") or [])
    # No predicate was injected — Oracle saw the original join query.
    assert "SEGMENT IN" not in dc.calls[-1]["query"]
    assert "JOIN EDW.BRANCH_DIM" in dc.calls[-1]["query"]


def _produced_kpi_manifest():
    # KPI (aggregation) over a Hazırlık-PRODUCED view `deps_py`. The user bound
    # the SEGMENT column to the `segment` concept in Hazırlık (column_concepts),
    # carried into manifest.basket. The block embeds {{concept_filters}}, so
    # apply-filters injects a DuckDB predicate from column_concepts — the
    # aggregation counterpart of the projection-only dataset_binding path.
    # No catalog table-doc, no Oracle (the view lives in the session DuckDB).
    return {
        "id": "p1", "version": 1,
        "basket": [{"table": "deps_py", "alias": "deps_py", "columns": [],
                    "source": "derived", "column_concepts": {"SEGMENT": "segment"}}],
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{
            "id": "sec", "type": "section_header", "title": "x", "children": [{
                "id": "b_kpi", "type": "kpi", "title": "Ortalama",
                "data_source": {"original_sql":
                    "SELECT ROUND(AVG(AMT), 2) AS value FROM deps_py WHERE {{concept_filters}}"},
                "config": {"value": 0, "unit": "", "delta": 0, "delta_label": "", "period": ""},
            }],
        }],
    }


def _register_produced_view(app):
    sess = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1")
    sess._conn.execute(
        'CREATE TABLE deps_py AS SELECT * FROM (VALUES '
        "('RETAIL', 100), ('RETAIL', 200), ('SME', 300)) t(\"SEGMENT\", \"AMT\")"
    )


def _kpi_value(app):
    sess = app.config["SESSION_REGISTRY"].get_or_create("A16438", "p1")
    return sess.get_manifest()["blocks"][0]["children"][0]["config"]["value"]


def test_produced_view_kpi_concept_filtered():
    """An AVG KPI over a produced view becomes interactively filterable by a
    concept the user bound to a produced column — runs in DuckDB, NOT Oracle."""
    dc = _RecordingDC()
    app = _make_app(_produced_kpi_manifest(), dc)
    _register_produced_view(app)
    resp = _post(app.test_client(), "p1", {"f_segment": ["RETAIL"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    blocks = {b["id"]: b for b in resp.get_json()["blocks"]}
    assert blocks["b_kpi"]["status"] == "dataset_sql", blocks["b_kpi"]
    assert dc.calls == []                       # viewer-read-only: no Oracle
    assert _kpi_value(app) == 150.0             # AVG(100, 200) over RETAIL only


def test_produced_view_kpi_no_active_filter_shows_all():
    dc = _RecordingDC()
    app = _make_app(_produced_kpi_manifest(), dc)
    _register_produced_view(app)
    resp = _post(app.test_client(), "p1", {})   # nothing selected → sentinel = 1 = 1
    assert resp.status_code == 200
    assert _kpi_value(app) == 200.0             # AVG(100, 200, 300) over all rows


def test_produced_view_blind_filter_reported():
    """A filter whose concept is bound to NONE of the view's columns is blind:
    the block still renders (all rows) but the response flags the blind concept
    so the UI shows 'filtre uygulanmadı' instead of the filter silently doing
    nothing (the as_of_time-vs-trade_time confusion)."""
    m = _produced_kpi_manifest()
    m["filters"].append({"id": "f_ccy", "semantic_tag": "currency",
                         "type": "enum_multi", "label": "Para",
                         "allowed_values": ["TRY", "USD"]})
    dc = _RecordingDC()
    app = _make_app(m, dc)
    _register_produced_view(app)
    resp = _post(app.test_client(), "p1", {"f_ccy": ["TRY"]})
    kpi = {b["id"]: b for b in resp.get_json()["blocks"]}["b_kpi"]
    assert kpi["status"] == "dataset_sql"
    assert "currency" in (kpi.get("blind_filters") or []), kpi
    assert kpi.get("concept_injected") is False
    assert _kpi_value(app) == 200.0          # blind → not filtered → all rows


def _cte_sentinel_manifest():
    # Block whose FROM is a derived/CTE view name (not SCHEMA.TABLE, not a basket
    # alias) carrying the {{concept_filters}} sentinel + NO variables. The concept
    # compiler can't reach it (no base-table binding) and there is no basket alias
    # to bind via column_concepts → before 3.2 it was silently DROPPED (no vars,
    # not concept-eligible). It must now render + report the blind filter.
    return {
        "id": "p1", "version": 1,
        "filters": [{"id": "f_segment", "semantic_tag": "segment",
                     "type": "enum_multi", "label": "Segment",
                     "allowed_values": ["RETAIL", "SME", "CORP", "PRIVATE"]}],
        "blocks": [{"id": "sec", "type": "section_header", "title": "x", "children": [{
            "id": "b_cte", "type": "bar_chart", "title": "Derived",
            "query": "SELECT SEGMENT, TOTAL FROM block_b_verilen_combo_daily WHERE {{concept_filters}}",
            "config": {"categories": [], "series": [{"name": "T", "values": []}]},
        }]}],
    }


def test_sentinel_over_unbound_derived_view_is_visible_blind():
    """C2a/c: a sentinel block over an unbound derived/CTE view is NOT silently
    dropped and the sentinel does NOT silently become 1=1 — the block renders
    (all rows) and the response flags the blind concept ('filtre uygulanmadı')."""
    dc = _RecordingDC()
    client = _make_app(_cte_sentinel_manifest(), dc).test_client()
    resp = _post(client, "p1", {"f_segment": ["RETAIL"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body["blocks"]) == 1, body            # was [] (dropped) before 3.2
    blk = body["blocks"][0]
    assert blk["id"] == "b_cte"
    assert blk.get("concept_injected") is False
    assert "segment" in (blk.get("blind_filters") or []), blk
    assert blk["status"] in ("refetched", "cache_hit", "subset")
    # Sentinel neutralised — no literal sentinel ever reaches the DataClient.
    assert dc.calls and "{{concept_filters}}" not in dc.calls[-1]["query"]


def _derived_view_with_source_tables_manifest():
    # Blok türetilmiş view `deps_py`'yi okuyor AMA source_tables bir Oracle
    # tablosu gösteriyor (LLM concept-native shape) ve sentinel YOK. Eski kod
    # path 3'e (Oracle) düşüp `SELECT ... FROM deps_py` çalıştırıyor → deps_py
    # Oracle'da yok → ORA-00942. N1 fix: view referansı → DuckDB.
    return {
        "id": "p1", "version": 1,
        "basket": [{"table": "deps_py", "alias": "deps_py", "columns": [],
                    "source": "derived", "column_concepts": {}}],
        "filters": [{"id": "f_segment", "semantic_tag": "segment", "type": "enum_multi",
                     "label": "Segment", "allowed_values": ["RETAIL", "SME"]}],
        "blocks": [{"id": "sec", "type": "section_header", "title": "x", "children": [{
            "id": "b_cum", "type": "kpi", "title": "Toplam",
            "source_tables": [{"schema": "EDW", "table": "DEPOSITS_DAILY"}],
            "data_source": {"original_sql": "SELECT SUM(AMT) AS value FROM deps_py"},
            "config": {"value": 0, "unit": "", "delta": 0, "delta_label": "", "period": ""},
        }]}],
    }


def test_derived_view_block_runs_in_duckdb_not_oracle():
    """N1/A1: SQL'i bir türetilmiş/scope view referans eden blok, sentinel olmasa
    ve source_tables Oracle tablosu gösterse bile DuckDB'de koşar — Oracle'a
    GİTMEZ (eskiden ORA-00942 alıyordu)."""
    dc = _RecordingDC()
    app = _make_app(_derived_view_with_source_tables_manifest(), dc)
    _register_produced_view(app)
    resp = _post(app.test_client(), "p1", {"f_segment": ["RETAIL"]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    blk = {b["id"]: b for b in resp.get_json()["blocks"]}["b_cum"]
    assert blk["status"] == "dataset_sql", blk      # DuckDB, Oracle değil
    assert dc.calls == []                            # Oracle'a HİÇ gidilmedi (ORA-00942 yok)
    assert blk.get("concept_injected") is False      # sentinel yok → filtre blind
    assert "segment" in (blk.get("blind_filters") or [])

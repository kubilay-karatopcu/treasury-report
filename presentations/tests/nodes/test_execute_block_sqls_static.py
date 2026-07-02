"""execute_block_sqls — statik (SQL'siz) blok istisnası regresyonu.

Data-bound tipte ama data_source'u OLMAYAN bir blokta (değeri elle girilmiş
KPI) title gibi veri-dışı bir alt-path'i patch'lemek SQL zorunluluğu hatası
ÜRETMEMELİ. Eski davranış: '`data_source.original_sql` zorunlu' → chat retry
döngüsü → başlık değişimi bile reddediliyordu.
"""
from __future__ import annotations

import duckdb
import pytest
from flask import Flask

from presentations.nodes.execute_block_sqls import execute_block_sqls


class _Sess:
    def __init__(self):
        self._conn = duckdb.connect(":memory:")

    def get_duck_conn(self):
        return self._conn


class _State:
    def __init__(self, manifest, patches):
        self.manifest = manifest
        self.pending_patches = patches
        self.session = _Sess()
        self.validation_errors: list[str] = []


class _DC:
    def get_data(self, **kw):  # pragma: no cover — çağrılmamalı
        raise AssertionError("statik blok patch'i Oracle'a gitmemeli")


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, DATA_CLIENT=_DC())
    return app


def _manifest_with_static_kpi():
    return {
        "version": 1,
        "meta": {"title": "t"},
        "blocks": [{
            "id": "sec_1", "type": "section_header", "title": "S",
            "config": {}, "children": [{
                "id": "kpi_static", "type": "kpi", "title": "Eski Başlık",
                "locked": False,
                "config": {"value": 5.0, "unit": "M", "delta": 0,
                           "delta_label": "", "period": ""},
            }],
        }],
    }


def test_title_patch_on_static_kpi_produces_no_error(app):
    with app.app_context():
        st = _State(_manifest_with_static_kpi(), [
            {"op": "replace", "path": "/blocks/0/children/0/title",
             "value": "Yeni Başlık"},
        ])
        out = execute_block_sqls(st)
        assert out.validation_errors == []
        # Patch listesi bozulmadan kaldı (synthetic config patch'i de yok).
        assert out.pending_patches[0]["value"] == "Yeni Başlık"


def test_config_value_patch_on_static_kpi_no_error(app):
    with app.app_context():
        st = _State(_manifest_with_static_kpi(), [
            {"op": "replace", "path": "/blocks/0/children/0/config/value",
             "value": 42.0},
        ])
        out = execute_block_sqls(st)
        assert out.validation_errors == []


def test_data_source_patch_still_requires_valid_sql(app):
    """data_source'a DOKUNAN alt-path patch'i executor'a girmeye devam
    etmeli — SQL'i boşaltan bir patch yine hata (retry feedback) üretir."""
    manifest = _manifest_with_static_kpi()
    manifest["blocks"][0]["children"][0]["data_source"] = {
        "original_sql": "SELECT 1 AS value"}
    with app.app_context():
        st = _State(manifest, [
            {"op": "replace",
             "path": "/blocks/0/children/0/data_source/original_sql",
             "value": "   "},
        ])
        out = execute_block_sqls(st)
        assert any("original_sql" in e for e in out.validation_errors)

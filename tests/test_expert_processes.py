"""Faz P0 — uzman süreç kayıt defteri (prisma_home/processes.py) testleri."""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home.processes import PROCESS_REGISTRY, resolve_processes


@pytest.fixture
def app():
    app = Flask(__name__)

    @app.route("/mevduat-panel/", endpoint="mevduat_panel.index")
    def _index():  # pragma: no cover - sadece url_for hedefi
        return ""

    app.config["MEVDUAT_PANEL_ENABLED"] = True
    return app


class TestRegistry:
    def test_kayitlar_tam(self):
        # Her süreç tanımı render için gereken alanları taşımalı.
        for pid, meta in PROCESS_REGISTRY.items():
            assert meta.get("label"), pid
            assert meta.get("endpoint"), pid

    def test_yedi_mevduat_sureci(self):
        assert sum(1 for p in PROCESS_REGISTRY if p.startswith("mevduat.")) == 7


class TestResolve:
    def test_cozumleme_url_ve_sira(self, app):
        with app.test_request_context():
            out = resolve_processes(["mevduat.maliyet", "mevduat.bsc"])
        assert [p["id"] for p in out] == ["mevduat.maliyet", "mevduat.bsc"]
        assert out[0]["url"] == "/mevduat-panel/?page=cost-analysis"
        assert out[1]["url"] == "/mevduat-panel/?page=bsc-presentation"
        assert [p["num"] for p in out] == ["01", "02"]

    def test_bilinmeyen_id_dusulur(self, app):
        with app.test_request_context():
            out = resolve_processes(["yok.boyle.surec", "mevduat.vade"])
        assert [p["id"] for p in out] == ["mevduat.vade"]
        assert out[0]["num"] == "01"

    def test_config_bayragi_kapali_gizler(self, app):
        app.config["MEVDUAT_PANEL_ENABLED"] = False
        with app.test_request_context():
            assert resolve_processes(["mevduat.maliyet"]) == []

    def test_endpoint_kayitsiz_gizler(self):
        app = Flask(__name__)
        app.config["MEVDUAT_PANEL_ENABLED"] = True  # bayrak açık ama blueprint yok
        with app.test_request_context():
            assert resolve_processes(["mevduat.maliyet"]) == []

    def test_bos_ve_none(self, app):
        with app.test_request_context():
            assert resolve_processes(None) == []
            assert resolve_processes([]) == []

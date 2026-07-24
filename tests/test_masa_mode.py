"""Masa modu — tek anahtarlı LLM'siz masa görünümü.

Kabul:
- masa_name: 'Uzman' ekli adları 'Masa'ya çevirir, diğerlerine dokunmaz.
- is_blocked_endpoint: Atölye ana + LLM uçları + tüm presentations.* bloklanır;
  masa/tüketici + mevduat_panel + statik dokunulmaz.
- masa_mode_on: app.config['PRISMA_MASA_MODE'] okunur (varsayılan kapalı).
"""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home.masa import is_blocked_endpoint, masa_mode_on, masa_name


class TestMasaName:
    def test_uzman_to_masa(self):
        assert masa_name("Mevduat Uzmanı") == "Mevduat Masası"
        assert masa_name("Likidite Uzmanı") == "Likidite Masası"

    def test_bare_uzman(self):
        assert masa_name("Uzman") == "Masa"

    def test_no_uzman_untouched(self):
        assert masa_name("Hazine Masası") == "Hazine Masası"
        assert masa_name("Fonlama") == "Fonlama"

    def test_empty_safe(self):
        assert masa_name("") == ""
        assert masa_name(None) == ""


class TestBlockedEndpoint:
    @pytest.mark.parametrize("ep", [
        "prisma_home.atolye_home",
        "prisma_home.expert_ask",
        "prisma_home.expert_briefing_json",
        "presentations.list_presentations",
        "presentations.pipeline_kesif",
        "presentations.editor",
        "presentations.atolye_bloklar",
    ])
    def test_blocked(self, ep):
        assert is_blocked_endpoint(ep) is True

    @pytest.mark.parametrize("ep", [
        "prisma_home.landing",
        "prisma_home.expert_detail",
        "prisma_home.expert_list",
        "mevduat_panel.index",
        "static",
        "prisma_home.static",
        None,
        "",
    ])
    def test_allowed(self, ep):
        assert is_blocked_endpoint(ep) is False


class TestMasaModeFlag:
    def test_default_off(self):
        app = Flask(__name__)
        with app.app_context():
            assert masa_mode_on() is False

    def test_on_when_config_set(self):
        app = Flask(__name__)
        app.config["PRISMA_MASA_MODE"] = True
        with app.app_context():
            assert masa_mode_on() is True

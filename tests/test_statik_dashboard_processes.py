"""Faz S2 — legacy statik dashboard'ların süreç/uzman bağlaması.

Kapsam: PROCESS_REGISTRY girdileri URL'e çözülüyor mu, blok descriptor'ları
embed hedefine (render_url + anchor) sahip mi, `dep` uzmanının departman
bakışında yeni süreçler topic'lerde görünüyor mu, blok id'leri kütüphane
genelinde tekil mi.

`prisma_home` testleri repo kökündeki tests/ altında yaşar (mevduat_panel'in
kendi suite'i modül-içidir; bu dosya iki modülü birlikte sınadığı için burada).

Koşum: repo kökünden `python -m pytest tests/test_statik_dashboard_processes.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mevduat_panel import mevduat_panel_bp  # noqa: E402
from prisma_home.expert_views import resolve_view  # noqa: E402
from prisma_home.experts import LocalExpertStore  # noqa: E402
from prisma_home.processes import (  # noqa: E402
    PROCESS_REGISTRY,
    get_process,
    list_processes,
    resolve_processes,
)

#: Faz S1/S2'de eklenen süreçler → beklenen sayfa yolu.
NEW_PROCESSES = {
    "mevduat.oranlar": "/mevduat-panel/oranlar",
    "mevduat.miktarlar": "/mevduat-panel/miktarlar",
    "mevduat.tarihsel": "/mevduat-panel/tarihsel",
    "mevduat.rakip": "/mevduat-panel/rakip",
}
DEPT = "FİNANSAL YAPAY ZEKA UYGULAMALARI"


@pytest.fixture
def app_ctx():
    """mevduat_panel kayıtlı + modül bayrağı açık bir app bağlamı."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", MEVDUAT_PANEL_ENABLED=True)
    app.register_blueprint(mevduat_panel_bp, url_prefix="/mevduat-panel")
    with app.test_request_context():
        yield app


@pytest.fixture
def expert():
    return LocalExpertStore(base_dir=str(REPO / "dev_data/experts")).load("dep")


# ── Süreç kaydı ──────────────────────────────────────────────────────────────

def test_new_processes_resolve_to_pages(app_ctx):
    cards = {c["id"]: c for c in resolve_processes(list(NEW_PROCESSES))}
    assert set(cards) == set(NEW_PROCESSES)
    for pid, path in NEW_PROCESSES.items():
        assert cards[pid]["url"] == path
        assert cards[pid]["label"]
        assert cards[pid]["documented"] is True


def test_new_processes_hidden_when_module_disabled():
    """MEVDUAT_PANEL_ENABLED kapalıysa süreç kartı gizlenir (mevcut sözleşme)."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", MEVDUAT_PANEL_ENABLED=False)
    app.register_blueprint(mevduat_panel_bp, url_prefix="/mevduat-panel")
    with app.test_request_context():
        assert resolve_processes(list(NEW_PROCESSES)) == []


def test_blocks_carry_embed_target(app_ctx):
    """Her blok brifing atıf iframe'i için render_url + anchor taşır."""
    for pid in NEW_PROCESSES:
        p = get_process(pid)
        assert p is not None
        assert p["blocks"], pid
        for b in p["blocks"]:
            assert b["render_url"] == NEW_PROCESSES[pid], (pid, b["id"])
            assert b["custom_render"]["anchor"], (pid, b["id"])
            # PRISMA-native sayfalar SPA'nın ?page= deep-link'ini kullanmaz
            assert b["custom_render"]["page"] is None
            assert b["documented"] is True


def test_block_anchors_exist_in_templates():
    """Anchor id'leri sayfa şablonundaki .mvp-card id'leriyle eşleşmeli.

    Eşleşmezse embed izolasyonu sessizce boş kart gösterir — bu testin amacı
    şablon/registry kaymasını yakalamak.
    """
    tpl_by_path = {
        "/mevduat-panel/oranlar": "rates.html",
        "/mevduat-panel/miktarlar": "amounts.html",
        "/mevduat-panel/tarihsel": "historic.html",
        "/mevduat-panel/rakip": "competitor.html",
    }
    base = REPO / "mevduat_panel/templates/mevduat_panel/prisma"
    for pid, path in NEW_PROCESSES.items():
        html = (base / tpl_by_path[path]).read_text(encoding="utf-8")
        for b in PROCESS_REGISTRY[pid]["blocks"]:
            anchor = b["custom_render"]["anchor"]
            assert f'id="{anchor}"' in html, (pid, anchor)


def test_block_ids_unique_across_registry():
    """Bloklar kütüphanesi tek listedir — id çakışması kartları karıştırır."""
    ids = [b["id"] for m in PROCESS_REGISTRY.values() for b in (m.get("blocks") or [])]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_new_processes_in_library(app_ctx):
    lst = {p["id"]: p for p in list_processes()}
    for pid in NEW_PROCESSES:
        assert pid in lst
        assert lst[pid]["block_count"] == 3
        assert lst[pid]["documented"] is True
        assert lst[pid]["source_kind"] == "custom"


# ── Uzman bağlaması ──────────────────────────────────────────────────────────

def test_expert_binds_new_processes(expert):
    bound = expert.bound_content["processes"]
    for pid in NEW_PROCESSES:
        assert pid in bound


def test_department_view_topics_expose_new_processes(expert, app_ctx):
    r = resolve_view(expert, DEPT)
    assert r["granted"] and not r["legacy"]
    view = r["view"]
    for pid in NEW_PROCESSES:
        assert pid in view["process_ids"], pid
    titles = [t["title"] for t in view["topics"]]
    assert "Rezervasyon & Fiyatlama" in titles
    assert "Sektör & Rakip" in titles
    by_title = {t["title"]: t["process_ids"] for t in view["topics"]}
    assert by_title["Rezervasyon & Fiyatlama"] == [
        "mevduat.oranlar", "mevduat.miktarlar", "mevduat.tarihsel"]
    assert "mevduat.rakip" in by_title["Sektör & Rakip"]


def test_topic_processes_all_resolve(expert, app_ctx):
    """Bakıştaki her süreç id'si kayıtlı olmalı — YAML yazım hatası kapısı."""
    view = resolve_view(expert, DEPT)["view"]
    resolved = {c["id"] for c in resolve_processes(view["process_ids"])}
    missing = set(view["process_ids"]) - resolved
    assert not missing, missing


def test_embed_mode_implemented_for_prisma_pages():
    """Süreç blokları embed URL'i üretir; sayfa tarafında karşılığı olmalı."""
    common = (REPO / "mevduat_panel/static/prisma/common.js").read_text(encoding="utf-8")
    css = (REPO / "mevduat_panel/static/prisma/pages.css").read_text(encoding="utf-8")
    assert "function initEmbed" in common
    assert 'qs.get("embed")' in common
    assert "mvp-embed" in css
    for js in ("rates.js", "amounts.js", "historic.js", "competitor.js"):
        src = (REPO / "mevduat_panel/static/prisma" / js).read_text(encoding="utf-8")
        assert "M.initEmbed(" in src, js

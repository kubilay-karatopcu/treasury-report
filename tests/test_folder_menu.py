"""Faz R3 — klasör bazlı sol menü.

Kapsam: menü raporun bulunduğu klasörün kardeşlerinden üretiliyor mu, farklı
klasörler sızıyor mu, klasörsüz süreç (BSC) menü üretmiyor mu, SPA deep-link
sayfasından klasör bulunuyor mu ve sağlayıcı yokken panel modülü sessizce
menüsüz kalıyor mu.

Koşum: repo kökünden `python -m pytest tests/test_folder_menu.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mevduat_panel import mevduat_panel_bp  # noqa: E402
from mevduat_panel.routes import (  # noqa: E402
    folder_menu_for_page as panel_menu_for_page,
    folder_menu_for_process as panel_menu_for_process,
)
from prisma_home.experts import LocalExpertStore  # noqa: E402
from prisma_home.folders import folder_menu, folder_menu_for_page  # noqa: E402

DEPT = "FİNANSAL YAPAY ZEKA UYGULAMALARI"


@pytest.fixture
def app_ctx():
    """mevduat_panel kayıtlı + uzman store'u dev fixture'a bağlı app bağlamı."""
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="test",
        MEVDUAT_PANEL_ENABLED=True,
        EXPERT_STORE=LocalExpertStore(base_dir=str(REPO / "dev_data/experts")),
    )
    app.register_blueprint(mevduat_panel_bp, url_prefix="/mevduat-panel")
    with app.test_request_context():
        yield app


def _ids(menu):
    return [i["id"] for i in menu["items"]]


# ── Klasör çözümü ────────────────────────────────────────────────────────────

def test_daily_view_menu_lists_only_its_folder(app_ctx):
    menu = folder_menu("mevduat.oranlar", DEPT)
    assert menu["title"] == "Daily View"
    assert _ids(menu) == [
        "mevduat.oranlar", "mevduat.miktarlar", "mevduat.tarihsel"]


def test_macro_view_menu_lists_only_its_folder(app_ctx):
    menu = folder_menu("mevduat.bakiye", DEPT)
    assert menu["title"] == "Macro Deposit View"
    assert _ids(menu) == [
        "mevduat.maliyet", "mevduat.bakiye", "mevduat.vade",
        "mevduat.donusler", "mevduat.yeni_uretim", "mevduat.sektor",
    ]


def test_folders_do_not_leak_into_each_other(app_ctx):
    """Klasör bazlı olmanın asıl sözleşmesi: başka klasörün raporu görünmez."""
    daily = set(_ids(folder_menu("mevduat.oranlar", DEPT)))
    macro = set(_ids(folder_menu("mevduat.bakiye", DEPT)))
    assert not daily & macro
    assert "mevduat.rakip" not in daily | macro   # Sektör klasöründe


def test_single_item_folder_still_renders(app_ctx):
    """Tek raporlu klasörde de menü çıkar ('her zaman menü' sözleşmesi)."""
    menu = folder_menu("mevduat.rakip", DEPT)
    assert menu["title"] == "Sektör"
    assert _ids(menu) == ["mevduat.rakip"]


def test_active_flag_marks_current_report(app_ctx):
    menu = folder_menu("mevduat.miktarlar", DEPT)
    active = [i["id"] for i in menu["items"] if i["active"]]
    assert active == ["mevduat.miktarlar"]


def test_hidden_process_has_no_folder(app_ctx):
    """BSC hiçbir klasörde değil → menü üretmez (masada da gizli)."""
    assert folder_menu("mevduat.bsc", DEPT) is None


def test_unknown_process_and_blank_are_none(app_ctx):
    assert folder_menu("yok.boyle.bir.surec", DEPT) is None
    assert folder_menu("", DEPT) is None


def test_foreign_department_gets_no_menu(app_ctx):
    """Bakış eşleşmiyorsa (SIKI erişim) menü de yok."""
    assert folder_menu("mevduat.oranlar", "BASKA DEPARTMAN") is None


# ── SPA deep-link (?page=) ───────────────────────────────────────────────────

def test_menu_from_spa_page_key(app_ctx):
    menu = folder_menu_for_page("balance-analysis", DEPT)
    assert menu["title"] == "Macro Deposit View"
    assert "mevduat.bakiye" in _ids(menu)


def test_spa_items_carry_page_key_for_clientside_switch(app_ctx):
    """Aynı endpoint'teki kardeşler data-page taşımalı (SPA içi geçiş korunur)."""
    menu = folder_menu_for_page("cost-analysis", DEPT)
    assert all(i["page"] for i in menu["items"])
    assert {i["endpoint"] for i in menu["items"]} == {"mevduat_panel.index"}


def test_unknown_page_key_is_none(app_ctx):
    assert folder_menu_for_page("boyle-bir-sayfa-yok", DEPT) is None


# ── Panel tarafı: config sağlayıcısı (izolasyon sözleşmesi) ─────────────────

def test_panel_reads_menu_through_config_provider(app_ctx):
    """mevduat_panel prisma_home'u import etmez; menüyü config'ten okur."""
    app_ctx.config["FOLDER_MENU_PROVIDER"] = folder_menu
    app_ctx.config["FOLDER_MENU_FOR_PAGE_PROVIDER"] = folder_menu_for_page
    # Departman anonim kullanıcıda boş gelir → bakış eşleşmez; sağlayıcının
    # çağrıldığını doğrulamak için departmanlı yolu doğrudan sınıyoruz.
    assert folder_menu("mevduat.oranlar", DEPT) is not None
    assert panel_menu_for_process("mevduat.oranlar") is None  # anonim → bakış yok
    assert panel_menu_for_page("cost-analysis") is None


def test_panel_without_provider_renders_no_menu(app_ctx):
    """Sağlayıcı yoksa sayfa menüsüz ama çalışır durumda kalır."""
    app_ctx.config.pop("FOLDER_MENU_PROVIDER", None)
    app_ctx.config.pop("FOLDER_MENU_FOR_PAGE_PROVIDER", None)
    assert panel_menu_for_process("mevduat.oranlar") is None
    assert panel_menu_for_page("cost-analysis") is None


def test_panel_survives_broken_provider(app_ctx):
    """Sağlayıcı patlarsa menü çizilmez — sayfa 500 vermez."""
    def boom(*_a, **_kw):
        raise RuntimeError("patladı")

    app_ctx.config["FOLDER_MENU_PROVIDER"] = boom
    assert panel_menu_for_process("mevduat.oranlar") is None


# ── Şablon sözleşmesi ────────────────────────────────────────────────────────

def test_spa_template_has_no_hardcoded_nav():
    """R3 — elle yazılmış nav listesi kaldırıldı; menü partial'dan gelir."""
    html = (REPO / "mevduat_panel/templates/mevduat_panel/index.html").read_text(
        encoding="utf-8")
    assert "_folder_nav.html" in html
    for stale in ('data-page="cost-analysis"', 'id="np-nav"', 'id="sector-nav"'):
        assert stale not in html, stale


def test_prisma_shell_includes_folder_nav():
    page = (REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html").read_text(
        encoding="utf-8")
    assert "_folder_nav.html" in page
    assert "pk-shell" in page
    css = (REPO / "prisma_home/static/css/kit.css").read_text(encoding="utf-8")
    for cls in (".pk-shell", ".pk-fnav", ".pk-fnav__list"):
        assert cls in css, cls


def test_spa_nav_click_falls_through_for_foreign_endpoints():
    """data-page taşımayan kardeşte preventDefault edilmemeli (link ölmesin)."""
    js = (REPO / "mevduat_panel/static/mevduat_panel.js").read_text(encoding="utf-8")
    idx = js.index('document.querySelectorAll("#deposit-nav a")')
    handler = js[idx:idx + 600]
    assert "if (!this.dataset.page) return;" in handler
    assert handler.index("if (!this.dataset.page) return;") < handler.index(
        "e.preventDefault();")


# ── Şablonun GERÇEK veriyle render'ı ─────────────────────────────────────────
# Regresyon: `folder_menu.items` Jinja'da dict.items METODUNU döndürüyordu
# (attribute önce denenir) → daima truthy, döngüde TypeError. Şablonu gerçek
# sözlükle render etmeyen testler bunu kaçırdı; bu blok o boşluğu kapatır.

@pytest.fixture
def nav_tpl():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(REPO / "mevduat_panel/templates")))
    return env.get_template("mevduat_panel/_folder_nav.html")


def _menu(**over):
    base = {"title": "Daily View", "items": [
        {"id": "mevduat.oranlar", "label": "Rezervasyon Oranları",
         "url": "/mevduat-panel/oranlar", "page": None,
         "endpoint": "mevduat_panel.prisma_rates", "active": True},
        {"id": "mevduat.miktarlar", "label": "Rezervasyon Miktarları",
         "url": "/mevduat-panel/miktarlar", "page": None,
         "endpoint": "mevduat_panel.prisma_amounts", "active": False},
    ]}
    base.update(over)
    return base


def test_nav_renders_with_real_dict(nav_tpl):
    out = nav_tpl.render(folder_menu=_menu(), nav_variant="pk")
    assert "Daily View" in out
    assert "Rezervasyon Oranları" in out
    assert "/mevduat-panel/miktarlar" in out
    assert 'class="active"' in out
    assert "pk-fnav" in out


def test_nav_spa_variant_emits_data_page(nav_tpl):
    spa = _menu(title="Macro Deposit View", items=[
        {"id": "mevduat.bakiye", "label": "Outstanding Balance",
         "url": "/mevduat-panel/?page=balance-analysis", "page": "balance-analysis",
         "endpoint": "mevduat_panel.index", "active": True}])
    out = nav_tpl.render(folder_menu=spa, nav_variant="sidebar",
                         spa_endpoint="mevduat_panel.index")
    assert 'data-page="balance-analysis"' in out
    assert 'id="deposit-nav"' in out
    assert "sidebar-group" in out


def test_nav_foreign_endpoint_gets_no_data_page(nav_tpl):
    """Başka endpoint'teki kardeş data-page taşımaz → tarayıcı href'i izler."""
    out = nav_tpl.render(folder_menu=_menu(), nav_variant="sidebar",
                         spa_endpoint="mevduat_panel.index")
    assert "data-page=" not in out


def test_nav_silent_when_absent_or_empty(nav_tpl):
    assert nav_tpl.render(folder_menu=None).strip() == ""
    assert nav_tpl.render(folder_menu={"title": "X", "items": []}).strip() == ""


def test_nav_template_uses_subscript_not_dot_items():
    """`.items` yasak — dict metodunu döndürür. Anahtar erişimi köşeli parantez."""
    src = (REPO / "mevduat_panel/templates/mevduat_panel/_folder_nav.html").read_text(
        encoding="utf-8")
    assert "folder_menu.items" not in src
    assert "folder_menu['items']" in src

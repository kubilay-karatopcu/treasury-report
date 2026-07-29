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

import re
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
    assert "Daily View" in titles
    assert "Sektör" in titles
    by_title = {t["title"]: t["process_ids"] for t in view["topics"]}
    assert by_title["Daily View"] == [
        "mevduat.oranlar", "mevduat.miktarlar", "mevduat.tarihsel"]
    assert by_title["Sektör"] == ["mevduat.rakip"]


def test_macro_deposit_view_topic(expert, app_ctx):
    """Faz R1 — Outstandingler (maliyet/bakiye/vade) + haftalık, yeni üretim, sektör."""
    view = resolve_view(expert, DEPT)["view"]
    by_title = {t["title"]: t["process_ids"] for t in view["topics"]}
    assert by_title["Macro Deposit View"] == [
        "mevduat.maliyet", "mevduat.bakiye", "mevduat.vade",
        "mevduat.donusler", "mevduat.yeni_uretim", "mevduat.sektor",
    ]


def test_bsc_bound_but_hidden_from_masa(expert, app_ctx):
    """BSC bağlı kalır ama hiçbir klasörde yer almaz → masada görünmez."""
    assert "mevduat.bsc" in expert.bound_content["processes"]
    view = resolve_view(expert, DEPT)["view"]
    assert "mevduat.bsc" not in view["process_ids"]


def test_seed_job_matches_dev_expert(expert):
    """Sürüklenme kapısı: seed job (PROD/S3) ile dep.yaml (DEV) aynı ağacı üretmeli.

    İki kaynak ayrışırsa ofiste klasörler/süreçler sessizce farklı çıkar — bu
    testin kırılması "seed job'ı da güncelle" demektir.
    """
    seed = _load_seed_job()
    built = seed.build_expert(
        read_departments=[DEPT], edit_departments=[DEPT], view_departments=[DEPT],
    )
    assert list(seed.PROCESS_IDS) == list(expert.bound_content["processes"])
    assert built["version"] == expert.version

    def tree(views):
        return [(t["title"], list(t["processes"]))
                for v in views for t in v["topics"]]

    assert tree(built["department_views"]) == tree(expert.department_views)


def _load_seed_job():
    """jobs/seed_mevduat_expert.py'yi DataClient/boto3 olmadan yükler.

    Job import anında repo kökündeki DataClient'ı çeker (S3 istemcisi); test
    ortamında bu bağımlılık gereksiz, yalnız saf `build_expert` sınanıyor.
    """
    import importlib.util
    import types

    for name, attrs in (("boto3", {}), ("DataClient", {"DataClient": object})):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod
    spec = importlib.util.spec_from_file_location(
        "_seed_mevduat_expert", REPO / "jobs/seed_mevduat_expert.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_topic_processes_all_registered(expert):
    """Bakıştaki her süreç id'si registry'de olmalı — YAML yazım hatası kapısı.

    Kasıtlı olarak URL çözümü DEĞİL registry üyeliği sınanır: `resolve_processes`
    ayrıca ilgili blueprint'in kayıtlı olmasını ister, bu ise uygulama
    kompozisyonu meselesidir (her modülün kaydı kendi testinde doğrulanır).
    """
    view = resolve_view(expert, DEPT)["view"]
    missing = [p for p in view["process_ids"] if p not in PROCESS_REGISTRY]
    assert not missing, missing


# ── Faz S4: Uygulamalar (deposit_panel + asistan) ────────────────────────────

APP_PROCESSES = {
    "uygulamalar.panel_parametreler": "/deposit-panel/params",
    "uygulamalar.panel_rezervasyon": "/deposit-panel/reservations",
}


@pytest.fixture
def app_ctx_deposit():
    """deposit_panel kayıtlı + bayrak açık app bağlamı."""
    from deposit_panel import deposit_panel_bp, init_app as dp_init

    dp_init(None, None)
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test", DEPOSIT_PANEL_ENABLED=True)
    app.register_blueprint(deposit_panel_bp, url_prefix="/deposit-panel")
    with app.test_request_context():
        yield app


def test_app_processes_resolve(app_ctx_deposit):
    cards = {c["id"]: c for c in resolve_processes(list(APP_PROCESSES))}
    assert set(cards) == set(APP_PROCESSES)
    for pid, path in APP_PROCESSES.items():
        assert cards[pid]["url"] == path
        assert cards[pid]["documented"] is True


def test_app_processes_hidden_when_disabled(app_ctx_deposit):
    app_ctx_deposit.config["DEPOSIT_PANEL_ENABLED"] = False
    assert resolve_processes(list(APP_PROCESSES)) == []


def test_app_processes_have_no_blocks():
    """Uygulamalar araçtır, analiz bileşeni değil — atıf verilecek blok yok.

    Bilinçli modelleme (mevduat.bsc ile aynı); blok eklenirse embed hedefi de
    gerekir, bu test o kararın farkında olunmasını sağlar.
    """
    for pid in list(APP_PROCESSES) + ["uygulamalar.asistan"]:
        assert PROCESS_REGISTRY[pid]["blocks"] == []


def test_assistant_process_registered():
    meta = PROCESS_REGISTRY["uygulamalar.asistan"]
    assert meta["endpoint"] == "deposit.chat"
    # Asistan kendi blueprint'iyle gelir; modül bayrağı yok (endpoint yoksa
    # resolve_processes zaten sessizce düşürür).
    assert meta["config_flag"] is None


def test_deposit_panel_pages_use_prisma_shell():
    """Sayfalar legacy base.html yerine PRISMA kabuğunu extend etmeli.

    S5: `light_content` (Tabler kaçış kapısı) bırakıldı — bileşenler paylaşılan
    kitten gelir; bkz. test_pages_extend_shell_and_avoid_tabler.
    """
    base = REPO / "deposit_panel/templates/deposit_panel"
    for name in ("params.html", "reservations.html"):
        html = (base / name).read_text(encoding="utf-8")
        assert 'extends "home/_base_prisma.html"' in html, name
        assert 'extends "base.html"' not in html, name
        assert "hide_mode_switch = True" in html, name


def test_deposit_panel_assets_are_vendored():
    """'@'li CDN URL'i kalmamalı — kurumsal proxy mangle ediyor (CLAUDE.md).

    jsdelivr ofis ağında engelli (mevduat_panel Faz A0 notu); bu sayfalar
    ApexCharts/AG Grid'i oradan çekiyordu ve headless turda gerçekten
    yüklenemedi. Vendor dosyaları repoda.
    """
    base = REPO / "deposit_panel/templates/deposit_panel"
    for name in ("params.html", "reservations.html"):
        html = (base / name).read_text(encoding="utf-8")
        script_srcs = [line for line in html.splitlines()
                       if ("<script src=" in line or "<link rel=\"stylesheet\"" in line)]
        for line in script_srcs:
            assert "@" not in line, (name, line.strip()[:90])
    vendor = REPO / "deposit_panel/static/vendor"
    for asset in ("apexcharts.min.js", "ag-grid-enterprise.min.noStyle.js",
                  "ag-grid.css", "ag-theme-alpine.css"):
        assert (vendor / asset).is_file(), asset


def test_expert_view_has_uygulamalar_topic(expert, app_ctx_deposit):
    view = resolve_view(expert, DEPT)["view"]
    by_title = {t["title"]: t["process_ids"] for t in view["topics"]}
    assert "Uygulamalar" in by_title
    assert by_title["Uygulamalar"] == [
        "uygulamalar.panel_parametreler",
        "uygulamalar.panel_rezervasyon",
        "uygulamalar.asistan",
    ]


def test_embed_mode_implemented_for_prisma_pages():
    """Süreç blokları embed URL'i üretir; sayfa tarafında karşılığı olmalı."""
    common = (REPO / "mevduat_panel/static/prisma/common.js").read_text(encoding="utf-8")
    css = KIT.read_text(encoding="utf-8")
    assert "function initEmbed" in common
    assert 'qs.get("embed")' in common
    assert "pk-embed" in css
    for js in ("rates.js", "amounts.js", "historic.js", "competitor.js"):
        src = (REPO / "mevduat_panel/static/prisma" / js).read_text(encoding="utf-8")
        assert "M.initEmbed(" in src, js


# ── Faz S5: UI uniformluğu (paylaşılan kit) ─────────────────────────────────

KIT = REPO / "prisma_home/static/css/kit.css"
#: Kabuğu extend eden ve kit'i kullanması beklenen sayfalar.
PRISMA_PAGE_TEMPLATES = [
    "mevduat_panel/templates/mevduat_panel/prisma/_page.html",
    "deposit_panel/templates/deposit_panel/params.html",
    "deposit_panel/templates/deposit_panel/reservations.html",
    "templates/deposit/chat.html",
]


def test_kit_is_loaded_by_shell():
    """Kit kabuğun parçasıdır — her PRISMA sayfası onu otomatik alır.

    Modüllerin tek tek link vermesi gerekmez; bağ kopar da modüller kendi
    stylesheet'ine dönerse uniformluk sessizce bozulur.
    """
    shell = (REPO / "prisma_home/templates/home/_base_prisma.html").read_text(encoding="utf-8")
    assert "css/kit.css" in shell
    assert KIT.is_file()


@pytest.mark.parametrize("tpl", PRISMA_PAGE_TEMPLATES)
def test_pages_extend_shell_and_avoid_tabler(tpl):
    """Sayfalar kabuğu extend eder ve Tabler'a düşmez (tek tasarım sistemi)."""
    html = (REPO / tpl).read_text(encoding="utf-8")
    assert 'extends "home/_base_prisma.html"' in html, tpl
    # Yorum metnindeki "Tabler" kelimesi değil, GERÇEK kullanım aranır:
    # varlık yüklemesi (link/script) ve kabuğun Tabler opt-in'i.
    asset_lines = [ln for ln in html.splitlines()
                   if ("<script src=" in ln or "<link rel=\"stylesheet\"" in ln)]
    assert not [ln for ln in asset_lines if "tabler" in ln.lower()], tpl
    assert "{% set light_content" not in html, tpl


def test_kit_defines_shared_vocabulary():
    """Kitin bileşen sözlüğü eksiksiz — sayfalar buradan besleniyor."""
    css = KIT.read_text(encoding="utf-8")
    for cls in (".pk-page", ".pk-head", ".pk-btn", ".pk-input", ".pk-select",
                ".pk-filterbar", ".pk-chip", ".pk-badge", ".pk-kpi", ".pk-card",
                ".pk-table", ".pk-dl", ".pk-alert", ".pk-toast",
                ".pk-workspace", ".pk-thread", ".pk-msg", ".pk-pick"):
        assert cls in css, cls


def test_kit_defines_no_new_color_tokens():
    """Kit YENİ renk token'ı tanımlamaz — yalnız prisma.css token'larını kullanır.

    Aksi halde tema geçişi (data-theme) bileşenlerde kopar.
    """
    css = KIT.read_text(encoding="utf-8")
    # `--x: value` biçiminde tanım aranır (kullanım `var(--x)` serbesttir)
    defs = re.findall(r"^\s*(--[a-z0-9-]+)\s*:", css, flags=re.M)
    assert defs == [], defs


def test_assistant_uses_kit_not_own_components():
    """Asistan bileşenleri kitten alır; eski Tabler kabuğu geri gelmemeli."""
    for tpl in ("templates/deposit/chat_partial.html", "templates/deposit/info_panel_left.html"):
        html = (REPO / tpl).read_text(encoding="utf-8")
        assert "pk-" in html, tpl
        for legacy in ("card-body", "btn btn-primary", "form-control", "text-muted"):
            assert legacy not in html, (tpl, legacy)
    # sayfaya özgü CSS yalnız artıkları taşımalı (bileşenler kitte)
    css = (REPO / "static/deposit/style_new.css").read_text(encoding="utf-8")
    assert len(css.splitlines()) < 80, "asistan CSS'i tekrar bileşen tanımlamaya başlamış"


def test_deposit_panel_js_has_no_bootstrap_dependency():
    """Kabukta Bootstrap JS yok — toast kit diline taşındı."""
    js = (REPO / "deposit_panel/static/deposit_panel.js").read_text(encoding="utf-8")
    assert "bootstrap." not in js
    assert "pk-toast" in js

"""PRISMA-native sayfalar (Faz S1) — yapısal testler.

Kapsam: route kaydı, PRISMA kabuğu gerekliliği (kabuk yoksa 503, stack trace
değil), endpoints payload'ının şekli, sayfanın kabuğu gerçekten extend ettiği.
Chart/JS davranışı headless Chromium turuyla doğrulanır (bkz. commit notu).

Koşum: repo kökünden `python -m pytest mevduat_panel/tests/test_prisma_pages.py -q`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mevduat_panel import mevduat_panel_bp  # noqa: E402
from mevduat_panel.engine import reservation_data as rd  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PAGES = ["/mvp/oranlar", "/mvp/miktarlar", "/mvp/tarihsel", "/mvp/rakip"]


class _User(UserMixin):
    id = "A16438"
    name = "Test Kullanici"
    sicil = "A16438"
    department = "FİNANSAL YAPAY ZEKA UYGULAMALARI"


def _fake_loader(name, params=None):
    """Oracle'sız sentetik veri — ETL semantiği test_reservation_data'da."""
    if name.startswith("myu"):
        return pd.DataFrame({
            "CUST_ID": [1], "CREATE_DT": ["2026-07-24"], "CREATE_TM": ["093000"],
            "VADE_BASLANGIC": ["32-35"], "CUST_TP": ["G"], "CCY_CODE": ["TRY"],
            "TALEP_REVIZE_NO": [1], "RESERVATION_AMT": [60000], "MARKET_MAX_RT": [0.50],
            "OFFERED_RATE": [0.45], "DEMANDED_RATE": [0.48],
            "COMPETITOR_BANK_RTS": [0.47], "CURRENTAMOUNT": [10],
            "INCOMING_AMT": [5], "PORTFOLIO_AMT": [7],
        })
    if name.startswith("core_comparison"):
        return pd.DataFrame(columns=[
            "CUST_ID", "CREATE_DT", "CREATE_TM", "VADE_BASLANGIC", "CUST_TP",
            "CCY_CODE", "TALEP_REVIZE_NO", "RESERVATION_AMT", "MARKET_MAX_RT",
            "OFFERED_RATE", "DEMANDED_RATE", "COMPETITOR_BANK_RTS",
        ])
    if name.startswith("treasury"):
        return pd.DataFrame({
            "CUST_ID": [9], "RSRVTN_DT": ["2026-07-24"], "CREATE_TM": ["20260724093000"],
            "MTRTY_STRT": ["32-35"], "MTRTY_END": ["45"], "CURRENCY_CD": ["TRY"],
            "PRCNG_CNT": [1], "RQSTD_INTRST_RT": [0.48], "RECMMND_INTRST_RT": [0.44],
            "APPRVD_INTRST_RT": [0.40], "RESERVATION_AMT": [70000],
            "MARKET_MAX_RT": [0.50], "COMPETITOR_BANK_RTS": [0.47],
        })
    if name == "competitor_analysis":
        return pd.DataFrame({
            "TARIH": ["2026-07-24"], "VADE": ["32-45 Gün"], "TUTAR": ["0-500 Bin"],
            "FAIZ": [0.45], "DOVIZ_CINSI": ["TRY"], "KAYNAK": ["web"],
            "URUN": ["MEVDUAT"], "BANKA_ADI": ["A Bank"],
        })
    raise KeyError(name)


def _make_app(with_shell: bool) -> Flask:
    app = Flask(__name__, template_folder=str(REPO / "templates"),
                static_folder=str(REPO / "static"))
    app.config.update(SECRET_KEY="test", MEVDUAT_PANEL_ENABLED=True)
    app.register_blueprint(mevduat_panel_bp, url_prefix="/mvp")
    if with_shell:
        from prisma_home import prisma_home_bp
        app.register_blueprint(prisma_home_bp, url_prefix="")
    lm = LoginManager()
    lm.init_app(app)
    lm.user_loader(lambda uid: _User())

    @app.route("/login-stub")
    def _login_stub():
        login_user(_User())
        return "ok"

    return app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader)
    rd.reset_caches()
    app = _make_app(with_shell=True)
    with app.test_client() as c:
        c.get("/login-stub")
        yield c
    rd.reset_caches()


# ── Route kaydı + kabuk ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", PAGES)
def test_page_renders_with_prisma_shell(client, path):
    r = client.get(path)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # PRISMA kabuğu gerçekten extend edilmiş mi (topbar + tema + token CSS)
    assert 'class="prisma' in body
    assert "prisma-theme" in body            # tema flash-prevention scripti
    assert "css/prisma.css" in body          # PRISMA token stylesheet'i
    assert "pk-page" in body                 # sayfa gövdesi (kit)
    assert "pk-filterbar" in body            # paylaşılan kit filtre barı


@pytest.mark.parametrize("path", PAGES)
def test_page_requires_login(path):
    """Oturumsuz erişim sayfayı sızdırmaz (login_required)."""
    app = _make_app(with_shell=True)
    with app.test_client() as c:
        r = c.get(path)
    assert r.status_code in (302, 401)


@pytest.mark.parametrize("path", PAGES)
def test_page_degrades_without_shell(monkeypatch, path):
    """prisma_home kayıtlı değilse 503 — stack trace değil (izolasyon sözleşmesi)."""
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader)
    rd.reset_caches()
    app = _make_app(with_shell=False)
    with app.test_client() as c:
        c.get("/login-stub")
        r = c.get(path)
    assert r.status_code == 503
    assert "prisma_home" in r.get_data(as_text=True)


# ── Endpoints payload'ı ──────────────────────────────────────────────────────

def test_endpoints_payload_shape(client):
    """JS'e gömülen fetch tabanları doğru prefix'i ve tarih-eki biçimini taşır."""
    body = client.get("/mvp/oranlar").get_data(as_text=True)
    start = body.index('id="mvp-endpoints"')
    frag = body[start:start + 600]
    payload = json.loads(frag[frag.index(">") + 1:frag.index("</script>")])
    assert set(payload) == {"dates", "rates", "amounts", "historic", "competitor"}
    # blueprint prefix'i korunur (SCRIPT_NAME/OpenShift uyumu)
    assert all(v.startswith("/mvp/") for v in payload.values())
    # tarih path parametresi client tarafında ekleneceği için sondaki '/' kalır
    assert payload["rates"].endswith("/")
    assert payload["amounts"].endswith("/")
    # base + tarih gerçekten çalışan bir URL üretir
    assert client.get(payload["rates"] + "2026-07-24").status_code == 200


def test_static_assets_served(client):
    """PRISMA sayfa varlıkları blueprint static'inden servis ediliyor."""
    for asset in ("prisma/common.js", "prisma/rates.js",
                  "prisma/amounts.js", "prisma/historic.js", "prisma/competitor.js"):
        assert client.get("/mvp/static/" + asset).status_code == 200, asset


def test_chip_and_button_rules_survive_prisma_reset():
    """prisma.css:154 `body.prisma button {border:none}` reset'ini aşan seçici.

    Ön ek düşerse chip/butonlar düz metin gibi render olur (headless turda
    yakalandı) — regresyon kapısı. Kurallar artık paylaşılan kitte.
    """
    css = (REPO / "prisma_home/static/css/kit.css").read_text(encoding="utf-8")
    assert "body.prisma .pk-chip {" in css
    assert "body.prisma .pk-btn {" in css


def test_module_defines_no_own_component_css():
    """UI uniformluğu: modül kendi buton/kart/tablo stilini tanımlamaz.

    Bileşenler kabuğun paylaşılan kitinden (prisma_home/static/css/kit.css)
    gelir; modülde ayrı bir bileşen stylesheet'i yeniden belirirse bu test
    kırılır.
    """
    assert not (REPO / "mevduat_panel/static/prisma/pages.css").exists()
    page = (REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html").read_text(encoding="utf-8")
    assert "pages.css" not in page


# ── R11 — gün dönümü + takvim ikonu (bkz. docs/CUSTOM_PAGE_DESIGN.md §2b/§2c) ─

def test_date_auto_refresh_wired_on_single_date_pages():
    """Tek tarihli sayfalar gün dönümünü izler.

    Tarih listesi yalnız açılışta çekilirse ekranda günlerce açık kalan sekme
    dünkü tarihte takılı kalır (yaşandı). `MVP.initDateAutoRefresh` sözleşmesi:
    her sayfa listeyi tazeler VE yeniden yüklerken o günün client cache'ini
    boşaltır — yoksa loadDate eski satırları geri koyar.
    """
    common = (REPO / "mevduat_panel/static/prisma/common.js").read_text(encoding="utf-8")
    assert "function initDateAutoRefresh(" in common
    assert "initDateAutoRefresh: initDateAutoRefresh" in common       # MVP'de dışa açık
    # Açık filtre kutusu popup'tır; sarmalayıcı (.bub-filter-dd) daima görünür —
    # yanlış seçici tazelemeyi sonsuza erteler (yaşandı).
    assert ".bub-filter-dd-popup:not(.hidden)" in common

    for page in ("rates.js", "amounts.js"):
        js = (REPO / "mevduat_panel/static/prisma" / page).read_text(encoding="utf-8")
        assert "MVP.initDateAutoRefresh({" in js, page
        assert "delete cache[iso]" in js, page


def test_dock_date_row_reserves_a_lane_for_the_calendar_icon():
    """Takvim ikonu GG.AA.YYYY overlay'inin (özellikle yılın) üstüne binmez.

    272px'lik dock'ta satır dardır; ölçüler birlikte çalışır — biri geri alınırsa
    ikon yılı yeniden kapatır.
    """
    css = (REPO / "mevduat_panel/static/mevduat_prisma.css").read_text(encoding="utf-8")
    assert "flex: 0 0 58px;" in css                       # anahtar kolonu ("Başlangıç" ≈ 49px)
    assert "::-webkit-calendar-picker-indicator {" in css  # ikon küçültülür
    # Overlay'in sağındaki yasak şerit: yer daralsa bile metin ikonun altına akmaz.
    start = css.index("\n.mv-dock-datetext {")   # is-editing bloğu değil, kuralın kendisi
    block = css[start:css.index("}", start)]
    assert "right: 16px;" in block
    assert "overflow: hidden;" in block


def test_filter_strip_cannot_be_squashed_or_become_a_scroll_container():
    """Filtre şeridi ezilmez ve kendi scroll kabına dönüşmez.

    Yaşandı: `.main` dikey flex kabı içeriğiyle taşarken şerit 18px'e inip
    kendi içinde dikey scrollbar çıkardı — butonlar yarım, açılan bub-filter
    kutuları hiç görünmedi. İki kural birlikte tutar (bkz. CUSTOM_PAGE_DESIGN §3):
    flex-shrink kapalı + overflow-x 'clip' ('hidden' overflow-y'yi auto'ya
    çevirip hem otomatik minimum yüksekliği siler hem kutuları kırpar).
    """
    css = (REPO / "mevduat_panel/static/mevduat_prisma.css").read_text(encoding="utf-8")
    start = css.index(".mevduat-mount .mv-page-filters {")
    block = css[start:css.index("}", start)]
    assert "flex: 0 0 auto;" in block
    assert "overflow-x: clip;" in block
    assert "overflow-x: hidden;" not in block

    # Inline stil overflow/flex yazmaz — stylesheet'i ezip bug'ı geri getirir.
    page = (REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html").read_text(encoding="utf-8")
    tag_start = page.index('<div class="mv-page-filters"')
    inline = page[tag_start:page.index(">", tag_start)]
    assert "overflow" not in inline
    assert "flex:" not in inline.replace("flex-wrap:", "")

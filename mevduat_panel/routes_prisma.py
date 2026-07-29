"""PRISMA-native rezervasyon/rakip sayfaları — Faz S1 HTML route'ları.

Legacy sayfaların (``/oranlar``, ``/miktarlar``, ``/historic``, ``/competitor``)
güncel PRISMA design sistemine yeniden kurulmuş halleri. Şablonlar PRISMA
kabuğunu (``home/_base_prisma.html``) extend eder; filtre/date-picker/chart
konvansiyonları PRISMA'dan gelir (bkz. static/prisma/pages.css + common.js).

Şablon bağımlılığı notu (docs/STATIK_DASHBOARD_ADAPTASYON.md §2 amendment):
bu sayfalar PRISMA kabuğunu Jinja üzerinden extend eder — ``prisma_home``
blueprint'i kayıtlı olmalıdır. Python izolasyonu korunur (bu modül
``prisma_home``'u import ETMEZ); kabuk yoksa route 503 ile zarifçe durur,
modülün geri kalanı (SPA + API'ler) etkilenmez.
"""
from __future__ import annotations

from flask import Response, current_app, render_template, url_for
from flask_login import current_user, login_required

from .routes import MEVDUAT_VERSION, folder_menu_for_process, mevduat_panel_bp

#: PRISMA kabuğunun varlık testi — topbar.html bu endpoint'lere url_for atar.
_SHELL_ENDPOINTS = ("prisma_home.landing", "prisma_home.atolye_home")


def _shell_available() -> bool:
    return all(ep in current_app.view_functions for ep in _SHELL_ENDPOINTS)


def _endpoints() -> dict:
    """JS'e gömülen fetch tabanları — url_for SCRIPT_NAME prefix'ini üretir."""
    return {
        "dates": url_for("mevduat_panel.api_reservation_dates"),
        # Tarih path parametresi client tarafında eklenir → sondaki '/' korunur.
        "rates": url_for("mevduat_panel.api_reservation_rates", date_str="_")[:-1],
        "amounts": url_for("mevduat_panel.api_reservation_amounts", date_str="_")[:-1],
        "historic": url_for("mevduat_panel.api_reservation_historic"),
        "competitor": url_for("mevduat_panel.api_reservation_competitor"),
    }


def _render(template: str, process_id: str | None = None, **ctx) -> Response:
    if not _shell_available():
        return Response(
            "PRISMA kabuğu (prisma_home) kayıtlı değil — bu sayfa kabuğu gerektirir.",
            status=503,
            mimetype="text/plain",
        )
    html = render_template(
        template,
        mevduat_version=MEVDUAT_VERSION,
        endpoints=_endpoints(),
        # R3 — bu sayfanın klasöründeki kardeş raporlar (sol menü).
        folder_menu=folder_menu_for_process(process_id),
        **ctx,
    )
    resp = Response(html, mimetype="text/html")
    # SPA sayfalarıyla aynı disiplin: bayat HTML/JS "değişiklik görünmüyor"
    # sınıfının ana sebebi.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@mevduat_panel_bp.route("/oranlar")
@login_required
def prisma_rates() -> Response:
    return _render(
        "mevduat_panel/prisma/rates.html",
        process_id="mevduat.oranlar",
        eyebrow="Mevduat · Rezervasyon",
        page_heading="Rezervasyon Oranları",
        page_sub="Gün içi teklif, talep ve rakip oranlarının vade kırılımında "
                 "tutar ağırlıklı karşılaştırması.",
    )


@mevduat_panel_bp.route("/miktarlar")
@login_required
def prisma_amounts() -> Response:
    return _render(
        "mevduat_panel/prisma/amounts.html",
        process_id="mevduat.miktarlar",
        eyebrow="Mevduat · Rezervasyon",
        page_heading="Rezervasyon Miktarları",
        page_sub="Rezervasyon, gelen tutar ve portföy büyüklüklerinin vade, "
                 "segment ve kaynak kırılımı.",
    )


@mevduat_panel_bp.route("/tarihsel")
@login_required
def prisma_historic() -> Response:
    return _render(
        "mevduat_panel/prisma/historic.html",
        process_id="mevduat.tarihsel",
        eyebrow="Mevduat · Rezervasyon",
        page_heading="Tarihsel Görünüm",
        page_sub="Son 30 günün günlük hacim ve ağırlıklı oran evrimi; "
                 "teklif–talep–piyasa bandı.",
    )


@mevduat_panel_bp.route("/rakip")
@login_required
def prisma_competitor() -> Response:
    return _render(
        "mevduat_panel/prisma/competitor.html",
        process_id="mevduat.rakip",
        eyebrow="Sektör · Rekabet",
        page_heading="Rakip Faiz Analizi",
        page_sub="Rakip bankaların mevduat faiz kotasyonları: banka, vade "
                 "bandı ve zaman ekseninde piyasa konumu.",
    )

"""Faz R5 — Rezervasyon Oranları sayfasının eski orijinaliyle paritesi.

Kaynak: ``templates/rates.html`` + ``static/js/rates.js`` (PRISMA öncesi üretim).
Hedef: ``mevduat_panel/templates/mevduat_panel/prisma/rates.html`` +
``mevduat_panel/static/prisma/rates.js``.

Bu testler YAPI ve FORMÜL paritesini sabitler: kart/karosel/slayt sayısı, plot
kimlikleri, seri adları ve sayı üreten fonksiyonların varlığı. Kullanıcı kararı
"ekstra kart/blok ekleme" olduğu için fazlalık da hatadır — slayt sayıları
birebir sınanır.

Koşum: repo kökünden `python -m pytest tests/test_rates_parity.py -q`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

NEW_HTML = REPO / "mevduat_panel/templates/mevduat_panel/prisma/rates.html"
NEW_JS = REPO / "mevduat_panel/static/prisma/rates.js"
OLD_JS = REPO / "static/js/rates.js"
KIT = REPO / "prisma_home/static/css/kit.css"
CAROUSEL_JS = REPO / "mevduat_panel/static/prisma/carousel.js"



# ── Render yardımcısı ────────────────────────────────────────────────────────
# Şablonlar markup'ı Jinja makrolarıyla üretir; ham dosya metni yapıyı GÖSTERMEZ
# (folder_nav 500'ü bu boşluktan kaçmıştı). Yapısal assert'ler render ÇIKTISI
# üzerinde koşar. Kabuk (_page.html) stub'lanır — blok içerikleri aynen basılır,
# url_for filename döndürür ki vendor asset adları çıktıda görünsün.

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

_PAGE_STUB = ("{% block title %}{% endblock %}{% block mvp_controls %}{% endblock %}"
              "{% block mvp_body %}{% endblock %}{% block page_scripts %}{% endblock %}")


def _render_page(name: str) -> str:
    env = Environment(loader=ChoiceLoader([
        DictLoader({"mevduat_panel/prisma/_page.html": _PAGE_STUB}),
        FileSystemLoader(str(REPO / "mevduat_panel/templates")),
    ]))
    env.globals["url_for"] = lambda *a, **k: k.get("filename", "#")
    return env.get_template(f"mevduat_panel/prisma/{name}").render(mevduat_version=1)

def _html() -> str:
    return _render_page("rates.html")


def _js() -> str:
    return NEW_JS.read_text(encoding="utf-8")


# ── Yapı paritesi ────────────────────────────────────────────────────────────

#: Eski sayfadaki 4 kart ve slayt sayıları (templates/rates.html).
EXPECTED_CAROUSELS = {
    "carousel-offered": 3,
    "carousel-demanded": 3,
    "carousel-competitor": 3,
    "carousel-spread": 2,
}


def test_four_carousels_with_exact_slide_counts():
    html = _html()
    roots = set(re.findall(r'class="mv-carousel" id="([\w-]+)"', html))
    assert roots == set(EXPECTED_CAROUSELS), roots
    for root, expected in EXPECTED_CAROUSELS.items():
        # Kökten sonraki bölümde slayt say — kartlar sırayla dizili.
        after = html.split(f'id="{root}"', 1)[1]
        nxt = min((after.index(f'id="{o}"') for o in EXPECTED_CAROUSELS
                   if o != root and f'id="{o}"' in after), default=len(after))
        assert after[:nxt].count("mvc-slide") == expected, root


def test_no_extra_cards_beyond_original():
    """'Ekstra kart/blok ekleme' kuralı: tam olarak 4 kart olmalı."""
    assert _html().count('class="accordion"') == 4


#: Eski sayfanın plot kimlikleri — birebir korunur.
EXPECTED_PLOTS = [
    "chart-offered", "chart-offered-hourly", "chart-offered-dist",
    "chart-demanded", "chart-demanded-hourly", "chart-demanded-dist",
    "chart-competitor", "chart-competitor-hourly", "chart-competitor-dist",
    "chart-spread", "chart-spread-cumulative",
]


def test_plot_ids_match_original():
    html = _html()
    for pid in EXPECTED_PLOTS:
        assert f'id="{pid}"' in html, pid


def test_metric_tiles_present():
    """Dağılım slaytlarındaki Ekstrem / Ekstrem+Yetki kutuları."""
    html = _html()
    for suffix in ("off", "dem", "comp"):
        assert f'id="metric-ekstrem-yetki-{suffix}"' in html, suffix
        assert f'id="metric-ekstrem-{suffix}"' in html, suffix


def test_carousel_controls_wired_to_existing_roots():
    html = _html()
    targets = set(re.findall(r'data-car-(?:prev|next|ind)="([\w-]+)"', html))
    assert targets == set(EXPECTED_CAROUSELS)


# ── Formül paritesi ──────────────────────────────────────────────────────────

#: Sayı üreten fonksiyonlar — eski sayfadan birebir portlandı.
PORTED_FUNCTIONS = [
    "groupRowsByHour", "calculateDistribution", "generateHourlyWeightedAvg",
    "generateCumulativeWeightedAvg", "generateCumulativeSpread",
    "generateSpreadSeries", "generateCumulativeCount", "generateHourlyCount",
    "getFixedValue", "getAmountBucketLabel",
]


def test_all_computation_functions_ported():
    old, new = OLD_JS.read_text(encoding="utf-8"), _js()
    for fn in PORTED_FUNCTIONS:
        assert fn in old, f"kaynak değişmiş: {fn}"
        assert fn in new, f"portlanmamış: {fn}"


def test_amount_buckets_match_original_thresholds():
    """Tutar kovaları eski eşiklerle aynı olmalı (rapor sayıları buna bağlı)."""
    new = _js()
    for threshold in ("5000000", "10000000", "25000000", "100000000",
                      "200000000", "500000000", "1000000000"):
        assert threshold in new, threshold


def test_weighted_average_not_plain_mean():
    """Oranlar tutar ağırlıklı — platform kuralı; düz ortalamaya düşülmemeli."""
    new = _js()
    assert "RESERVATION_AMT" in new
    assert "num += val * w" in new


def test_ekstrem_yetki_is_mode_line():
    """Ekstrem Yetki gün boyunca mod değeriyle düz çizgi (kaynak davranışı)."""
    new = _js()
    assert "getFixedValue" in new
    assert "EKSTREM_YETKI" in new


#: Eski updateChartsWithData'daki seri adları.
EXPECTED_SERIES = [
    "İşlem Sayısı", "Saatlik Adet", "Verilen Oran", "İstenen Oran",
    "Rakip Oranı", "Ekstrem Yetki",
    "Roll (Saatlik)", "Yeni (Saatlik)", "Roll (Kümülatif)", "Yeni (Kümülatif)",
]


def test_series_names_match_original():
    new = _js()
    for name in EXPECTED_SERIES:
        assert name in new, name


def test_old_defaults_preserved():
    """Eski panel varsayılanları: yalnız 32-35 vade, yalnız Son Revize."""
    new = _js()
    assert "VADE_DEFAULT = '32-35'" in new
    assert "'MAX'" in new


# ── Kit kullanımı (R4/R5 sözleşmesi) ─────────────────────────────────────────

def test_page_uses_outstanding_filter_kit():
    """Filtreler Outstanding bileşeniyle çizilir (All | None, bub-filter-*)."""
    page = (REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html").read_text(
        encoding="utf-8")
    assert 'id="fDims"' in page          # tek filtre satırı kabukta
    assert "renderBubFilters" in _js()
    bub = (REPO / "mevduat_panel/static/prisma/bubfilter.js").read_text(encoding="utf-8")
    assert "bub-filter-dd" in bub        # SPA sınıf adları — ayrı stil yok
    assert "pkf-" not in bub


def test_page_uses_apexcharts_via_shared_renderer():
    """Tema-duyarlı ortak renderer; sayfa kendi ApexCharts örneğini kurmaz."""
    new = _js()
    assert "MVP.renderChart(" in new
    assert "new ApexCharts(" not in new


def test_carousel_dispatches_resize_for_apex():
    """ApexCharts gizli kapta 0 genişlikle çizilir — slayt değişiminde resize."""
    src = CAROUSEL_JS.read_text(encoding="utf-8")
    assert "requestAnimationFrame" in src
    assert "new Event('resize')" in src


def test_shell_is_outstanding_homogeneous():
    """Kabuk Outstanding SPA'sıyla aynı stylesheet'leri yükler (homojenlik)."""
    page = (REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html").read_text(
        encoding="utf-8")
    for asset in ("mevduat_panel.css", "mevduat_prisma.css",
                  "bub-filter-panel", "mv-page-filters", "dashboard-title"):
        assert asset in page, asset
    # Karosel kontrolleri SPA'nın wf sınıflarını kullanır.
    html = _html()
    for cls in ("wf-carousel-nav", "wf-slide-label", "wf-nav-btn"):
        assert cls in html, cls


# ═══════════════════════════════════════════════════════════════════════════
# Rezervasyon Miktarları — eski templates/amounts.html + static/js/amounts.js
# ═══════════════════════════════════════════════════════════════════════════

AMT_HTML = REPO / "mevduat_panel/templates/mevduat_panel/prisma/amounts.html"
AMT_JS = REPO / "mevduat_panel/static/prisma/amounts.js"
AMT_OLD_JS = REPO / "static/js/amounts.js"

#: Eski sayfadaki 4 kart ve slayt sayıları.
AMT_CAROUSELS = {
    "carousel-time-series": 2,
    "carousel-dist-current": 3,
    "carousel-dist-incoming": 3,
    "carousel-dist-portfolio": 3,
}


def test_amounts_carousels_and_slide_counts():
    html = _render_page("amounts.html")
    roots = set(re.findall(r'class="mv-carousel" id="([\w-]+)"', html))
    assert roots == set(AMT_CAROUSELS), roots
    assert html.count("mvc-slide") == sum(AMT_CAROUSELS.values())
    assert html.count('class="accordion"') == 4


def test_amounts_plot_ids_match_original():
    html = _render_page("amounts.html")
    for pid in ("chart-time-cumulative", "chart-time-hourly",
                "chart-curr-pie-vol", "chart-curr-pie-count", "chart-curr-hist",
                "chart-inc-pie-vol", "chart-inc-pie-count", "chart-inc-hist",
                "chart-port-pie-vol", "chart-port-pie-count", "chart-port-hist"):
        assert f'id="{pid}"' in html, pid


def test_amounts_computation_functions_ported():
    old, new = AMT_OLD_JS.read_text(encoding="utf-8"), AMT_JS.read_text(encoding="utf-8")
    for fn in ("groupRowsByHour", "getDistributionBucket",
               "calculateDistributionStats", "calculateHistogramStats",
               "formatCurrency", "parseCurrencyStr", "calculateAllStats"):
        assert fn in old, f"kaynak değişmiş: {fn}"
        assert fn in new, f"portlanmamış: {fn}"


def test_amounts_histogram_bin_and_bands():
    """500k bin ve dağılım bantları kaynakla aynı olmalı (sayılar buna bağlı)."""
    new = AMT_JS.read_text(encoding="utf-8")
    assert "HIST_BIN = 500000" in new
    for band in ("0-5M", "5-10M", "10-25M", "25-100M", "100-200M",
                 "200-500M", "500-1000M", "1000M+"):
        assert band in new, band


def test_amounts_series_names_and_stacking():
    new = AMT_JS.read_text(encoding="utf-8")
    for name in ("Roll Hacim", "Yeni Hacim", "İşlem Adedi"):
        assert name in new, name
    # Hacim kolonları yığılır ve ortak eksen max'ıyla hizalanır.
    assert "stacked: true" in new
    assert "group: 'vol'" in new


def test_amounts_uses_kit_and_shared_renderer():
    html, new = _render_page("amounts.html"), AMT_JS.read_text(encoding="utf-8")
    assert "renderBubFilters" in new
    assert "MVP.renderChart(" in new
    assert "new ApexCharts(" not in new


# ═══════════════════════════════════════════════════════════════════════════
# Tarihsel Görünüm — eski templates/historic.html + static/js/historic.js
# ═══════════════════════════════════════════════════════════════════════════

HIS_HTML = REPO / "mevduat_panel/templates/mevduat_panel/prisma/historic.html"
HIS_JS = REPO / "mevduat_panel/static/prisma/historic.js"
HIS_OLD_JS = REPO / "static/js/historic.js"


def test_historic_has_three_cards_and_no_carousel():
    """Kaynakta karosel YOKTU — üç düz kart, her biri tek grafik."""
    html = _render_page("historic.html")
    assert html.count('class="accordion"') == 3
    assert "mv-carousel" not in html
    for pid in ("chart-trend-rates", "chart-trend-volume", "chart-trend-auth"):
        assert f'id="{pid}"' in html, pid


def test_historic_uses_date_range_not_single_day():
    """Kaynakta Litepicker range vardı — tek gün değil aralık."""
    html = _render_page("historic.html")
    assert 'id="fFrom"' in html and 'id="fTo"' in html
    assert 'id="fDate"' not in html


def test_historic_computation_functions_ported():
    old, new = HIS_OLD_JS.read_text(encoding="utf-8"), HIS_JS.read_text(encoding="utf-8")
    for fn in ("groupRowsByDay", "groupRowsByHourFull", "generateWeightedAvg",
               "generateIntradayCumulativeWeightedAvg",
               "generateSimpleCumulativePercentile", "getDailyFixedValue",
               "generateDailySum", "generateHourlyCount"):
        assert fn in old, f"kaynak değişmiş: {fn}"
        assert fn in new, f"portlanmamış: {fn}"


def test_historic_percentiles_and_threshold():
    """P90/P75/P50 ve 3. grafiğin ≥10 işlem eşiği kaynakla aynı."""
    new = HIS_JS.read_text(encoding="utf-8")
    for p in ("0.90", "0.75", "0.50"):
        assert p in new, p
    assert "MIN_HOURLY_COUNT = 10" in new


def test_historic_series_names_match_original():
    new = HIS_JS.read_text(encoding="utf-8")
    for name in ("Market Max", "Ekstrem Yetki", "Offered (W.Avg)",
                 "Competitor (W.Avg)", "Demanded (W.Avg)",
                 "Current Amount", "Incoming Amount",
                 "P90 (Rate)", "P75 (Rate)", "P50 (Median)"):
        assert name in new, name


def test_historic_daily_mode_carries_last_known():
    """Modu olmayan günde son bilinen değer taşınır (çizgi kopmasın)."""
    new = HIS_JS.read_text(encoding="utf-8")
    assert "lastKnown" in new


def test_historic_uses_kit_and_shared_renderer():
    html, new = _render_page("historic.html"), HIS_JS.read_text(encoding="utf-8")
    assert "renderBubFilters" in new
    assert "MVP.renderChart(" in new
    assert "new ApexCharts(" not in new


# ═══════════════════════════════════════════════════════════════════════════
# Rakip Faiz Analizi — eski templates/competitor.html + static/js/competitor.js
# ═══════════════════════════════════════════════════════════════════════════

CMP_HTML = REPO / "mevduat_panel/templates/mevduat_panel/prisma/competitor.html"
CMP_JS = REPO / "mevduat_panel/static/prisma/competitor.js"
CMP_OLD_JS = REPO / "static/js/competitor.js"
VENDOR = REPO / "mevduat_panel/static/vendor"


def test_competitor_cards_and_plots():
    """4 kart: snapshot (iki grafik), trend, AG-Grid tablosu, kaynakça."""
    html = _render_page("competitor.html")
    assert html.count('class="accordion"') == 4
    assert "mv-carousel" not in html          # kaynakta karosel yoktu
    for pid in ("chart-snapshot-bars", "chart-snapshot-change", "chart-trend",
                "competitor-grid", "source-links"):
        assert f'id="{pid}"' in html, pid


def test_competitor_uses_ag_grid_enterprise_not_plain_table():
    """Madde 6: tablo AG-Grid olmalı; satır gruplaması Enterprise ister."""
    html, new = _render_page("competitor.html"), CMP_JS.read_text(encoding="utf-8")
    assert "ag-grid-enterprise" in html
    assert "ag-theme-alpine" in html
    assert "agGrid.createGrid" in new
    assert "rowGroup: true" in new
    assert "rowGroupPanelShow" in new
    assert "renderTable" not in new           # düz HTML tablo kalmadı


def test_competitor_vendor_assets_present():
    for asset in ("ag-grid-enterprise.min.noStyle.js", "ag-theme-alpine.css",
                  "ag-grid.css"):
        assert (VENDOR / asset).is_file(), asset


def test_competitor_grid_columns_match_original():
    new = CMP_JS.read_text(encoding="utf-8")
    for field in ("BANKA_ADI", "VADE", "TUTAR", "TARIH", "FAIZ", "DOVIZ"):
        assert f"field: '{field}'" in new, field
    assert "aggFunc: 'max'" in new             # Faiz agregasyonu


def test_competitor_computation_functions_ported():
    old, new = CMP_OLD_JS.read_text(encoding="utf-8"), CMP_JS.read_text(encoding="utf-8")
    for fn in ("rangesOverlap", "bankColor", "fmtDate", "updateSourceLinks",
               "buildGroupedGrid"):
        assert fn in old, f"kaynak değişmiş: {fn}"
        assert fn in new, f"portlanmamış: {fn}"


def test_competitor_vade_filter_is_range_overlap():
    """Vade filtresi aralık ÖRTÜŞMESİ (kesişim), eşitlik değil."""
    new = CMP_JS.read_text(encoding="utf-8")
    assert "rMin <= p[1] && rMax >= p[0]" in new


def test_competitor_sector_average_series():
    """Sektör ortalaması ilk seri, kalın + kesikli (kaynak stili)."""
    new = CMP_JS.read_text(encoding="utf-8")
    assert "Sektör Ortalaması" in new
    assert "series.unshift" in new
    assert "var widths = [3]" in new
    assert "var dashes = [5]" in new


def test_competitor_llm_summary_is_optional():
    """Piyasa Özeti ucu portlanmadı; panel yapılandırılmadıkça gizli kalmalı."""
    html, new = _render_page("competitor.html"), CMP_JS.read_text(encoding="utf-8")
    assert 'id="summary-panel"' in html
    assert "hidden" in html
    assert "EP.competitorSummary" in new


def test_competitor_uses_filter_kit():
    html, new = _render_page("competitor.html"), CMP_JS.read_text(encoding="utf-8")
    assert "renderBubFilters" in new
    assert "MVP.renderChart(" in new
    assert "new ApexCharts(" not in new


# ═══════════════════════════════════════════════════════════════════════════
# R6 — ufak düzeltme turu (2026-07-29): kurallar docs/CUSTOM_PAGE_DESIGN.md
# ═══════════════════════════════════════════════════════════════════════════

PAGE_SHELL = REPO / "mevduat_panel/templates/mevduat_panel/prisma/_page.html"
CAROUSEL = REPO / "mevduat_panel/static/prisma/carousel.js"


def test_controls_live_in_sidebar_not_content():
    """R6.2 — tarih/ccy kontrolleri sol-alt kontrol panelinde (mvp_controls)."""
    shell = PAGE_SHELL.read_text(encoding="utf-8")
    assert "mvp_controls" in shell
    assert "sidebar-controls" in shell
    assert "margin-top:auto" in shell
    # İçerik alanındaki filtre satırında yalnız boyutlar kalır.
    assert "mvp_filters" not in shell
    for page in ("rates.html", "amounts.html", "historic.html", "competitor.html"):
        src = (REPO / "mevduat_panel/templates/mevduat_panel/prisma" / page).read_text(
            encoding="utf-8")
        assert "{% block mvp_controls %}" in src, page


def test_ag_grid_css_loads_in_head_before_panel_css():
    """R6.5 — dark override'lar ancak vendor CSS head'de ÖNCE yüklenirse kazanır."""
    shell = PAGE_SHELL.read_text(encoding="utf-8")
    # Yorum metinleri de bu adları içerir — GERÇEK link satırları karşılaştırılır.
    assert shell.index("filename='vendor/ag-theme-alpine.css'") < \
        shell.index("filename='mevduat_panel.css'")


def test_accordion_header_grows_plot_not_collapse():
    """R6.3 — başlık tıklaması plot büyütme; aç/kapa değil."""
    js = CAROUSEL.read_text(encoding="utf-8")
    assert "plot-max" in js
    shell = PAGE_SHELL.read_text(encoding="utf-8")
    assert ".accordion.plot-max .plot-container" in shell
    # Chart'lar kabı doldurur; kaplar açık yükseklik taşır.
    assert "height: '100%'" in _js()
    assert 'style="height:350px;"' in _html()


def test_competitor_bank_colors_restored_from_legacy():
    """R6.4 — kurumsal banka renkleri eski sistemden birebir."""
    new = CMP_JS.read_text(encoding="utf-8")
    old = CMP_OLD_JS.read_text(encoding="utf-8")
    for pair in ("'rgb(0, 51, 160)'", "'rgb(220, 0, 5)'",
                 "'rgb(253, 185, 19)'", "'rgb(19, 102, 178)'",
                 "'rgb(180, 78, 167)'"):
        assert pair in old, f"kaynak değişmiş: {pair}"
        assert pair in new, f"taşınmamış: {pair}"
    assert "FALLBACK_PALETTE" in new


def test_competitor_disclaimer_popup_restored():
    """R6.6 — kaynak bilgilendirme popup'ı (3 sn geri sayım + Anladım)."""
    html = _render_page("competitor.html")
    assert 'id="source-disclaimer"' in html
    assert 'id="popup-source-links"' in html
    assert 'id="btn-close-disclaimer"' in html
    js = CMP_JS.read_text(encoding="utf-8")
    assert "countdown-val" in js
    assert "Anladım" in js


def test_design_rules_doc_exists_and_is_referenced():
    """R6.7 — tasarım kuralları dosyası + CLAUDE.md hafıza işaretçisi."""
    doc = (REPO / "docs/CUSTOM_PAGE_DESIGN.md").read_text(encoding="utf-8")
    for key in ("bub-filter", "plot-max", "AG-Grid", "mvp_controls",
                "BANK_COLORS", "render çıktısına"):
        assert key in doc, key
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "CUSTOM_PAGE_DESIGN.md" in claude

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


def _html() -> str:
    return NEW_HTML.read_text(encoding="utf-8")


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
    roots = set(re.findall(r'class="pk-carousel" id="([\w-]+)"', html))
    assert roots == set(EXPECTED_CAROUSELS), roots
    for root, expected in EXPECTED_CAROUSELS.items():
        # Kökten sonraki bölümde slayt say — kartlar sırayla dizili.
        after = html.split(f'id="{root}"', 1)[1]
        nxt = min((after.index(f'id="{o}"') for o in EXPECTED_CAROUSELS
                   if o != root and f'id="{o}"' in after), default=len(after))
        assert after[:nxt].count("pk-carousel__slide") == expected, root


def test_no_extra_cards_beyond_original():
    """'Ekstra kart/blok ekleme' kuralı: tam olarak 4 kart olmalı."""
    assert _html().count('class="pk-card"') == 4


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
    """Filtreler Outstanding kitiyle çizilir (All | None)."""
    assert 'id="fDims"' in _html()
    assert "renderBubFilters" in _js()


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


def test_carousel_and_metric_styles_exist():
    css = KIT.read_text(encoding="utf-8")
    for cls in (".pk-carousel__slide", ".pk-carousel__nav", ".pk-metrics",
                ".pk-metric__value", ".pk-plot"):
        assert cls in css, cls


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
    html = AMT_HTML.read_text(encoding="utf-8")
    roots = set(re.findall(r'class="pk-carousel" id="([\w-]+)"', html))
    assert roots == set(AMT_CAROUSELS), roots
    assert html.count("pk-carousel__slide") == sum(AMT_CAROUSELS.values())
    assert html.count('class="pk-card"') == 4


def test_amounts_plot_ids_match_original():
    html = AMT_HTML.read_text(encoding="utf-8")
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
    html, new = AMT_HTML.read_text(encoding="utf-8"), AMT_JS.read_text(encoding="utf-8")
    assert 'id="fDims"' in html
    assert "renderBubFilters" in new
    assert "MVP.renderChart(" in new
    assert "new ApexCharts(" not in new

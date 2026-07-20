"""nim_panel Outstanding portu — hızlı doğrulama testleri.

Kapsam: saf hesap yardımcılarının kaynak sözleşmeleri + blueprint endpoint
kaydı + data_source kapıları. Motorların sayı-sayı kaynak-eşdeğerlik testleri
(dev.db tabanlı snapshot karşılaştırması) ofis makinesinde koşulacak ayrı
suite'tir; buradakiler CI-hafifi, bağımsız testlerdir.

Koşum: repo kökünden `python -m pytest nim_panel/tests -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nim_panel.engine.common import (  # noqa: E402
    _apply_demand_deposit,
    _aum_numeric_key,
    _convert_rate_series,
    _wavg,
)


# ── _convert_rate_series — kaynak app.py:1103 sözleşmesi ───────────────────

def test_rate_conv_simple_gecis_yok():
    r = pd.Series([0.45, 0.30])
    t = pd.Series([92.0, 32.0])
    out = _convert_rate_series(r, t, "simple")
    assert np.allclose(out, r)


def test_rate_conv_compound_act365():
    # (1 + r·t/365)^(365/t) − 1 satır bazında
    r = pd.Series([0.45])
    t = pd.Series([92.0])
    out = _convert_rate_series(r, t, "compound")
    want = (1 + 0.45 * 92 / 365) ** (365 / 92) - 1
    assert abs(out.iloc[0] - want) < 1e-12


def test_rate_conv_on_esdegeri():
    # ((1 + r·t/365)^(1/t) − 1)·365
    r = pd.Series([0.45])
    t = pd.Series([92.0])
    out = _convert_rate_series(r, t, "on")
    want = ((1 + 0.45 * 92 / 365) ** (1 / 92) - 1) * 365
    assert abs(out.iloc[0] - want) < 1e-12


def test_rate_conv_compound_on_sirasi():
    # Pozitif oranlar için: on < simple < compound (t > 1 gün)
    r = pd.Series([0.40])
    t = pd.Series([180.0])
    on = _convert_rate_series(r, t, "on").iloc[0]
    comp = _convert_rate_series(r, t, "compound").iloc[0]
    assert on < r.iloc[0] < comp


# ── _apply_demand_deposit — kaynak app.py:1133 sözleşmesi ──────────────────

def _demand_df():
    return pd.DataFrame({
        "DIM_SUBPRODUCT": ["KGH", "BTH", "STD"],
        "BALANCE": [100.0, 200.0, 300.0],
        "INTEREST_RATE": [0.40, 0.30, 0.20],
    })


def test_demand_effect_faiz_tutari_sabit():
    df = _demand_df()
    # demand_pct yüzde PUANI gelir (25 → p=0.25; kaynak app.py:1133 /100 böler)
    out = _apply_demand_deposit(df, 25.0)
    # KGH/BTH: bakiye ×(1+p), oran ÷(1+p) → faiz tutarı (B×r) değişmez
    for i in (0, 1):
        assert out.loc[i, "BALANCE"] == pytest.approx(df.loc[i, "BALANCE"] * 1.25)
        assert out.loc[i, "BALANCE"] * out.loc[i, "INTEREST_RATE"] == pytest.approx(
            df.loc[i, "BALANCE"] * df.loc[i, "INTEREST_RATE"])
    # Diğer satırlar birebir aynı
    assert out.loc[2, "BALANCE"] == 300.0
    assert out.loc[2, "INTEREST_RATE"] == 0.20


def test_demand_effect_orijinali_bozmaz():
    df = _demand_df()
    _ = _apply_demand_deposit(df, 0.5)
    assert df.loc[0, "BALANCE"] == 100.0  # df.copy() disiplini


def test_demand_effect_sifir_noop():
    df = _demand_df()
    out = _apply_demand_deposit(df, 0.0)
    pd.testing.assert_frame_equal(out, df)


# ── _aum_numeric_key — bant sıralaması ─────────────────────────────────────

def test_aum_band_siralamasi_sayisal():
    bands = ["AUM_100K_500K", "AUM_0_100K", "AUM_1M_5M", "AUM_500K_1M"]
    ordered = sorted(bands, key=_aum_numeric_key)
    assert ordered == ["AUM_0_100K", "AUM_100K_500K", "AUM_500K_1M", "AUM_1M_5M"]


# ── _wavg ──────────────────────────────────────────────────────────────────

def test_wavg_bakiye_agirlikli():
    x = pd.Series([0.10, 0.20])
    w = pd.Series([300.0, 100.0])
    assert _wavg(x, w) == pytest.approx(0.125)


# ── Blueprint endpoint kaydı ───────────────────────────────────────────────

EXPECTED_ENDPOINTS = {
    "/api/deposit_detail_dates",
    "/api/deposit_detail_waterfalls",
    "/api/daily_deposit_dates",
    "/api/daily_deposit_waterfalls",
    "/api/rate_drill",
    "/api/cost_rate_heatmap",
    "/api/hm_product_bar",
    "/api/deposit_product_daily",
    "/api/bubble_series",
    "/api/tenor_dates",
    "/api/tenor_monthly",
    "/api/tenor_daily",
    "/api/balance_dates",
    "/api/balance_monthly",
    "/api/balance_daily",
    "/api/balance_drill",
    # Faz A4 — Weekly Rollings
    "/api/weekly_rollings",
    "/api/weekly_segments",
    "/api/weekly_drilldown",
    # Faz A5 — New Business
    "/api/np/meta",
    "/api/np/volume_pricing",
    "/api/np/aum_rate_chart",
    "/api/np/segment_rate_bubble",
    "/api/np/rate_volume_bubble",
    "/api/np/rate_volume_heatmap",
    "/api/np/cell_timeseries",
    "/api/np/cell_drilldown",
    "/api/np/detail_prewarm",
    "/api/np/rate_volume_curve",
    # Faz A6 — Sector Comparison + BSC
    "/api/sector_deposit_rates",
    "/api/tcmb_rate_table",
    "/api/sector_blotter",
    "/api/sector_outstanding",
    "/api/sector_outstanding_monthly",
    "/api/sector_mix_attribution",
    "/api/sector_vade_mix",
    "/api/sector_vade_mix_pres",
    "/api/bsc_np_rate_series",
    "/api/bsc_np_monthly_table",
}


def test_blueprint_endpointleri_kayitli():
    from flask import Flask
    from nim_panel import nim_panel_bp

    app = Flask(__name__)
    app.register_blueprint(nim_panel_bp, url_prefix="/nim-panel")
    rules = {str(r) for r in app.url_map.iter_rules()}
    for ep in EXPECTED_ENDPOINTS:
        assert f"/nim-panel{ep}" in rules, f"eksik endpoint: {ep}"


def test_data_source_prod_client_kapisi():
    """DataClient yoksa net hata — sessiz boş sonuç yasak."""
    from flask import Flask
    from nim_panel.data_source import load_dataframe

    app = Flask(__name__)
    app.config["DATA_CLIENT"] = None
    with app.app_context():
        with pytest.raises(RuntimeError):
            load_dataframe("daily_deposit")


def test_data_source_bilinmeyen_sorgu():
    from nim_panel.data_source import _sql

    with pytest.raises(KeyError):
        _sql("../etc/passwd")

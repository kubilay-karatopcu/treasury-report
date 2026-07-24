"""Rezervasyon/fiyatlama JSON API'leri — legacy ``/api/data/*`` portu.

Kaynak: PRISMA öncesi ``app.py`` route'ları (rates_page/amounts_page/
api_oranlar_data/api_miktarlar_data/historic_page/competitor_page). Legacy
sayfalar `current_df`/`competitor_df`'ten okurdu; burada aynı kolon alt-kümeleri
``engine/reservation_data`` cache'inden servis edilir.

Faz S0: yalnız veri endpoint'leri (PRISMA-native sayfalar Faz S1'de kendi HTML
route'larını ekler; LLM piyasa özeti Faz S3). Spesifik ``/api/reservations/*``
kuralları ``routes.py``'deki ``/api/<path:subpath>`` catch-all stub'ından önce
eşleşir (Flask kural spesifiklik sırası).

DataFrame→JSON: ``df.to_json(orient="records")`` + ``Response`` (asla ``jsonify``
— NaN emisyonu client ``JSON.parse``'ını kırar; platform kuralı).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Response, jsonify
from flask_login import login_required

from .routes import mevduat_panel_bp

# Legacy kolon alt-kümeleri (app.py'den birebir) ------------------------------
_ORANLAR_COLS = [
    "DATE_TIME_STR", "DATE_STR_CLEAN", "DATA_SRC", "CUST_TP", "VADE_BASLANGIC",
    "CCY_CODE", "CURRENTAMOUNT", "INCOMING_AMT", "RESERVATION_AMT",
    "TALEP_REVIZE_NO", "IS_MAX_REVIZE", "PERCENTILE_COMPETITOR_RTS",
    "OFFERED_RATE", "PERCENTILE_DEMANDED_RTS", "MARKET_MAX_RT", "EKSTREM_YETKI",
    "EKSTREM",
]
_MIKTARLAR_COLS = [
    "DATE_TIME_STR", "DATA_SRC", "CUST_TP", "VADE_BASLANGIC", "CCY_CODE",
    "RESERVATION_AMT", "TALEP_REVIZE_NO", "IS_MAX_REVIZE", "CURRENTAMOUNT",
    "INCOMING_AMT", "PORTFOLIO_AMT", "DATE_STR_CLEAN",
]
_HISTORIC_COLS = [
    "DATE_TIME_STR", "DATA_SRC", "CUST_TP", "VADE_BASLANGIC", "CCY_CODE",
    "RESERVATION_AMT", "TALEP_REVIZE_NO", "IS_MAX_REVIZE",
    "PERCENTILE_COMPETITOR_RTS", "OFFERED_RATE", "PERCENTILE_DEMANDED_RTS",
    "MARKET_MAX_RT", "CURRENTAMOUNT", "INCOMING_AMT", "EKSTREM_YETKI",
]
_COMPETITOR_COLS = [
    "DATE_STR", "VADE", "VADE_MIN", "VADE_MAX", "TUTAR", "TUTAR_MIN", "TUTAR_MAX",
    "FAIZ", "DOVIZ_CINSI", "BANKA_ADI", "KAYNAK",
]

_HISTORIC_WINDOW_DAYS = 30


def _json(df) -> Response:
    """DataFrame → JSON Response (NaN-güvenli, ISO tarih)."""
    return Response(df.to_json(orient="records", date_format="iso"),
                    mimetype="application/json")


def _reservation_df():
    from .engine.reservation_data import load_reservation_df
    return load_reservation_df()


def _competitor_df():
    from .engine.reservation_data import load_competitor_df
    return load_competitor_df()


def _valid(df, cols):
    return [c for c in cols if c in df.columns]


@mevduat_panel_bp.route("/api/reservations/dates")
@login_required
def api_reservation_dates() -> Response:
    """Seçilebilir tarih listesi (oranlar/miktarlar date-picker'ı için)."""
    df = _reservation_df()
    dates = sorted(df["DATE_STR_CLEAN"].unique().tolist()) if not df.empty else []
    return jsonify({"dates": dates, "latest": dates[-1] if dates else None})


@mevduat_panel_bp.route("/api/reservations/oranlar/<date_str>")
@login_required
def api_reservation_rates(date_str: str) -> Response:
    df = _reservation_df()
    if df.empty or "DATE_STR_CLEAN" not in df.columns:
        return _json(df.head(0))
    sub = df[df["DATE_STR_CLEAN"] == date_str][_valid(df, _ORANLAR_COLS)]
    return _json(sub)


@mevduat_panel_bp.route("/api/reservations/miktarlar/<date_str>")
@login_required
def api_reservation_amounts(date_str: str) -> Response:
    df = _reservation_df()
    if df.empty or "DATE_STR_CLEAN" not in df.columns:
        return _json(df.head(0))
    sub = df[df["DATE_STR_CLEAN"] == date_str][_valid(df, _MIKTARLAR_COLS)]
    return _json(sub)


@mevduat_panel_bp.route("/api/reservations/historic")
@login_required
def api_reservation_historic() -> Response:
    """Son 30 gün (legacy historic_page inline verisi → endpoint'e taşındı)."""
    df = _reservation_df()
    if df.empty or "DATE_STR_CLEAN" not in df.columns:
        return _json(df.head(0))
    cutoff = (datetime.now() - timedelta(days=_HISTORIC_WINDOW_DAYS)).strftime("%Y-%m-%d")
    sub = df[df["DATE_STR_CLEAN"] >= cutoff][_valid(df, _HISTORIC_COLS)]
    return _json(sub)


@mevduat_panel_bp.route("/api/reservations/competitor")
@login_required
def api_reservation_competitor() -> Response:
    """Rakip faiz verisi + banka listesi (legacy competitor_page)."""
    df = _competitor_df()
    banks = sorted(df["BANKA_ADI"].dropna().unique().tolist()) if not df.empty else []
    rows = df[_valid(df, _COMPETITOR_COLS)] if not df.empty else df
    payload = rows.to_json(orient="records", date_format="iso") if not df.empty else "[]"
    return Response(
        '{"banks": %s, "rows": %s}' % (jsonify(banks).get_data(as_text=True), payload),
        mimetype="application/json",
    )

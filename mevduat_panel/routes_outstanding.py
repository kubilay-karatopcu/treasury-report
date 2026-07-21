"""Outstanding Balance + Tenor endpoint'leri.

Kaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları
blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları
uyarlandı (bkz. mevduat_panel/tools/extract_a2.py).
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .request_params import (
    _parse_balance_dim_filters,
    _parse_balance_merges,
    _parse_dim_filters,
    _parse_tenor_merges,
)
from .routes import mevduat_panel_bp

log = logging.getLogger("mevduat_panel")


# Kaynak: app.py 3688-3689 — PlotlyJSONEncoder numpy/pandas/NaN'i güvenle
# serileştirir (jsonify NaN tuzağı yok).
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

from .engine.common import _aum_numeric_key, _convert_rate_series, _wavg
from .engine.outstanding import (
    BalanceAnalysisEngine,
    DailyBalanceEngine,
    DailyDepositEngine,
    DailyTenorEngine,
    DepositDetailEngine,
    SwapHedgeEngine,
    TenorAnalysisEngine,
    _apply_balance_merges,
    _apply_tenor_mode,
    _build_balance_payload,
    _build_tenor_daily_evolution,
    _build_tenor_payload,
)

# ── app.py 4298-4367 ──
@mevduat_panel_bp.route("/api/tenor_dates", methods=["GET"])
@login_required
def api_tenor_dates():
    """Return monthly + daily date lists used by the Tenor Analysis tabs.

    filter_meta = monthly ∪ daily boyut evrenleri. Panel her iki sekmede ortak;
    MATURITY_BUCKET yalnız günlük kaynakta dolu olabildiğinden (dev'de aylık
    PRODUCT anahtarında vade suffix'i yok) birleşim alınır — aksi halde
    MATURITY_BUCKET dropdown'ı hiç görünmez ve gruplama yapılamaz.
    """
    try:
        fm_m = TenorAnalysisEngine.get_filter_meta()
        fm_d = DailyTenorEngine.get_filter_meta()
        union: Dict[str, set] = {}
        for fm in (fm_m, fm_d):
            for dim, vals in fm.items():
                union.setdefault(dim, set()).update(vals)
        filter_meta: Dict[str, List[str]] = {}
        for dim, vals in union.items():
            if dim == "MATURITY_BUCKET":
                filter_meta[dim] = sorted(vals, key=TenorAnalysisEngine._bucket_lower)
            elif dim == "AUM":
                filter_meta[dim] = sorted(vals, key=_aum_numeric_key)
            else:
                filter_meta[dim] = sorted(vals)
        return _json_response({
            "ok": True,
            "monthly_dates": DepositDetailEngine.get_dates(),
            "daily_dates":   DailyDepositEngine.get_dates(),
            "filter_meta":   filter_meta,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/tenor_monthly", methods=["GET"])
@login_required
def api_tenor_monthly():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    mode = request.args.get("mode", "tenor").strip().lower()
    if mode not in ("tenor", "dtm"):
        mode = "tenor"
    try:
        dim_filters = _parse_dim_filters(request.args)
        merges = _parse_tenor_merges(request.args)
        payload = TenorAnalysisEngine.build_snapshot(date_0, date_1, dim_filters, mode=mode, merges=merges)
        return _json_response({"ok": True, **payload})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/tenor_daily", methods=["GET"])
@login_required
def api_tenor_daily():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    mode = request.args.get("mode", "tenor").strip().lower()
    if mode not in ("tenor", "dtm"):
        mode = "tenor"
    try:
        dim_filters = _parse_dim_filters(request.args)
        merges = _parse_tenor_merges(request.args)
        payload = DailyTenorEngine.build_snapshot(date_0, date_1, dim_filters, mode=mode, merges=merges)
        return _json_response({"ok": True, **payload})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 4454-4487 ──
@mevduat_panel_bp.route("/api/balance_dates", methods=["GET"])
@login_required
def api_balance_dates():
    """Date lists + filter dim universe for the Balance Analysis tabs.

    filter_meta = monthly ∪ daily boyut evrenleri (panel iki sekmede ortak;
    tenor_dates ile aynı desen). MATURITY_BUCKET numerik sıralı; AUM sayısal
    alt-sınıra göre; diğerleri alfabetik.
    """
    try:
        fm_m = BalanceAnalysisEngine.get_filter_meta()
        fm_d = DailyBalanceEngine.get_filter_meta()
        union: Dict[str, set] = {}
        for fm in (fm_m, fm_d):
            for dim, vals in fm.items():
                union.setdefault(dim, set()).update(vals)
        filter_meta: Dict[str, List[str]] = {}
        for dim, vals in union.items():
            if dim == "MATURITY_BUCKET":
                filter_meta[dim] = sorted(vals, key=TenorAnalysisEngine._bucket_lower)
            elif dim == "AUM":
                filter_meta[dim] = sorted(vals, key=_aum_numeric_key)
            else:
                filter_meta[dim] = sorted(vals)
        return _json_response({
            "ok": True,
            "monthly_dates": DepositDetailEngine.get_dates(),
            "daily_dates":   DailyDepositEngine.get_dates(),
            "filter_meta":   filter_meta,
            "dimensions":    BalanceAnalysisEngine.DIMENSIONS,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 4849-4895 ──
@mevduat_panel_bp.route("/api/balance_monthly", methods=["GET"])
@login_required
def api_balance_monthly():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    decomp = request.args.get("decomp", "SEGMENT").strip().upper()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    if decomp not in BalanceAnalysisEngine.DIMENSIONS:
        decomp = "SEGMENT"
    decomp2 = request.args.get("decomp2", "AUM").strip().upper()
    if decomp2 not in BalanceAnalysisEngine.DIMENSIONS:
        decomp2 = "AUM"
    try:
        dim_filters = _parse_balance_dim_filters(request.args)
        merges = _parse_balance_merges(request.args)
        payload = BalanceAnalysisEngine.build_snapshot(date_0, date_1, decomp, dim_filters, merges,
                                                       decomp2_dim=decomp2)
        return _json_response({"ok": True, **payload})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.info("BALANCE_MONTHLY ERROR:\n" + tb)
        return _json_response({"ok": False, "error": str(e), "traceback": tb}, status=500)


@mevduat_panel_bp.route("/api/balance_daily", methods=["GET"])
@login_required
def api_balance_daily():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    decomp = request.args.get("decomp", "SEGMENT").strip().upper()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    if decomp not in DailyBalanceEngine.DIMENSIONS:
        decomp = "SEGMENT"
    decomp2 = request.args.get("decomp2", "AUM").strip().upper()
    if decomp2 not in DailyBalanceEngine.DIMENSIONS:
        decomp2 = "AUM"
    try:
        dim_filters = _parse_balance_dim_filters(request.args)
        merges = _parse_balance_merges(request.args)
        payload = DailyBalanceEngine.build_snapshot(date_0, date_1, decomp, dim_filters, merges,
                                                    decomp2_dim=decomp2)
        return _json_response({"ok": True, **payload})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5015-5077 ──
@mevduat_panel_bp.route("/api/balance_drill", methods=["GET"])
@login_required
def api_balance_drill():
    """Daily balance time series for a single dimension value (drill-down).

    Query params:
        date_0, date_1   — date range (YYYY-MM-DD, inclusive)
        drill_dim        — dimension to filter by (SEGMENT/AUM/PRODUCT/CUSTOMER_TYPE)
        drill_value      — the category value clicked
        extra_dim        — optional secondary dimension (e.g. AUM when clicking heatmap)
        extra_value      — optional secondary value
        filter_*         — bubble filter params (same as balance_daily)
    """
    date_0      = request.args.get("date_0",      "").strip()
    date_1      = request.args.get("date_1",      "").strip()
    drill_dim   = request.args.get("drill_dim",   "SEGMENT").strip().upper()
    drill_value = request.args.get("drill_value", "").strip()
    extra_dim   = request.args.get("extra_dim",   "").strip().upper()
    extra_value = request.args.get("extra_value", "").strip()

    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)

    try:
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=400)

    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }
    try:
        dim_filters = _parse_balance_dim_filters(request.args)
        merges = _parse_balance_merges(request.args)
        df, _ = DailyDepositEngine._load()
        df = df[(df["DAT"] >= d0) & (df["DAT"] <= d1)].copy()
        df = BalanceAnalysisEngine._filter_by_dims(df, dim_filters)
        df = _apply_balance_merges(df, merges)

        drill_col = _DIM_COL.get(drill_dim)
        if drill_col and drill_col in df.columns and drill_value:
            df = df[df[drill_col].astype(str) == drill_value]

        if extra_dim and extra_value:
            extra_col = _DIM_COL.get(extra_dim)
            if extra_col and extra_col in df.columns:
                df = df[df[extra_col].astype(str) == extra_value]

        if df.empty:
            return _json_response({"ok": True, "dates": [], "balance_m": [], "drill_value": drill_value})

        daily = df.groupby("DAT")["BALANCE"].sum().sort_index()
        dates     = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in daily.index]
        balance_m = (daily / 1e6).round(2).tolist()
        return _json_response({"ok": True, "dates": dates, "balance_m": balance_m,
                               "drill_value": drill_value})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)




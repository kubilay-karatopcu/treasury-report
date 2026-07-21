"""Outstanding Cost Analysis endpoint'leri.

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

from .engine.common import (
    _RATE_CONV_MODES,
    _aum_numeric_key,
    _convert_rate_series,
    _cost_bubble_source,
    _rate_heatmap_seg_aum,
    _wavg,
)
from .engine.outstanding import (
    BalanceAnalysisEngine,
    DailyBalanceEngine,
    DailyDepositEngine,
    DepositDetailEngine,
    TenorAnalysisEngine,
    _apply_balance_merges,
)

# ── app.py 4199-4279 ──
@mevduat_panel_bp.route("/api/deposit_detail_dates", methods=["GET"])
@login_required
def api_deposit_detail_dates():
    try:
        dates = DepositDetailEngine.get_dates()
        return _json_response({"ok": True, "dates": dates})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/deposit_detail_waterfalls", methods=["GET"])
@login_required
def api_deposit_detail_waterfalls():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    dims_arg = request.args.get("dims", "").strip()
    dims = [d for d in dims_arg.split(",") if d] if dims_arg else None
    if dims is not None and not any(d in DepositDetailEngine.DIMENSIONS for d in dims):
        return _json_response(
            {"ok": False, "error": "Select at least one valid dimension (PRODUCT / CUSTOMER_TYPE / AUM / SEGMENT)."},
            status=400,
        )
    try:
        _inc_tenor = request.args.get("tenor_filter", "").strip() == "1"
        _rconv = request.args.get("rate_conv", "simple").strip().lower()
        if _rconv not in _RATE_CONV_MODES:
            _rconv = "simple"
        _demand = request.args.get("demand_pct", type=float) or 0.0
        dep_info, c1, c2, c3, c4, cbg, bbal, brate, bfmeta, bpdims, rate_hm = DepositDetailEngine.build_waterfalls(date_0, date_1, dims=dims, include_tenor=_inc_tenor, rate_conv=_rconv, demand_pct=_demand)
        return _json_response({
            "ok": True,
            "dep_info": dep_info,
            "figs": {"wf1": c1, "wf2": c2, "wf3": c3, "wf4": c4, "wf2_bg": cbg,
                     "bubble_balance": bbal, "bubble_rate": brate,
                     "bubble_filter_meta": bfmeta, "bubble_product_dims": bpdims,
                     "rate_heatmap": rate_hm},
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/daily_deposit_dates", methods=["GET"])
@login_required
def api_daily_deposit_dates():
    try:
        dates = DailyDepositEngine.get_dates()
        return _json_response({"ok": True, "dates": dates})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/daily_deposit_waterfalls", methods=["GET"])
@login_required
def api_daily_deposit_waterfalls():
    date_0 = request.args.get("date_0", "").strip()
    date_1 = request.args.get("date_1", "").strip()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    dims_arg = request.args.get("dims", "").strip()
    dims = [d for d in dims_arg.split(",") if d] if dims_arg else None
    if dims is not None and not any(d in DailyDepositEngine.DIMENSIONS for d in dims):
        return _json_response(
            {"ok": False, "error": "Select at least one valid dimension (PRODUCT / CUSTOMER_TYPE / AUM / SEGMENT)."},
            status=400,
        )
    try:
        _rconv = request.args.get("rate_conv", "simple").strip().lower()
        if _rconv not in _RATE_CONV_MODES:
            _rconv = "simple"
        _demand = request.args.get("demand_pct", type=float) or 0.0
        dep_info, c1, c2, c3, c4, cbg, bbal, brate, bfmeta, bpdims, rate_hm = DailyDepositEngine.build_waterfalls(date_0, date_1, dims=dims, include_tenor=True, rate_conv=_rconv, demand_pct=_demand)
        return _json_response({
            "ok": True,
            "dep_info": dep_info,
            "figs": {"wf1": c1, "wf2": c2, "wf3": c3, "wf4": c4, "wf2_bg": cbg,
                     "bubble_balance": bbal, "bubble_rate": brate,
                     "bubble_filter_meta": bfmeta, "bubble_product_dims": bpdims,
                     "rate_heatmap": rate_hm},
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5078-5139 ──
@mevduat_panel_bp.route("/api/rate_drill", methods=["GET"])
@login_required
def api_rate_drill():
    """Daily weighted-avg interest rate time series for a single (Segment, AUM) cell.

    Mirrors /api/balance_drill but returns rate (% with 2 decimals) instead of balance.
    Days with zero balance return None so the line breaks cleanly.
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
            return _json_response({"ok": True, "dates": [], "rate_pct": [], "drill_value": drill_value})

        df["_wr"] = df["BALANCE"] * df["INTEREST_RATE"]
        agg = df.groupby("DAT")[["BALANCE", "_wr"]].sum().sort_index()
        dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in agg.index]
        rate_pct = [
            round(float(wr / b) * 100.0, 2) if b > 0 else None
            for wr, b in zip(agg["_wr"].tolist(), agg["BALANCE"].tolist())
        ]
        return _json_response({"ok": True, "dates": dates, "rate_pct": rate_pct,
                               "drill_value": drill_value})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5140-5205 ──
@mevduat_panel_bp.route("/api/cost_rate_heatmap", methods=["GET"])
@login_required
def api_cost_rate_heatmap():
    """Rebuild the Cost Analysis interest-rate heatmap for a chosen Decomposition Dim.

    Rows follow ``decomp`` (Y axis); columns are always AUM. The weighted-average
    rate is balance-weighted inside ``_rate_heatmap_seg_aum`` (Σ(BAL·RATE)/Σ(BAL)),
    so the cell values match the embedded Segment × AUM heatmap when decomp=SEGMENT.

    Query params:
        source  — "monthly" → DepositDetailEngine (MONTH) / "daily" → DailyDepositEngine (DAT)
        date_0  — snapshot t0
        date_1  — snapshot t1
        decomp  — SEGMENT | AUM | PRODUCT | SUBPRODUCT | CUSTOMER_TYPE (row axis)
        decomp2 — same options (column / X axis, "Second Dec. Dim"; default AUM)
        filter_* / merges — bubble filter panel state (same wire format as
                            /api/balance_drill); heatmap reflects the panel.
    """
    date_0  = request.args.get("date_0", "").strip()
    date_1  = request.args.get("date_1", "").strip()
    src     = request.args.get("source", "monthly").strip().lower()
    decomp  = request.args.get("decomp",  "SEGMENT").strip().upper()
    decomp2 = request.args.get("decomp2", "AUM").strip().upper()
    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)

    _DIM_COL = {
        "PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER", "AUM": "DIM_AUM", "SEGMENT": "DIM_SEGMENT",
    }
    row_col = _DIM_COL.get(decomp, "DIM_SEGMENT")
    col_col = _DIM_COL.get(decomp2, "DIM_AUM")
    try:
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=400)
    try:
        if src == "monthly":
            df, _ = DepositDetailEngine._load()
            date_col = "MONTH"
        else:
            df, _ = DailyDepositEngine._load()
            date_col = "DAT"
        # Bubble filter panel state — heatmap sayfadaki filtrelere uyar.
        dim_filters = _parse_balance_dim_filters(request.args)
        merges      = _parse_balance_merges(request.args)
        df = BalanceAnalysisEngine._filter_by_dims(df, dim_filters)
        df = _apply_balance_merges(df, merges)
        # Rate Type dönüşümü (Outstanding Cost "Rate Type" seçicisi) — satır bazında.
        _rconv = request.args.get("rate_conv", "simple").strip().lower()
        if _rconv in ("compound", "on"):
            _tcol = "TENOR_RATE" if "TENOR_RATE" in df.columns else "AGIRLIKLI_ORT_TENOR"
            if _tcol in df.columns:
                df = df.copy()
                df["INTEREST_RATE"] = _convert_rate_series(df["INTEREST_RATE"], df[_tcol], _rconv)
        # All DIM columns needed so rows can follow any decomp.
        keep = ["BALANCE", "INTEREST_RATE"] + [c for c in _DIM_COL.values() if c in df.columns]
        df0 = df[df[date_col] == d0][keep].copy()
        df1 = df[df[date_col] == d1][keep].copy()
        rate_hm = _rate_heatmap_seg_aum(df0, df1, row_col=row_col, col_col=col_col)
        return _json_response({"ok": True, "rate_heatmap": rate_hm,
                               "decomp": decomp, "decomp2": decomp2})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5206-5399 ──
@mevduat_panel_bp.route("/api/hm_product_bar", methods=["GET"])
@login_required
def api_hm_product_bar():
    """Product-level snapshot for a heatmap (Segment × AUM) cell.

    Returns balance and rate metrics per product at both t0 and t1.
    All dimension filters (segment, aum) are optional — omit to aggregate.

    Query params:
        date_0   — snapshot date at t0 (YYYY-MM-DD)
        date_1   — snapshot date at t1 (YYYY-MM-DD)
        source   — "daily"   → DailyDepositEngine, exact DAT match (default)
                   "monthly" → DepositDetailEngine, exact MONTH match
        segment  — DIM_SEGMENT filter (omit = all; "" = filter for empty string)
        aum      — DIM_AUM filter     (omit = all; "" = filter for empty string)
        cust_tp  — DIM_CUSTOMER filter (omit = all; "" = filter for empty string)
        filter_* / merge_*  — same shape as /api/balance_drill
    """
    date_0  = request.args.get("date_0",  "").strip()
    date_1  = request.args.get("date_1",  "").strip()
    src     = request.args.get("source",  "daily").strip().lower()
    # None = param not sent (Total → no filter); "" = param sent as empty string (filter for "")
    _seg_raw  = request.args.get("segment")
    _aum_raw  = request.args.get("aum")
    _cust_raw = request.args.get("cust_tp")
    segment  = _seg_raw.strip()  if _seg_raw  is not None else None
    aum      = _aum_raw.strip()  if _aum_raw  is not None else None
    cust_tp  = _cust_raw.strip() if _cust_raw is not None else None
    # Ek boyut filtreleri (drill edilen hücrenin Decomposition Dim değeri PRODUCT/
    # SUBPRODUCT ise). None = filtre yok; "" = boş-string değeri için filtre.
    _prod_raw = request.args.get("product")
    _subp_raw = request.args.get("subproduct")
    product    = _prod_raw.strip() if _prod_raw is not None else None
    subproduct = _subp_raw.strip() if _subp_raw is not None else None

    # Waterfall/companion-bar COMPOSITE drill (Cost/Deposit). Waterfall bar.x =
    # aktif "Dimensions" boyutlarının "_" ile birleşimi (bkz. deposit_product_daily
    # _PROD). wf_product + wf_dims verilirse per-dim filtreler yerine bu bileşik
    # anahtarla eşleşen kalem seçilir, sonra break_dim'e göre kırılır.
    wf_product  = request.args.get("wf_product")   # None = klasik per-dim yol
    _wf_dims_a  = request.args.get("wf_dims", "")
    wf_dims     = [d for d in _wf_dims_a.split(",") if d]

    # Kırılım boyutu (Second Dim). Hangi DIM'e göre alt-kırılım — varsayılan PRODUCT.
    break_dim = request.args.get("break_dim", "PRODUCT").strip().upper()
    # TENOR → DIM_BUCKET: her iki deposit engine'i de vade bucket'ı DIM_BUCKET
    # kolonunda tutar (Tenor Analysis'teki "Maturity Buckets" ile aynı gruplama).
    _BREAK_COL = {"PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT",
                  "CUSTOMER_TYPE": "DIM_CUSTOMER", "SEGMENT": "DIM_SEGMENT", "AUM": "DIM_AUM",
                  "TENOR": "DIM_BUCKET"}
    break_col = _BREAK_COL.get(break_dim, "DIM_PRODUCT")

    if not date_0 or not date_1:
        return _json_response({"ok": False, "error": "date_0 and date_1 are required."}, status=400)
    try:
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=400)

    SCALE = 1_000_000.0
    try:
        if src == "monthly":
            df, _ = DepositDetailEngine._load()
            date_col = "MONTH"
        else:
            df, _ = DailyDepositEngine._load()
            date_col = "DAT"

        # Rate Type dönüşümü (Outstanding Cost seçicisi) — breakdown bar / product
        # bar oranları da sayfadaki gösterim tipine uyar (satır bazında, kendi vade).
        _rconv = request.args.get("rate_conv", "simple").strip().lower()
        if _rconv in ("compound", "on"):
            _tcol = "TENOR_RATE" if "TENOR_RATE" in df.columns else "AGIRLIKLI_ORT_TENOR"
            if _tcol in df.columns:
                df = df.copy()
                df["INTEREST_RATE"] = _convert_rate_series(df["INTEREST_RATE"], df[_tcol], _rconv)

        # Bubble-state filters / merges — apply for BOTH sources so the bar
        # totals match the heatmap cell exactly (Balance Analysis monthly tab
        # also carries dim_filters / merges via the ba-mon bubble state).
        dim_filters = _parse_balance_dim_filters(request.args)
        merges      = _parse_balance_merges(request.args)
        df = BalanceAnalysisEngine._filter_by_dims(df, dim_filters)
        df = _apply_balance_merges(df, merges)

        # Bileşik (waterfall) yol: aktif boyut kolonlarından _PROD kur, wf_product'a eşitle.
        _wf_cols = ([_BREAK_COL[d] for d in wf_dims
                     if d in _BREAK_COL and _BREAK_COL[d] in df.columns]
                    if wf_product is not None else [])

        def _snap(d: pd.Timestamp) -> pd.DataFrame:
            sub = df[df[date_col] == d].copy()
            if wf_product is not None:
                # Composite key eşleşmesi — per-dim filtreler bu yolda atlanır.
                if _wf_cols:
                    comp = (sub[_wf_cols].astype(str)
                            .apply(lambda r: "_".join(v for v in r
                                   if v and v not in ("nan", "None", "")), axis=1))
                    sub = sub[comp == wf_product]
                else:
                    sub = sub.iloc[0:0]
                return sub
            # None → param not sent (Total click, no filter); "" → filter for empty string.
            if segment is not None and "DIM_SEGMENT" in sub.columns:
                sub = sub[sub["DIM_SEGMENT"].astype(str) == segment]
            if aum is not None and "DIM_AUM" in sub.columns:
                sub = sub[sub["DIM_AUM"].astype(str) == aum]
            if cust_tp is not None and "DIM_CUSTOMER" in sub.columns:
                sub = sub[sub["DIM_CUSTOMER"].astype(str) == cust_tp]
            if product is not None and "DIM_PRODUCT" in sub.columns:
                sub = sub[sub["DIM_PRODUCT"].astype(str) == product]
            if subproduct is not None and "DIM_SUBPRODUCT" in sub.columns:
                sub = sub[sub["DIM_SUBPRODUCT"].astype(str) == subproduct]
            return sub

        s0, s1 = _snap(d0), _snap(d1)

        def _agg(sub: pd.DataFrame) -> pd.DataFrame:
            if sub.empty or break_col not in sub.columns:
                return pd.DataFrame(columns=["BALANCE", "_wr", "CUST_COUNT"])
            g = sub.copy()
            g[break_col] = g[break_col].astype(str)
            g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
            if "CUST_COUNT" not in g.columns:
                g["CUST_COUNT"] = 0.0
            return g.groupby(break_col, dropna=False)[["BALANCE", "_wr", "CUST_COUNT"]].sum()

        a0, a1 = _agg(s0), _agg(s1)
        all_prods = sorted(set(a0.index) | set(a1.index))

        if not all_prods:
            empty: List = []
            return _json_response({"ok": True, "products": empty, "balance_t0_m": empty,
                                   "balance_t1_m": empty, "delta_m": empty,
                                   "rate_t0_pct": empty, "rate_t1_pct": empty, "delta_bps": empty,
                                   "count_t0": empty, "count_t1": empty, "count_delta": empty})

        bal_t0_m, bal_t1_m, delta_m_list = [], [], []
        r_t0, r_t1, d_bps = [], [], []
        cnt_t0, cnt_t1, cnt_delta = [], [], []
        for p in all_prods:
            b0  = float(a0["BALANCE"].get(p, 0.0))
            b1  = float(a1["BALANCE"].get(p, 0.0))
            wr0 = float(a0["_wr"].get(p, 0.0))
            wr1 = float(a1["_wr"].get(p, 0.0))
            c0  = float(a0["CUST_COUNT"].get(p, 0.0)) if "CUST_COUNT" in a0.columns else 0.0
            c1  = float(a1["CUST_COUNT"].get(p, 0.0)) if "CUST_COUNT" in a1.columns else 0.0
            rate0 = wr0 / b0 if b0 else 0.0
            rate1 = wr1 / b1 if b1 else 0.0
            # 6-decimal in M units preserves precision even for sub-1M balances
            # (display formatters round further; hesap için tam precision şart).
            bal_t0_m.append(round(b0 / SCALE, 6))
            bal_t1_m.append(round(b1 / SCALE, 6))
            delta_m_list.append(round((b1 - b0) / SCALE, 6))
            r_t0.append(round(rate0 * 100.0, 4) if b0 else None)
            r_t1.append(round(rate1 * 100.0, 4) if b1 else None)
            d_bps.append(round((rate1 - rate0) * 10000.0, 2) if b0 and b1 else None)
            cnt_t0.append(round(c0, 0)); cnt_t1.append(round(c1, 0))
            cnt_delta.append(round(c1 - c0, 0))

        # AUM kırılımı bant sırasına göre (numeric lower bound); TENOR kırılımı
        # vade bucket alt sınırına göre (Maturity Buckets gibi); diğer boyutlar
        # abs(Δ) desc (en büyük hareket üstte).
        if break_dim == "AUM":
            order = sorted(range(len(all_prods)),
                           key=lambda i: _aum_numeric_key(all_prods[i]))
        elif break_dim == "TENOR":
            order = sorted(range(len(all_prods)),
                           key=lambda i: TenorAnalysisEngine._bucket_lower(all_prods[i]))
        else:
            order = sorted(range(len(all_prods)),
                           key=lambda i: abs(delta_m_list[i] if delta_m_list[i] is not None else 0.0),
                           reverse=True)
        def _sort(lst):
            return [lst[i] for i in order]

        return _json_response({
            "ok":           True,
            "break_dim":    break_dim,
            "products":     _sort(all_prods),
            "balance_t0_m": _sort(bal_t0_m),
            "balance_t1_m": _sort(bal_t1_m),
            "delta_m":      _sort(delta_m_list),
            "rate_t0_pct":  _sort(r_t0),
            "rate_t1_pct":  _sort(r_t1),
            "delta_bps":    _sort(d_bps),
            "count_t0":     _sort(cnt_t0),
            "count_t1":     _sort(cnt_t1),
            "count_delta":  _sort(cnt_delta),
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5400-5504 ──
@mevduat_panel_bp.route("/api/deposit_product_daily", methods=["GET"])
@login_required
def api_deposit_product_daily():
    """Return daily balance + rate series for a single product key over a date range.

    Query params:
        product  — composite product key (matches waterfall bar label)
        date_0   — start date (inclusive, YYYY-MM-DD)
        date_1   — end date   (inclusive, YYYY-MM-DD)
        dims     — comma-separated list of active dimensions (same as waterfall call)
        align    — "monthly" to treat date_0/date_1 as month markers. date_0 is
                   expanded to the first day of its month; date_1 is set to the
                   last DAT in data inside date_1's month. Used by Monthly
                   Averages / Deposit Detail tabs where dropdown values are
                   month-start markers representing the whole month average.
        members  — optional comma-separated list of underlying product keys.
                   When present the response aggregates these products together;
                   used for bubbles that have been merged on the frontend.
    """
    product  = request.args.get("product",  "").strip()
    date_0   = request.args.get("date_0",   "").strip()
    date_1   = request.args.get("date_1",   "").strip()
    dims_arg = request.args.get("dims",     "").strip()
    align    = request.args.get("align",    "").strip().lower()
    members_arg = request.args.get("members", "").strip()
    if not product or not date_0 or not date_1:
        return _json_response({"ok": False, "error": "product, date_0 and date_1 are required."}, status=400)
    dims = [d for d in dims_arg.split(",") if d] if dims_arg else list(DailyDepositEngine.DIMENSIONS)
    dims = [d for d in DailyDepositEngine.DIMENSIONS if d in dims]
    if not dims:
        dims = list(DailyDepositEngine.DIMENSIONS)
    members = [m.strip() for m in members_arg.split(",") if m.strip()] if members_arg else []
    try:
        df, _ = DailyDepositEngine._load()
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d1 < d0:
            d0, d1 = d1, d0
        if align == "monthly":
            # d0 → start of its month
            d0 = pd.Timestamp(year=d0.year, month=d0.month, day=1)
            # d1 → last DAT in data inside d1's month
            d1_ms   = pd.Timestamp(year=d1.year, month=d1.month, day=1)
            d1_next = d1_ms + pd.offsets.MonthBegin(1)
            in_mon  = df[(df["DAT"] >= d1_ms) & (df["DAT"] < d1_next)]["DAT"]
            d1 = pd.Timestamp(in_mon.max()) if not in_mon.empty else (d1_next - pd.Timedelta(days=1))
        df_range = df[(df["DAT"] >= d0) & (df["DAT"] <= d1)].copy()
        if df_range.empty:
            return _json_response({"ok": True, "product": product, "dates": [], "balance_m": [], "rate_pct": []})
        # Rate Type dönüşümü — drill combo çizgisi de sayfadaki seçime uyar.
        _rconv = request.args.get("rate_conv", "simple").strip().lower()
        if _rconv in ("compound", "on") and "AGIRLIKLI_ORT_TENOR" in df_range.columns:
            df_range["INTEREST_RATE"] = _convert_rate_series(
                df_range["INTEREST_RATE"], df_range["AGIRLIKLI_ORT_TENOR"], _rconv)
        cols = [DailyDepositEngine._DIM_COL[d] for d in dims]
        df_range["_PROD"] = (
            df_range[cols]
            .astype(str)
            .apply(lambda r: "_".join(v for v in r if v and v not in ("nan", "None", "")), axis=1)
        )
        # Cost bubble kaynağı (TÜM boyutlar × DIM_BUCKET) ince granülerlikte →
        # bubble/merge üye anahtarları TAM boyut kompoziti + vade kovası SUFFIX'i
        # taşır. Drill dört anahtar biçimini de eşler: aktif-boyut kompoziti
        # (_PROD), vade-ekli (_PROD_BK), tam-boyut (_PROD_FULL) ve tam-boyut+vade
        # (_PROD_FULL_BK) — _cost_bubble_source ile aynı join kuralı.
        _bk_suffix = None
        if "DIM_BUCKET" in df_range.columns:
            _bk = df_range["DIM_BUCKET"].astype(str)
            _bk_suffix = _bk.map(lambda b: ("_" + b) if b and b not in ("nan", "None", "") else "")
            df_range["_PROD_BK"] = df_range["_PROD"] + _bk_suffix
        _full_cols = [DailyDepositEngine._DIM_COL[d] for d in DailyDepositEngine.DIMENSIONS]
        df_range["_PROD_FULL"] = (
            df_range[_full_cols]
            .astype(str)
            .apply(lambda r: "_".join(v for v in r if v and v not in ("nan", "None", "")), axis=1)
        )
        if _bk_suffix is not None:
            df_range["_PROD_FULL_BK"] = df_range["_PROD_FULL"] + _bk_suffix
        # When members is provided, aggregate across all listed underlying products.
        match_keys = members if members else [product]
        _mask = df_range["_PROD"].isin(match_keys) | df_range["_PROD_FULL"].isin(match_keys)
        if "_PROD_BK" in df_range.columns:
            _mask = _mask | df_range["_PROD_BK"].isin(match_keys)
        if "_PROD_FULL_BK" in df_range.columns:
            _mask = _mask | df_range["_PROD_FULL_BK"].isin(match_keys)
        sub = df_range[_mask].copy()
        if sub.empty and "GRUP_KEY" in df_range.columns:
            sub = df_range[df_range["GRUP_KEY"].isin(match_keys)].copy()
        if sub.empty:
            return _json_response({"ok": True, "product": product, "dates": [], "balance_m": [], "rate_pct": []})
        sub["_wr"] = sub["BALANCE"] * sub["INTEREST_RATE"]
        agg = sub.groupby("DAT")[["BALANCE", "_wr"]].sum().reset_index()
        agg["RATE_PCT"] = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"] * 100.0, 0.0)
        agg = agg.sort_values("DAT")
        SCALE = 1e6
        return _json_response({
            "ok": True,
            "product": product,
            "dates":     agg["DAT"].dt.strftime("%Y-%m-%d").tolist(),
            "balance_m": (agg["BALANCE"] / SCALE).round(2).tolist(),
            "rate_pct":  agg["RATE_PCT"].round(4).tolist(),
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 5505-5583 ──
@mevduat_panel_bp.route("/api/bubble_series", methods=["GET"])
@login_required
def api_bubble_series():
    """Bubble ZAMAN ÇİZELGESİ verisi (tam-ekran tarih slider'ı + play).

    Tek istekte: sabit date_0 fotoğrafının b0/r0'ı + istenen her son-tarih için
    ince hücre (TÜM boyutlar × vade — _cost_bubble_source ile aynı grain) bazında
    b1/r1. Frontend adımları CLIENT-SIDE aggregate edip oynatır → adım başına
    refetch yok, tempo deterministik. rate_conv satır bazında uygulanır.
    Birimler: b0/b1 ₺M, r0/r1 decimal. En fazla 120 tarih.

    Döner: {ok, products:[...], product_dims:{p:{DIM:val,...}}, b0:[...],
    r0:[...], steps:[{date, b1:[...], r1:[...]}]} — diziler products hizalı;
    üründe o tarihte veri yoksa 0.
    """
    src      = request.args.get("source", "daily").strip().lower()
    date_0   = request.args.get("date_0", "").strip()
    dates_arg = request.args.get("dates", "").strip()
    rconv    = request.args.get("rate_conv", "simple").strip().lower()
    if rconv not in _RATE_CONV_MODES:
        rconv = "simple"
    if not date_0 or not dates_arg:
        return _json_response({"ok": False, "error": "date_0 and dates are required."}, status=400)
    dates = [d for d in dates_arg.split(",") if d]
    if not dates or len(dates) > 120:
        return _json_response({"ok": False, "error": "Send between 1 and 120 dates."}, status=400)
    try:
        if src == "monthly":
            df, _ = DepositDetailEngine._load()
            date_col = "MONTH"
        else:
            df, _ = DailyDepositEngine._load()
            date_col = "DAT"
        _dim_map = {"PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT",
                    "CUSTOMER_TYPE": "DIM_CUSTOMER", "AUM": "DIM_AUM", "SEGMENT": "DIM_SEGMENT"}
        _dims = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
        d0 = pd.to_datetime(date_0)
        df0 = df[df[date_col] == d0].copy()
        if df0.empty:
            return _json_response({"ok": False, "error": f"No data for date_0={date_0}."}, status=400)
        _tcol = "TENOR_RATE" if "TENOR_RATE" in df0.columns else "AGIRLIKLI_ORT_TENOR"
        if rconv in ("compound", "on") and _tcol in df0.columns:
            df0["INTEREST_RATE"] = _convert_rate_series(df0["INTEREST_RATE"], df0[_tcol], rconv)
        M = 1e6
        prod_index: Dict[str, int] = {}
        products: List[str] = []
        b0_out: List[float] = []
        r0_out: List[float] = []
        pdims_out: Dict[str, Dict[str, str]] = {}
        raw_steps = []
        for ds in dates:
            dd_ = pd.to_datetime(ds)
            dfd = df[df[date_col] == dd_].copy()
            if rconv in ("compound", "on") and _tcol in dfd.columns:
                dfd["INTEREST_RATE"] = _convert_rate_series(dfd["INTEREST_RATE"], dfd[_tcol], rconv)
            m_bub, pdims = _cost_bubble_source(df0, dfd, _dims, _dim_map)
            s_b1, s_r1 = {}, {}
            for _, r in m_bub.iterrows():
                p = r["PRODUCT"]
                if p not in prod_index:
                    prod_index[p] = len(products)
                    products.append(p)
                    b0_out.append(round(float(r["b0"]) / M, 4))
                    r0_out.append(round(float(r["r0"]), 6))
                    pdims_out[p] = pdims.get(p, {})
                s_b1[p] = round(float(r["b1"]) / M, 4)
                s_r1[p] = round(float(r["r1"]), 6)
            raw_steps.append((ds, s_b1, s_r1))
        steps = [{"date": ds,
                  "b1": [s_b1.get(p, 0.0) for p in products],
                  "r1": [s_r1.get(p, 0.0) for p in products]}
                 for ds, s_b1, s_r1 in raw_steps]
        return _json_response({"ok": True, "products": products, "product_dims": pdims_out,
                               "b0": b0_out, "r0": r0_out, "steps": steps})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)




"""New Business — Volume & Pricing endpoint'leri.

Kaynak: NIM_calculation (bs_evolution5 @ c569ae3) — satır referansları blok
başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları uyarlandı
(bkz. mevduat_panel/tools/extract_a4a5.py).
"""

from __future__ import annotations

import json
import logging
import threading as _threading
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .routes import mevduat_panel_bp

from .data_source import load_dataframe
from .engine.common import _build_bubble_charts, _wavg
from .engine.np_agg import (
    _AUM_LABELS as NP_AUM_LABELS,
    aggregate_distribution as np_aggregate_distribution,
    aggregate_timeseries as np_aggregate_timeseries,
    apply_filters as np_apply_filters,
    compound_to_simple_pct as np_compound_to_simple_pct,
    get_dimension_values as np_get_dimension_values,
    load_np_data,
    simple_to_compound_pct_series as np_simple_to_compound_pct_series,
)
from .engine.outstanding_daily import (
    COMMON_AUM_ORDER,
    COMMON_AUM_TO_NP_BANDS,
    COMMON_TENOR_ORDER,
    COMMON_TENOR_TO_NP_GRP,
    COMMON_TENOR_TO_OS_VADE,
    NP_AUM_TO_COMMON,
    NP_TENOR_TO_COMMON,
    _require_mapped as os_require_mapped,
    aggregate_outstanding,
    load_outstanding_daily,
)
from .engine.weekly import _mask_full_nm


log = logging.getLogger("mevduat_panel")


# Kaynak: app.py 3688-3689
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

# ── app.py 6556-6635 ──
def _parse_list_param(val: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated query param into list, or None if empty."""
    if not val:
        return None
    items = [v.strip() for v in val.split(",") if v.strip()]
    return items or None


def _apply_tenor_buckets(tenor_grp, tenor_buckets):
    """Ortak VADE (DTM) bucket seçimini TENOR_GRP kısıtına çevirir.

    Frontend'in grafik-üstü VADE (DTM) çok-seçimli filtresi `tenor_buckets`
    (ortak 8-bucket) gönderir. Bunlar `COMMON_TENOR_TO_NP_GRP` ile new-prod
    TENOR_GRP değerlerine map'lenir. Varsa mevcut `tenor_grp` ile KESİŞİM alınır
    (ikisi de aktifse). Hiçbiri eşleşmezse '__no_match__' → boş sonuç (sessiz
    drop yok; kullanıcı filtreyi boş seçmiştir).
    """
    if tenor_buckets is None:
        return tenor_grp
    allowed_tg = set()
    for b in tenor_buckets:
        allowed_tg.update(COMMON_TENOR_TO_NP_GRP.get(b, []))
    if not allowed_tg:
        allowed_tg = {"__no_match__"}
    if tenor_grp:
        return [t for t in tenor_grp if t in allowed_tg] or ["__no_match__"]
    return sorted(allowed_tg)


def _parse_np_filters() -> dict:
    """Extract common NP filter query params from current request.

    VP bub-filter query'si (filter_CCY_CODE=a|b …) tercih edilir; yoksa eski
    comma param'lara (ccy=a,b) düşer. Böylece 3 New Business sayfası da ortak
    filtre panelini (pipe formatı) kullanabilir, eski çağrılar da çalışır.

    Vade filtresi artık ortak panele taşındı: `tenor_buckets` (VADE (DTM)
    çok-seçimli) TENOR_GRP kısıtına map'lenir (bkz. `_apply_tenor_buckets`).
    """
    def _pipe(k):
        v = request.args.get(k)
        return [x for x in v.split("|") if x] if v else None
    tenor_grp = _pipe("filter_TENOR_GRP") or _parse_list_param(request.args.get("tenor_grp"))
    tenor_grp = _apply_tenor_buckets(tenor_grp, _pipe("tenor_buckets"))
    return {
        "ccy":       _pipe("filter_CCY_CODE")    or _parse_list_param(request.args.get("ccy")),
        "cust_tp":   _pipe("filter_CUST_TP")     or _parse_list_param(request.args.get("cust_tp")),
        "segment":   _pipe("filter_SUB_SEGMENT") or _parse_list_param(request.args.get("segment")),
        "aum_band":  _pipe("filter_AUM_BAND")    or _parse_list_param(request.args.get("aum_band")),
        "campaign":  _pipe("filter_RELATED_PC")  or _parse_list_param(request.args.get("campaign")),
        "tenor_grp": tenor_grp,
        "date_from": request.args.get("date_from") or None,
        "date_to":   request.args.get("date_to") or None,
    }


def _freq_param() -> str:
    freq = request.args.get("freq", "W").upper()
    return freq if freq in ("D", "W") else "W"


def _np_records(df: pd.DataFrame) -> list:
    """Convert aggregated DataFrame rows to JSON-safe dicts."""
    records = []
    for row in df.itertuples(index=False):
        rec = {}
        for col in df.columns:
            v = getattr(row, col)
            if isinstance(v, float):
                rec[col] = None if pd.isna(v) else round(v, 4)
            elif hasattr(v, "item"):
                rec[col] = v.item()
            elif isinstance(v, pd.Timestamp):
                rec[col] = v.strftime("%Y-%m-%d")
            else:
                rec[col] = str(v)
        records.append(rec)
    return records



# ── app.py 6636-6750 ──
@mevduat_panel_bp.route("/api/np/meta", methods=["GET"])
@login_required
def api_np_meta():
    """Date range + filter dimension values for the Deposit Dashboard."""
    try:
        df = load_np_data()
        dims = np_get_dimension_values(df)
        return _json_response({
            "ok": True,
            "date_from": df["DAT"].min().strftime("%Y-%m-%d"),
            "date_to":   df["DAT"].max().strftime("%Y-%m-%d"),
            "dimensions": dims,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/np/aum_rate_chart", methods=["GET"])
@login_required
def api_np_aum_rate_chart():
    """Volume bars + WA rate lines decomposed by `decomp` dim for a single currency."""
    try:
        df = load_np_data()
        ccy = request.args.get("ccy", "TRY")
        date_from = request.args.get("date_from") or None
        date_to   = request.args.get("date_to")   or None
        freq = request.args.get("freq", "W").upper()
        if freq not in ("D", "W"):
            freq = "W"
        decomp = request.args.get("decomp", "AUM_BAND")
        valid_dims = {"AUM_BAND", "TENOR_GRP", "SUB_SEGMENT", "CUST_TP", "RELATED_PC"}
        if decomp not in valid_dims:
            decomp = "AUM_BAND"

        def _pipe_list(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        # Bubble filter params (filter_CCY_CODE overrides the inline ccy selector)
        bub_ccy       = _pipe_list("filter_CCY_CODE") or [ccy]
        bub_cust_tp   = _pipe_list("filter_CUST_TP")
        bub_aum_band  = _pipe_list("filter_AUM_BAND")
        bub_segment   = _pipe_list("filter_SUB_SEGMENT")
        bub_campaign  = _pipe_list("filter_RELATED_PC")
        bub_tenor_grp = _pipe_list("filter_TENOR_GRP")
        # VADE (DTM) ortak filtresi → TENOR_GRP kısıtı (heatmap ile aynı mantık).
        bub_tenor_grp = _apply_tenor_buckets(bub_tenor_grp, _pipe_list("tenor_buckets"))

        df_f = np_apply_filters(
            df,
            ccy=bub_ccy,
            cust_tp=bub_cust_tp,
            aum_band=bub_aum_band,
            segment=bub_segment,
            campaign=bub_campaign,
            tenor_grp=bub_tenor_grp,
            date_from=date_from,
            date_to=date_to,
        )

        # Total volume (for bars). Haftalık binler Date(End)'de biten rolling
        # 7-günlük pencereler (heatmap/hover ile aynı tanım) → aynı sayfadaki tüm
        # haftalık grafikler ortak hafta sınırlarını paylaşır.
        tot = np_aggregate_timeseries(df_f, group_by=[], freq=freq, week_anchor=date_to)
        dates = [str(d.date()) for d in tot["DATE"].tolist()]
        volumes = [round(v, 2) for v in tot["NP_HACIM"].tolist()]

        # ── Per-dimension grouping ──────────────────────────────────────────
        if decomp == "AUM_BAND":
            # Collapse 11 fine AUM bands → 6 coarse groups (chart-specific)
            _AUM_GROUP_MAP = {
                "0-1M":      "0-5M",   "1M-2M":     "0-5M",   "2M-5M":     "0-5M",
                "5M-10M":    "5-25M",  "10M-25M":   "5-25M",
                "25M-50M":   "25-50M",
                "50M-100M":  "50-100M",
                "100M-200M": "100-200M",
                "200M-500M": "200M+",  "500M-1B":   "200M+",  "1B+":       "200M+",
            }
            _GROUP_ORDER = ["0-5M","5-25M","25-50M","50-100M","100-200M","200M+"]
            df_g = df_f.copy()
            df_g["AUM_BAND"] = df_g["AUM_BAND"].astype(str).map(_AUM_GROUP_MAP).fillna("Bilinmiyor")
            by_band = np_aggregate_timeseries(df_g, group_by=["AUM_BAND"], freq=freq, week_anchor=date_to)
            present = set(by_band["AUM_BAND"].unique().tolist())
            bands = [b for b in _GROUP_ORDER if b in present]
        else:
            by_band = np_aggregate_timeseries(df_f, group_by=[decomp], freq=freq, week_anchor=date_to)
            present = set(by_band[decomp].astype(str).unique().tolist())
            if decomp == "TENOR_GRP":
                # Natural prefix order: "01_1-3" … "11_540+"
                _TENOR_ORDER = ["01_1-3","02_4-31","03_32-35","04_36-45","05_46-60",
                                "06_61-91","07_92-181","08_182-273","09_274-365",
                                "10_366-540","11_540+","99_DIGER"]
                bands = [b for b in _TENOR_ORDER if b in present]
            else:
                bands = sorted(present)
            # Cast the dim column to str for consistent indexing
            by_band[decomp] = by_band[decomp].astype(str)

        # Build per-band rate series aligned to the global date list
        date_set = list(tot["DATE"])
        rates = {}
        for band in bands:
            sub = by_band[by_band[decomp].astype(str) == band].set_index("DATE")
            rates[band] = [
                round(float(sub.loc[d, "NP_FAIZ"]), 4) if d in sub.index else None
                for d in date_set
            ]

        return _json_response({
            "ok": True, "ccy": ccy, "decomp": decomp,
            "dates": dates, "volumes": volumes,
            "bands": bands, "rates": rates,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# Port notu: bsc_np_rate_series + bsc_np_monthly_table (kaynak 6751-6981)
# sector_data'ya bağımlı — Faz A6 ile gelir.

# ── app.py 6982-7263 ──
@mevduat_panel_bp.route("/api/np/segment_rate_bubble", methods=["GET"])
@login_required
def api_np_segment_rate_bubble():
    """Cross-sectional bubble chart: RELATED_PC × AUM_BAND.

    Y = weighted-avg rate over the date range, bubble size = total volume.
    X-axis groups by RELATED_PC (campaign). Bubble filters honored.
    """
    try:
        df = load_np_data()
        ccy = request.args.get("ccy", "TRY")
        date_from = request.args.get("date_from") or None
        date_to   = request.args.get("date_to")   or None

        def _pipe_list(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        bub_ccy       = _pipe_list("filter_CCY_CODE") or [ccy]
        bub_cust_tp   = _pipe_list("filter_CUST_TP")
        bub_aum_band  = _pipe_list("filter_AUM_BAND")
        bub_segment   = _pipe_list("filter_SUB_SEGMENT")
        bub_campaign  = _pipe_list("filter_RELATED_PC")
        bub_tenor_grp = _pipe_list("filter_TENOR_GRP")

        df_f = np_apply_filters(
            df,
            ccy=bub_ccy,
            cust_tp=bub_cust_tp,
            aum_band=bub_aum_band,
            segment=bub_segment,
            campaign=bub_campaign,
            tenor_grp=bub_tenor_grp,
            date_from=date_from,
            date_to=date_to,
        )

        # Collapse the 11 fine AUM bands → 6 coarse groups for legibility
        _AUM_GROUP_MAP = {
            "0-1M":      "0-5M",   "1M-2M":     "0-5M",   "2M-5M":     "0-5M",
            "5M-10M":    "5-25M",  "10M-25M":   "5-25M",
            "25M-50M":   "25-50M",
            "50M-100M":  "50-100M",
            "100M-200M": "100-200M",
            "200M-500M": "200M+",  "500M-1B":   "200M+",  "1B+":       "200M+",
        }
        _BAND_ORDER = ["0-5M", "5-25M", "25-50M", "50-100M", "100-200M", "200M+"]

        df_g = df_f.copy()
        df_g["AUM_BAND"] = df_g["AUM_BAND"].astype(str).map(_AUM_GROUP_MAP).fillna("Bilinmiyor")

        agg = np_aggregate_distribution(df_g, group_by=["RELATED_PC", "AUM_BAND"])

        # X-axis order: campaigns descending by total volume (largest first)
        cat_vols = (
            agg.groupby("RELATED_PC", observed=True)["NP_HACIM"]
            .sum().sort_values(ascending=False)
        )
        segments = cat_vols.index.astype(str).tolist()
        present_bands = set(agg["AUM_BAND"].astype(str).unique().tolist())
        bands = [b for b in _BAND_ORDER if b in present_bands]

        # Per-band series of points {x: campaign, y: rate, z: volume}
        #   z = SUM(NP_HACIM) [TL mn] = seçilen dönem boyunca bağlanan mevduatın
        #   TOPLAM bakiyesi (TRY_BAKIYE_TOPLAM — yeni para değil). Frontend
        #   bubble alanını bu değer ile orantılı çiziyor.
        series = {}
        for band in bands:
            sub = agg[agg["AUM_BAND"].astype(str) == band]
            pts = []
            for _, r in sub.iterrows():
                cat = str(r["RELATED_PC"])
                vol = float(r["NP_HACIM"]) if pd.notna(r["NP_HACIM"]) else 0.0
                rate = float(r["NP_FAIZ"]) if pd.notna(r["NP_FAIZ"]) else None
                if rate is None or vol <= 0:
                    continue
                pts.append({"x": cat, "y": round(rate, 4), "z": round(vol, 2)})
            if pts:
                series[band] = pts

        return _json_response({
            "ok": True, "ccy": ccy,
            "date_from": date_from, "date_to": date_to,
            "segments": segments, "bands": bands, "series": series,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


@mevduat_panel_bp.route("/api/np/rate_volume_bubble", methods=["GET"])
@login_required
def api_np_rate_volume_bubble():
    """Cost-style Bubble Analysis (Balance Evolution + Interest Rate Evolution)
    driven by NEW-PRODUCTION data — for the New Business > Volume & Pricing page.

    Groups new-production at the FINEST granularity (the 5 bubble dims) over two
    windows (t0=Date Start, t1=Date End; freq D/W → 1-day or 7-day window ending on
    the date). Per product/window the interest rate uses the SAME math as the NP
    heatmap: per-row simple→COMPOUND (annualized), volume-weight, reverse to SIMPLE
    via weighted tenor (_agg_window/_finalize muadili). Builds m[PRODUCT,b0,r0,b1,r1]
    and reuses _build_bubble_charts → the exact Cost bubble contract, so the generic
    frontend bubble pipeline (filters/merge/min-size/wavg/fullscreen) works unchanged.

    Params: t0, t1 (date), freq (D|W), tenor_buckets (common 8-bucket scope, opsiyonel).
    The 5 dim filters are NOT applied server-side (client filters/merges the fine
    cells); ALL currencies returned (client defaults to TRY). Δrate(bps) on the Rate
    chart = (simple_t1 − simple_t0)·100 (SIMPLE difference), matching the heatmap.
    """
    try:
        df = load_np_data()
        t0   = request.args.get("t0") or None
        t1   = request.args.get("t1") or None
        freq = request.args.get("freq", "D").upper()
        if freq not in ("D", "W"):
            freq = "D"

        def _pl(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        # Tenor scope (heatmap ile aynı): common bucket → NP TENOR_GRP kümesi.
        tenor_buckets = _pl("tenor_buckets")
        tenor_grp = None
        if tenor_buckets is not None:
            allowed = set()
            for b in tenor_buckets:
                allowed.update(COMMON_TENOR_TO_NP_GRP.get(b, []))
            tenor_grp = sorted(allowed) if allowed else ["__no_match__"]

        # "Dimensions" toggle (Cost muadili): dims= ile aktif boyut alt-kümesi.
        # Kapatılan boyut composite'ten çıkar → bubble o boyut üzerinden toplanır
        # (daha kaba kırılım). Default = hepsi. En az bir boyut kalmalı.
        ALL_DIMS = ["CCY_CODE", "CUST_TP", "AUM_BAND", "SUB_SEGMENT", "RELATED_PC"]
        _req = request.args.get("dims")
        _req_set = set(x for x in _req.split(",") if x) if _req else None
        DIMS = [d for d in ALL_DIMS if (_req_set is None or d in _req_set)] or ALL_DIMS

        base = df
        if tenor_grp is not None:
            base = base[base["TENOR_GRP"].astype(str).isin(tenor_grp)]

        def _win(date_str):
            if not date_str:
                return None, None
            end = pd.Timestamp(date_str)
            start = end - pd.Timedelta(days=6) if freq == "W" else end
            return start, end

        def _agg(start, end):
            """Per 5-dim cell: np_vol + reverse-converted SIMPLE rate (percent)."""
            if start is None:
                return {}
            f = base[(base["DAT"] >= start) & (base["DAT"] <= end)].copy()
            f = f[f["TENOR_DAYS"] > 0]                      # compound tanımsız → dışla
            if f.empty:
                return {}
            f["_comp"] = np_simple_to_compound_pct_series(f["NP_FAIZ"], f["TENOR_DAYS"])
            f = f[f["_comp"].notna()]
            if f.empty:
                return {}
            f["_wc"] = f["_comp"]      * f["NP_HACIM"]
            f["_wt"] = f["TENOR_DAYS"] * f["NP_HACIM"]
            for d in DIMS:
                f[d] = f[d].astype(str)
            g = (f.groupby(DIMS, observed=True)
                   .agg(np_vol=("NP_HACIM", "sum"),
                        wc_sum=("_wc", "sum"),
                        wt_sum=("_wt", "sum"))
                   .reset_index())
            out = {}
            for _, r in g.iterrows():
                vol = float(r["np_vol"])
                if vol <= 0:
                    continue
                comp   = float(r["wc_sum"]) / vol
                tenor  = float(r["wt_sum"]) / vol
                simple = np_compound_to_simple_pct(comp, tenor)   # percent (reverse)
                out[tuple(str(r[d]) for d in DIMS)] = {"vol": vol, "simple": simple}
            return out

        s0, e0 = _win(t0)
        s1, e1 = _win(t1)
        w0 = _agg(s0, e0)
        w1 = _agg(s1, e1)

        # ── Outstanding (STOK) bakiye — Balance Evolution X ekseni artık new-prod
        # hacim deltası DEĞİL, outstanding bakiye deltası (OS_End − OS_Start).
        # OS verisi ayrı ve DAHA KABA kaynak: (CHANNEL=RELATED_PC, CUST_TP, ortak-AUM),
        # yalnız TRY. Her OS hücresinin bakiyesi, altındaki ince NP hücrelerine
        # NEW-PRODUCTION HACİM payına göre dağıtılır → toplanabilir kalır (client
        # gruplaması doğru toplar). Stok'u flow payıyla bölmek yaklaşıktır. CCY!=TRY
        # veya OS eşleşmesi yoksa o hücrede OS=0. Yalnız Balance X'i etkiler; bubble
        # BOYUTU (End new-hacim) ve FAİZ (NP compound→simple) DEĞİŞMEZ.
        od = load_outstanding_daily()

        def _os_win(start, end):
            # STOK snapshot (POINT-IN-TIME AS-OF): outstanding = `end`e AS-OF nokta-
            # değeri (≤ end en yakın mevcut iş günü). aggregate_outstanding (NB heatmap)
            # ve Balance Analysis (DailyBalanceEngine) ile AYNI semantik → outstanding
            # delta üç yerde de birebir uzlaşır. freq (D/W) bakiyeyi ETKİLEMEZ; yalnız
            # new-prod hacim/oran penceresel kalır.
            if end is None or od is None or od.empty:
                return {}
            avail = od["DAT"] <= end
            if not avail.any():
                return {}
            snap = od.loc[avail, "DAT"].max()
            f = od[od["DAT"] == snap]
            if f.empty:
                return {}
            s = (f.groupby(["CHANNEL", "CUST_TP", "AUM_COMMON"], observed=True)["OS_BAKIYE"]
                   .sum())                            # snapshot bakiye (TL-mn)
            return {tuple(str(x) for x in k): float(v) for k, v in s.items()}

        os0 = _os_win(s0, e0)
        os1 = _os_win(s1, e1)

        # KRİTİK: os0 ve os1 AYNI (iki-pencere birleşimi) hacim payıyla dağıtılır.
        # Aksi halde bir OS hücresinde new-üretim yalnız bir pencerede varsa, diğer
        # pencerenin outstanding bakiyesi hiçbir ince hücreye atanamaz → KAYBOLUR ve
        # delta = ±os_full çıkar (KR'de görülen sahte ~56 mia TL çöküşü). Birleşik pay
        # ile Σ(os1_alloc − os0_alloc) = os1_cell − os0_cell (gerçek hücre deltası).
        # w key = (CCY, CUST_TP, AUM_BAND, SUB_SEGMENT, RELATED_PC).
        all_keys = set(w0) | set(w1)
        fine_oc, fine_share, cell_share = {}, {}, {}
        for fk in all_keys:
            if fk[0] != "TRY":                        # OS yalnız TRY
                fine_oc[fk] = None
                fine_share[fk] = 0.0
                continue
            oc = (str(fk[4]), str(fk[1]), str(NP_AUM_TO_COMMON.get(fk[2], fk[2])))
            sh = ((w0[fk]["vol"] if fk in w0 else 0.0)
                  + (w1[fk]["vol"] if fk in w1 else 0.0))   # birleşik hacim (pay)
            fine_oc[fk] = oc
            fine_share[fk] = sh
            cell_share[oc] = cell_share.get(oc, 0.0) + sh

        def _alloc(fk, osm):
            oc = fine_oc.get(fk)
            if oc is None or oc not in osm or cell_share.get(oc, 0.0) <= 0:
                return 0.0
            return osm[oc] * (fine_share[fk] / cell_share[oc])

        rows = []
        for k in sorted(set(w0) | set(w1)):
            a, b = w0.get(k), w1.get(k)
            vol0 = a["vol"] if a else 0.0
            vol1 = b["vol"] if b else 0.0
            simp0 = a["simple"] if (a and a["simple"] is not None) else None
            simp1 = b["simple"] if (b and b["simple"] is not None) else None
            # Bir pencerede yoksa mevcut tarafın oranını taşı → pure new/dropped için
            # Δrate = 0 (X ekseninde uçmaz; kullanıcı tercihi).
            if simp0 is None:
                simp0 = simp1 if simp1 is not None else 0.0
            if simp1 is None:
                simp1 = simp0 if simp0 is not None else 0.0
            rows.append({
                "PRODUCT": "|".join(k),
                "b0": vol0 * 1e6, "b1": vol1 * 1e6,     # new-prod hacmi (size + rate ağırlığı)
                "r0": simp0 / 100.0, "r1": simp1 / 100.0,  # decimal fractions
                "os_b0": _alloc(k, os0) * 1e6,           # outstanding (Balance X); mn→TL
                "os_b1": _alloc(k, os1) * 1e6,           # her ikisi AYNI birleşik payla
                "_dims": dict(zip(DIMS, k)),
            })

        m = pd.DataFrame(rows, columns=["PRODUCT", "b0", "r0", "b1", "r1", "os_b0", "os_b1"])
        bubble_balance, bubble_rate = _build_bubble_charts(m)

        # Filtre paneli + product→dims — ham değer uzayı (NP filtre paneli = npMeta
        # .dimensions ile birebir; AUM_BAND ince etiket, common-band'e ÇEVRİLMEZ).
        bubble_product_dims = {r["PRODUCT"]: r["_dims"] for r in rows}
        bubble_filter_meta = {d: sorted({r["_dims"][d] for r in rows}) for d in DIMS}

        return _json_response({
            "ok": True,
            "bubble_balance": bubble_balance,
            "bubble_rate": bubble_rate,
            "bubble_filter_meta": bubble_filter_meta,
            "bubble_product_dims": bubble_product_dims,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 7264-7695 ──
@mevduat_panel_bp.route("/api/np/rate_volume_heatmap", methods=["GET"])
@login_required
def api_np_rate_volume_heatmap():
    """Rate × Volume heatmap: RELATED_PC (rows) × AUM_BAND (cols).

    Compares two date snapshots (T1=t0, T2=t1). Faiz hesabı BİLEŞİK (compound,
    annualized) üzerinden yapılır — ham veri basit faiz tutar, farklı vadeleri
    apples-to-apples karşılaştırmak için forward conversion uygulanır. Gösterim
    aşamasında INTEREST RATE modu için weighted tenor üzerinden tekrar basit
    faize çevrilir (reverse conversion). Per-cell metrics:
    - T1/T2 weighted compound rate (%) + reverse-converted simple rate (%)
    - T1/T2 weighted tenor (days)
    - rate delta (bps) — COMPOUND cinsinden: (t2_comp - t1_comp) * 100
    - T1/T2 outstanding balance (TL mn) — snapshot at the exact date
    - T2 new production volume over the selected window
    """
    try:
        df = load_np_data()

        t0   = request.args.get("t0") or None
        t1   = request.args.get("t1") or None
        freq = request.args.get("freq", "D").upper()
        if freq not in ("D", "W"):
            freq = "D"

        def _pl(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        ccy       = _pl("filter_CCY_CODE")
        cust_tp   = _pl("filter_CUST_TP")
        aum_band  = _pl("filter_AUM_BAND")
        segment   = _pl("filter_SUB_SEGMENT")
        campaign  = _pl("filter_RELATED_PC")
        tenor_grp = _pl("filter_TENOR_GRP")

        # ── Y EKSENİ (row dim) + X EKSENİ (col dim) — Decomp / Second Dec. Dim ───
        # Kullanıcı kararı: "Segment" = mevcut kanallar (RELATED_PC). Her boyut için:
        #   np_col : new-prod df'te gruplanacak kolon (_TENC/_aumc türetilir)
        #   os_dim : outstanding df kolonu (yoksa None → o boyutta stok boş)
        #   label  : header etiketi
        decomp  = (request.args.get("decomp")  or "SUB_SEGMENT").upper()
        decomp2 = (request.args.get("decomp2") or "AUM_BAND").upper()
        _DIM_MAP = {
            "SUB_SEGMENT": ("RELATED_PC", "CHANNEL",      "Segment"),
            "RELATED_PC":  ("RELATED_PC", "CHANNEL",      "Campaign"),
            "AUM_BAND":    ("_aumc",      "AUM_COMMON",   "AUM Band"),
            "CUST_TP":     ("CUST_TP",    "CUST_TP",      "Customer Type"),
            "TENOR_GRP":   ("_TENC",      "TENOR_COMMON", "Tenor"),
        }
        # Satır = kolon dejenere olur → kolonu alternatife düşür (frontend mutex'i
        # zaten engelliyor; API'yi doğrudan çağıranlara karşı guard).
        if decomp2 == decomp:
            decomp2 = "AUM_BAND" if decomp != "AUM_BAND" else "SUB_SEGMENT"
        np_row_col, os_row_dim, row_label = _DIM_MAP.get(
            decomp, ("RELATED_PC", "CHANNEL", "Segment"))
        np_col_col, os_col_dim, col_label = _DIM_MAP.get(
            decomp2, ("_aumc", "AUM_COMMON", "AUM Band"))

        # Vade (tenor) — ortak 8-bucket. new-prod tarafı TENOR_GRP'e, outstanding
        # tarafı VADE_BUCKET'e (TENOR_COMMON) map'lenir. Param yoksa = hepsi seçili
        # = vade kısıtı yok. Param varsa seçilen kovalara filtrelenir; section
        # filter_TENOR_GRP ile birlikte gelirse new-prod tarafında kesişim alınır.
        tenor_buckets = _pl("tenor_buckets")
        os_tenor_commons = None       # outstanding tenor filtresi (ortak bucket'lar)
        if tenor_buckets is not None:
            allowed_tg = set()
            for b in tenor_buckets:
                allowed_tg.update(COMMON_TENOR_TO_NP_GRP.get(b, []))
            if not allowed_tg:
                allowed_tg = {"__no_match__"}      # hiçbiri seçili → boş sonuç
            if tenor_grp:
                tenor_grp = [t for t in tenor_grp if t in allowed_tg] or ["__no_match__"]
            else:
                tenor_grp = sorted(allowed_tg)
            os_tenor_commons = list(tenor_buckets) or ["__no_match__"]

        # Tenor bir EKSEN ise (Y veya X): VADE (DTM) filtresi o eksenin kendisi →
        # kısıtlama; TÜM gruplanmış tenor'ları eksende göster (sayıya göre sıralı).
        if decomp == "TENOR_GRP" or decomp2 == "TENOR_GRP":
            tenor_grp = None
            os_tenor_commons = None

        # AUM merge — frontend fine-band grupları ORTAK band düzeyine çevrilir
        # (heatmap kolonları artık ortak 8-band). Format: "name:m1,m2|name2:m3,m4".
        common_aum_remap = {}         # common band -> group adı
        group_common_members = {}     # group adı -> set(common bands)
        aum_merge_raw = request.args.get("aum_merge")
        if aum_merge_raw:
            for grp in aum_merge_raw.split("|"):
                if ":" not in grp:
                    continue
                gname, mem = grp.split(":", 1)
                members = [m for m in mem.split(",") if m]
                if not members:
                    continue
                cms = {NP_AUM_TO_COMMON.get(m, m) for m in members}
                group_common_members[gname] = cms
                for cm in cms:
                    common_aum_remap[cm] = gname

        def _disp_aum(common_band):
            return common_aum_remap.get(common_band, common_band)

        # Generic satır/kolon merge (RELATED_PC / CUST_TP) — heatmap eksen-başlığı
        # seçiminden gelir (merges= JSON). Değerleri grup adına relabel eder;
        # AUM zaten aum_merge ile, tenor gruplanmaz. Row/col hangi filtre dim'ine
        # denk geliyorsa (Segment→RELATED_PC) o merge grupları uygulanır.
        _AXIS_MERGE_DIM = {"SUB_SEGMENT": "RELATED_PC", "RELATED_PC": "RELATED_PC",
                           "CUST_TP": "CUST_TP"}
        _np_merges = {}
        _mraw = request.args.get("merges")
        if _mraw:
            try:
                _md = json.loads(_mraw)
                if isinstance(_md, dict):
                    for _dim, _grps in _md.items():
                        if _dim in ("RELATED_PC", "CUST_TP") and isinstance(_grps, list):
                            _np_merges[_dim] = _grps
            except Exception:
                _np_merges = {}

        def _build_remap(dim_key):
            out = {}
            for g in (_np_merges.get(dim_key) or []):
                nm = str(g.get("name", "")).strip()
                for m in (g.get("members") or []):
                    if nm:
                        out[str(m)] = nm
            return out

        row_remap = _build_remap(_AXIS_MERGE_DIM.get(decomp))
        col_remap = _build_remap(_AXIS_MERGE_DIM.get(decomp2))

        # #9 — sessiz drop'ları yüzeye çıkar (warning'ler payload'da gösterilir):
        #  • Tenor filtresi aktifken haritada olmayan TENOR_GRP (örn. 99_DIGER)
        #    o filtreyle dışlanır — hangileri olduğunu bildir.
        #  • Kısmi fine-AUM seçimi: bir ortak bandın fine üyelerinin sadece bir
        #    kısmı seçilirse new-prod o alt-bandı, outstanding ise TÜM ortak bandı
        #    gösterir (outstanding ortak banddan ince çözemez) — kullanıcı bilsin.
        dq_warnings = []
        if tenor_buckets is not None:
            _unmapped = sorted(g for g in df["TENOR_GRP"].astype(str).unique()
                               if g not in NP_TENOR_TO_COMMON)
            if _unmapped:
                dq_warnings.append(
                    "Tenor filter active: unmapped tenor(s) excluded: "
                    + ", ".join(_unmapped))
        if aum_band:
            _sel = set(aum_band)
            _partial = [c for c, members in COMMON_AUM_TO_NP_BANDS.items()
                        if (_sel & set(members)) and not set(members) <= _sel]
            if _partial:
                dq_warnings.append(
                    "Partial AUM selection (" + ", ".join(sorted(_partial)) + "): "
                    "outstanding shows the full common band in these column(s) "
                    "(stock cannot be split finer).")

        def _window_bounds(date_str):
            if not date_str:
                return None, None
            end = pd.Timestamp(date_str)
            start = end - pd.Timedelta(days=6) if freq == "W" else end
            return start, end

        t0_start, t0_end = _window_bounds(t0)
        t1_start, t1_end = _window_bounds(t1)

        def _base_filter():
            return np_apply_filters(
                df, ccy=ccy, cust_tp=cust_tp, aum_band=aum_band,
                segment=segment, campaign=campaign, tenor_grp=tenor_grp,
            )

        # Veri kalitesi izleme: bileşik faiz > %100 ise loglanır (uç vade/oran).
        _dq_warn = {"n": 0, "max": 0.0}

        def _agg_window(start, end):
            """Forward-convert per row → volume-weighted compound rate + tenor.

            Returns per-cell dict carrying RAW weighted sums (wc_sum, wt_sum,
            np_vol) so totals can sum them directly = raw-level volume-weighting.
            """
            f = _base_filter()
            if start is not None:
                f = f[(f["DAT"] >= start) & (f["DAT"] <= end)]
            if f.empty:
                return {}
            f = f.copy()
            # Edge case: tenor ≤ 0 satırları exclude (compound tanımsız).
            f = f[f["TENOR_DAYS"] > 0]
            if f.empty:
                return {}
            # AUM_BAND → ortak 8-band → display (merge varsa grup adı).
            # _require_mapped: bilinmeyen banda raise (sessiz spurious-kolon yok, #9).
            f["_aumc"] = (os_require_mapped(f["AUM_BAND"].astype(str),
                                            NP_AUM_TO_COMMON, "AUM_BAND")
                          .map(_disp_aum))
            # Eksen boyutları (Y=decomp, X=decomp2). TENOR için TENOR_GRP'ten ortak
            # bucket türetilir; haritalanmayan (99_DIGER vb.) satır dışlanır (#9).
            if np_row_col == "_TENC" or np_col_col == "_TENC":
                f["_TENC"] = f["TENOR_GRP"].astype(str).map(NP_TENOR_TO_COMMON)
                f = f[f["_TENC"].notna()]
                if f.empty:
                    return {}
            _rowcol = np_row_col if np_row_col in f.columns else "RELATED_PC"
            _colcol = np_col_col if np_col_col in f.columns else "_aumc"
            # Generic satır/kolon merge (RELATED_PC/CUST_TP) — değerleri grup adına
            # relabel et; toplama additive olduğundan wc_sum/wt_sum/vol grup içinde
            # doğru birleşir (weighted rate = Σwc/Σvol korunur).
            if row_remap:
                f[_rowcol] = f[_rowcol].astype(str).map(lambda v: row_remap.get(v, v))
            if col_remap:
                f[_colcol] = f[_colcol].astype(str).map(lambda v: col_remap.get(v, v))
            # Step 1 — forward conversion (per raw row, BEFORE grouping).
            f["_comp"] = np_simple_to_compound_pct_series(f["NP_FAIZ"], f["TENOR_DAYS"])
            # Edge case: null compound satırları exclude.
            f = f[f["_comp"].notna()]
            if f.empty:
                return {}
            # DQ monitor
            mx = float(f["_comp"].max())
            if mx > 100.0:
                _dq_warn["n"] += int((f["_comp"] > 100.0).sum())
                _dq_warn["max"] = max(_dq_warn["max"], mx)
            # Volume-weighted sums
            f["_wc"] = f["_comp"]       * f["NP_HACIM"]
            f["_wt"] = f["TENOR_DAYS"]  * f["NP_HACIM"]
            g = (
                f.groupby([_rowcol, _colcol], observed=True)
                .agg(np_vol=("NP_HACIM", "sum"),
                     wc_sum=("_wc", "sum"),
                     wt_sum=("_wt", "sum"))
                .reset_index()
            )
            out = {}
            for _, r in g.iterrows():
                k = "{}|{}".format(r[_rowcol], r[_colcol])
                out[k] = {
                    "np_vol": float(r["np_vol"]),
                    "wc_sum": float(r["wc_sum"]),
                    "wt_sum": float(r["wt_sum"]),
                }
            return out

        win_t0 = _agg_window(t0_start, t0_end)
        win_t1 = _agg_window(t1_start, t1_end)

        if _dq_warn["n"] > 0:
            log.warning(
                "np_rate_volume_heatmap: %d satırda compound rate > %%100 "
                "(max %.1f%%). Vade/oran uç değer kontrolü gerekebilir.",
                _dq_warn["n"], _dq_warn["max"],
            )

        # ── OUTSTANDING (gerçek stok) — daily_deposit ← DEPOSITUSAGE_NEW ──────────
        # Kanal = RELATED_PC == SEGMENT; AUM ortak banda map'li; tenor ortak bucket
        # (prod-only). CCY: outstanding sadece TRY — ccy filtresi TRY'yi dışlıyorsa
        # outstanding boş. SUB_SEGMENT outstanding kaynağında YOK → o filtre
        # outstanding'e uygulanmaz (yalnız new-prod tarafını daraltır).
        os_t0, os_t1 = {}, {}
        ccy_ok = (not ccy) or ("TRY" in ccy)
        if ccy_ok:
            od = load_outstanding_daily()
            os_aum_commons = (sorted({NP_AUM_TO_COMMON.get(b, b) for b in aum_band})
                              if aum_band else None)
            _os_kw = dict(channels=campaign, cust_tp=cust_tp,
                          aum_commons=os_aum_commons, tenor_commons=os_tenor_commons,
                          aum_remap=common_aum_remap, row_dim=os_row_dim,
                          col_dim=os_col_dim, row_remap=row_remap, col_remap=col_remap)
            # Her snapshot bağımsız — biri verilmese de diğeri hesaplanır.
            if t0_start is not None:
                os_t0 = aggregate_outstanding(od, start=t0_start, end=t0_end, freq=freq, **_os_kw)
            if t1_start is not None:
                os_t1 = aggregate_outstanding(od, start=t1_start, end=t1_end, freq=freq, **_os_kw)

        def _finalize(agg):
            """Raw weighted sums → compound rate, weighted tenor, display simple."""
            vol = agg.get("np_vol", 0.0)
            if vol <= 0:
                return None, None, None
            comp = agg["wc_sum"] / vol           # weighted compound (percent)
            tenor = agg["wt_sum"] / vol          # weighted tenor (days)
            simple = np_compound_to_simple_pct(comp, tenor)  # reverse → display
            return (round(comp, 4),
                    round(tenor, 1),
                    round(simple, 4) if simple is not None else None)

        _PC_ORDER  = ["TC", "SB", "MI", "KR", "FB", "BR"]
        _CUST_DISPLAY = {"G": "Individual", "T": "Corporate"}

        # Satırlar/kolonlar = new-prod + outstanding eksen-değerlerinin BİRLEŞİMİ.
        all_pcs, all_aucs = set(), set()
        for k in (list(win_t0) + list(win_t1) + list(os_t0) + list(os_t1)):
            pc, auc = k.split("|", 1)
            all_pcs.add(pc); all_aucs.add(auc)

        # AUM sıralaması: ortak band sırası; merge grupları üyelerinin min index'i.
        def _aum_sort_key(col):
            if col in group_common_members:
                idxs = [COMMON_AUM_ORDER.index(m) for m in group_common_members[col]
                        if m in COMMON_AUM_ORDER]
                return min(idxs) if idxs else 999
            return COMMON_AUM_ORDER.index(col) if col in COMMON_AUM_ORDER else 999

        # Eksen sıralaması — boyuta göre (Y ve X aynı kuralları paylaşır):
        #   Kanal (RELATED_PC/CHANNEL): _PC_ORDER, sonra alfabetik.
        #   Tenor: ortak bucket doğal (sayıya göre) sırası (COMMON_TENOR_ORDER).
        #   AUM: ortak band sırası (merge grupları dahil).
        #   Customer Type / diğer: hacme göre azalan (t1 new-prod + outstanding).
        def _axis_order(vals, np_dim, os_dim, is_row):
            if os_dim == "TENOR_COMMON":
                return ([t for t in COMMON_TENOR_ORDER if t in vals]
                        + sorted(vals - set(COMMON_TENOR_ORDER)))
            if np_dim == "_aumc":
                return sorted(vals, key=_aum_sort_key)
            if np_dim == "RELATED_PC":
                return [p for p in _PC_ORDER if p in vals] + sorted(vals - set(_PC_ORDER))
            # Hacme göre azalan (t1 new-prod + outstanding bakiyesi yaklaşık ağırlık).
            def _axisvol(v):
                tot = 0.0
                for k in set(win_t1) | set(os_t1):
                    pc, auc = k.split("|", 1)
                    if (pc if is_row else auc) != v:
                        continue
                    if k in win_t1: tot += win_t1[k].get("np_vol", 0.0)
                    if k in os_t1:  tot += os_t1[k].get("os_bakiye", 0.0)
                return tot
            return sorted(vals, key=lambda v: (-_axisvol(v), v))

        rows = _axis_order(all_pcs, np_row_col, os_row_dim, True)
        cols = _axis_order(all_aucs, np_col_col, os_col_dim, False)
        # Customer Type görünüm etiketi (G/T → Gerçek/Tüzel); diğer boyutlar ham.
        row_display = (_CUST_DISPLAY if os_row_dim == "CUST_TP" else {})
        col_display = (_CUST_DISPLAY if os_col_dim == "CUST_TP" else {})

        def _build_payload(np0, np1, os0, os1):
            comp0, ten0, simp0 = _finalize(np0) if np0 else (None, None, None)
            comp1, ten1, simp1 = _finalize(np1) if np1 else (None, None, None)
            # Hücre-içi oran hesabı COMPOUND (volume-weighted + weighted tenor);
            # ama DELTA, weighted tenor ile reverse-convert edilmiş SIMPLE oranlar
            # üzerinden hesaplanır (kullanıcı tercihi). bps = (simp1-simp0)*100.
            delta = (round((simp1 - simp0) * 100, 2)
                     if (simp0 is not None and simp1 is not None) else None)
            os0v = os0["os_bakiye"] if os0 else None
            os1v = os1["os_bakiye"] if os1 else None
            return {
                "t0_compound":    comp0,
                "t1_compound":    comp1,
                "t0_simple":      simp0,
                "t1_simple":      simp1,
                "t0_tenor":       ten0,
                "t1_tenor":       ten1,
                "rate_delta_bps": delta,
                "t0_os":          os0v,
                "t1_os":          os1v,
                "t0_os_rate":     os0["os_faiz"] if os0 else None,
                "t1_os_rate":     os1["os_faiz"] if os1 else None,
                "bal_delta":      round((os1v or 0) - (os0v or 0), 2),
                "t0_np_vol":      round(np0["np_vol"], 2) if np0 else None,
                "t1_np_vol":      round(np1["np_vol"], 2) if np1 else None,
            }

        # Per-cell payload: new-prod (oran/hacim) + outstanding (bakiye) birleşik.
        cells = {}
        for k in set(win_t0) | set(win_t1) | set(os_t0) | set(os_t1):
            cells[k] = _build_payload(
                win_t0.get(k), win_t1.get(k), os_t0.get(k), os_t1.get(k))

        def _osfin(acc, has):
            if not has:
                return None
            return {
                "os_bakiye": round(acc["os_bakiye"], 2),
                "os_faiz":   round(acc["wr_sum"] / acc["bal_sum"], 4) if acc["bal_sum"] else None,
            }

        def _agg_total(pc_list, auc_list):
            """Total: new-prod raw-level volume-weighted; outstanding bakiye toplam
            (aynı pencere avg-daily-balance additive), oranı balance-weighted."""
            n0 = {"np_vol": 0.0, "wc_sum": 0.0, "wt_sum": 0.0}
            n1 = {"np_vol": 0.0, "wc_sum": 0.0, "wt_sum": 0.0}
            o0 = {"os_bakiye": 0.0, "bal_sum": 0.0, "wr_sum": 0.0}
            o1 = {"os_bakiye": 0.0, "bal_sum": 0.0, "wr_sum": 0.0}
            hn0 = hn1 = ho0 = ho1 = False
            for pc in pc_list:
                for auc in auc_list:
                    k = "{}|{}".format(pc, auc)
                    a, b = win_t0.get(k), win_t1.get(k)
                    c, d2 = os_t0.get(k), os_t1.get(k)
                    if a:
                        hn0 = True
                        for kk in n0: n0[kk] += a[kk]
                    if b:
                        hn1 = True
                        for kk in n1: n1[kk] += b[kk]
                    if c:
                        ho0 = True
                        for kk in o0: o0[kk] += c[kk]
                    if d2:
                        ho1 = True
                        for kk in o1: o1[kk] += d2[kk]
            return _build_payload(
                n0 if hn0 else None, n1 if hn1 else None,
                _osfin(o0, ho0), _osfin(o1, ho1))

        row_totals  = {pc:  _agg_total([pc], cols) for pc in rows}
        col_totals  = {auc: _agg_total(rows, [auc]) for auc in cols}
        grand_total = _agg_total(rows, cols)

        # Bu Y boyutunda outstanding stok kırılımı yoksa (ör. dev tenor / kaynakta
        # dim yok) kullanıcıyı bilgilendir — renk/OS Bakiye yalnız new-prod'u yansıtır.
        if ccy_ok and not os_t0 and not os_t1 and (win_t0 or win_t1) and decomp != "SUB_SEGMENT":
            dq_warnings.append(
                "No outstanding stock breakdown on the '{}' Y-axis dimension → cell color/OS "
                "Balance are shown from new-prod in this view.".format(row_label))

        return _json_response({
            "ok": True, "freq": freq, "t0": t0, "t1": t1,
            "rate_basis": "compound", "os_source": "daily_deposit",
            "decomp": decomp, "row_label": row_label, "row_display": row_display,
            "decomp2": decomp2, "col_label": col_label, "col_display": col_display,
            "rows": rows, "cols": cols,
            "cells": cells,
            "row_totals": row_totals,
            "col_totals": col_totals,
            "grand_total": grand_total,
            "dq_warnings": dq_warnings,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 7696-7830 ──
# cell_timeseries memo: hover başına full-df copy + groupby pahalı; heatmap
# üstünde gezerken her yerleşen hover bir istek üretir. Çıktı, aynı df ve aynı
# querystring için deterministiktir → LRU memoize. df kimliği (load_np_data
# objesi) değişince (refresh) memo temizlenir; eski keyler doğal olarak ıskalar.
from collections import OrderedDict as _OrderedDict

_TS_MEMO_LOCK = _threading.Lock()
_TS_MEMO_DF = None
_TS_MEMO: "_OrderedDict[str, list]" = _OrderedDict()
_TS_MEMO_MAX = 256


def _ts_memo_get(df, qs):
    global _TS_MEMO_DF
    with _TS_MEMO_LOCK:
        if _TS_MEMO_DF is not df:
            _TS_MEMO.clear()
            _TS_MEMO_DF = df
            return None
        rec = _TS_MEMO.get(qs)
        if rec is not None:
            _TS_MEMO.move_to_end(qs)
        return rec


def _ts_memo_put(df, qs, records):
    with _TS_MEMO_LOCK:
        if _TS_MEMO_DF is not df:
            return
        _TS_MEMO[qs] = records
        _TS_MEMO.move_to_end(qs)
        while len(_TS_MEMO) > _TS_MEMO_MAX:
            _TS_MEMO.popitem(last=False)


@mevduat_panel_bp.route("/api/np/cell_timeseries", methods=["GET"])
@login_required
def api_np_cell_timeseries():
    """Heatmap hücresine (kanal × AUM) çift-tık → new-production zaman serisi.

    Seçilen aralık [t0, t1] ve frekansta (D/W), o hücre için:
      - new-production weighted-avg faiz oranı (line, %)
      - new-production bağlanan hacim (bar, TL-mn)
    döner. AUM column'u ortak band ya da merge grup adı olabilir → new-prod fine
    bandlarına çözülür. Tüm section filtreleri (ccy/cust_tp/segment/tenor) uygulanır.
    """
    try:
        df = load_np_data()
        _qs = request.query_string.decode("utf-8", "ignore")
        _cached = _ts_memo_get(df, _qs)
        if _cached is not None:
            return _json_response({
                "ok": True,
                "channel": request.args.get("channel") or None,
                "aum": request.args.get("aum") or None,
                "freq": (request.args.get("freq", "W").upper() if
                         request.args.get("freq", "W").upper() in ("D", "W") else "W"),
                "t0": request.args.get("t0") or None,
                "t1": request.args.get("t1") or None,
                "records": _cached,
            })
        channel = request.args.get("channel") or None
        aum     = request.args.get("aum") or None
        if channel == "__ALL__": channel = None   # total satır/sütun → o boyutta filtre yok
        if aum == "__ALL__":     aum = None
        t0      = request.args.get("t0") or None
        t1      = request.args.get("t1") or None
        freq    = request.args.get("freq", "W").upper()
        if freq not in ("D", "W"):
            freq = "W"

        def _pl(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        cust_tp   = _pl("filter_CUST_TP")
        ccy       = _pl("filter_CCY_CODE")
        segment   = _pl("filter_SUB_SEGMENT")
        tenor_grp = _pl("filter_TENOR_GRP")
        aum_flt   = _pl("filter_AUM_BAND")
        tenor_buckets = _pl("tenor_buckets")
        if tenor_buckets is not None:
            allowed_tg = set()
            for b in tenor_buckets:
                allowed_tg.update(COMMON_TENOR_TO_NP_GRP.get(b, []))
            if not allowed_tg:
                allowed_tg = {"__no_match__"}
            tenor_grp = ([t for t in tenor_grp if t in allowed_tg] or ["__no_match__"]
                         if tenor_grp else sorted(allowed_tg))

        _dec  = (request.args.get("decomp")  or "SUB_SEGMENT").upper()
        _dec2 = (request.args.get("decomp2") or "AUM_BAND").upper()

        # AUM kolon etiketi → ortak band(lar) → new-prod fine bandlar (yalnız X=AUM).
        # Merge grup adıysa üyelerini (aum_merge param'ından) çöz.
        common_set = set()
        aum_merge_raw = request.args.get("aum_merge")
        group_common_members = {}
        if aum_merge_raw:
            for grp in aum_merge_raw.split("|"):
                if ":" not in grp:
                    continue
                gname, mem = grp.split(":", 1)
                members = [m for m in mem.split(",") if m]
                if members:
                    group_common_members[gname] = {NP_AUM_TO_COMMON.get(m, m) for m in members}
        def _common_to_fine(label):
            # Ortak band / merge grup etiketi → new-prod fine band listesi.
            commons = group_common_members.get(label, {label})
            return sorted({fb for c in commons
                           for fb in COMMON_AUM_TO_NP_BANDS.get(c, [c])})

        # AUM hangi eksendeyse tıklanan değer AUM kısıtına çevrilir.
        fine_bands = []
        if _dec2 == "AUM_BAND" and aum:
            fine_bands = _common_to_fine(aum)
        elif _dec == "AUM_BAND" and channel:
            fine_bands = _common_to_fine(channel)

        # Satır (channel) değeri decomp'a, kolon (aum) değeri decomp2'ye göre
        # yönlendirilir: CUST_TP → cust_tp; TENOR → tenor_grp; SEGMENT → kanal;
        # AUM → fine_bands (yukarıda).
        _camp, _cust, _teng = None, cust_tp, tenor_grp
        if _dec == "TENOR_GRP" or _dec2 == "TENOR_GRP":
            # Heatmap tenor eksende VADE (DTM) filtresini yok sayar (eksen tenor'un
            # kendisi) — drill de aynı davranmalı, yoksa tıklanan tenor satırı VADE
            # seçimiyle çelişip boş sonuç döner. Tenor'u tıklanan hücre belirler.
            _teng = None
        if channel:
            if _dec == "CUST_TP":
                _cust = [channel]
            elif _dec == "TENOR_GRP":
                _teng = COMMON_TENOR_TO_NP_GRP.get(channel, [channel])
            elif _dec != "AUM_BAND":
                _camp = [channel]
        if aum and _dec2 != "AUM_BAND":
            if _dec2 == "CUST_TP":
                _cust = [aum]
            elif _dec2 == "TENOR_GRP":
                _teng = COMMON_TENOR_TO_NP_GRP.get(aum, [aum])
            else:
                _camp = [aum]

        # AUM kısıtı: eksen değeri (fine bandlar) > section filtresi (heatmap ile tutarlı).
        np_aum_bands = (fine_bands or aum_flt) or None

        # Filtre + zaman serisi (group_by yok → tek seri).
        df_f = np_apply_filters(
            df,
            ccy=ccy, cust_tp=_cust, segment=segment,
            campaign=_camp,
            aum_band=np_aum_bands,
            tenor_grp=_teng,
            date_from=t0, date_to=t1,
        )
        # Haftalık binler Date(End)=t1'de biten rolling 7-günlük pencereler olsun →
        # son iki nokta heatmap'in T1/T2 pencereleriyle (rate_delta_bps'in tanımı)
        # birebir hizalanır; partial son-hafta çıkmaz.
        ts = np_aggregate_timeseries(df_f, group_by=[], freq=freq, week_anchor=t1)
        records = [
            {"date": str(r.DATE.date()),
             "rate": None if pd.isna(r.NP_FAIZ) else round(float(r.NP_FAIZ), 4),
             "balance": round(float(r.NP_HACIM), 2)}
            for r in ts.itertuples(index=False)
        ]
        _ts_memo_put(df, _qs, records)
        return _json_response({
            "ok": True, "channel": channel, "aum": aum, "freq": freq,
            "t0": t0, "t1": t1, "records": records,
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


import threading as _threading

# ── Müşteri-seviyesi new-production DETAY — İKİ MOD ───────────────────────────
# PRELOAD modu (açılışta kullanıcı 'evet' derse): tüm tarih aralığı TEK SEFER
#   RAM'e alınır (load_np_detail_master) → drill herhangi bir pencerede ANINDA
#   (in-memory filtre). Açılış yavaşlar.
# LAZY modu (default / 'hayır'): master yüklenmez; load_np_detail her pencereyi
#   ON-DEMAND SQL'ler ve küçük LRU memoize'de tutar (ESKİ davranış). Açılış hızlı;
#   frontend prefetch mevcut pencereyi arka planda warm ettiğinden çift-tık yine
#   çoğu zaman hazır gelir.
_NP_DETAIL_MASTER      = None

# ── app.py 7831-7896 ──
_NP_DETAIL_MASTER_LOCK = _threading.Lock()
_NP_DETAIL_CACHE       = {}                 # LAZY mod: {(start,end): df}
_NP_DETAIL_LOCK        = _threading.Lock()
_NP_DETAIL_MAX         = 12                  # bounded LRU (FIFO)


def _np_detail_enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Ham detay df'ine ortak band/tenor + kampanya + gün-bazlı VAL_DATE kolonları."""
    df["VAL_DT"]       = pd.to_datetime(df["VAL_DT"])
    df["MTRTY_DT"]     = pd.to_datetime(df["MTRTY_DT"], errors="coerce")
    df["_VAL_DATE"]    = df["VAL_DT"].dt.normalize()   # pencere filtresi (gün bazlı)
    df["AUM_BAND"]     = df["AUM_LOWER"].map(NP_AUM_LABELS).fillna("Bilinmiyor")
    df["AUM_COMMON"]   = df["AUM_BAND"].map(lambda b: NP_AUM_TO_COMMON.get(b, b))
    df["TENOR_COMMON"] = df["VADE_BUCKET"].map(lambda v: NP_TENOR_TO_COMMON.get(v))
    _kmp = df["KAMPANYA_ADI"].astype(str)
    df["_HAS_KMP"]     = df["KAMPANYA_ADI"].notna() & _kmp.ne("") & _kmp.ne("Kampanya Yok")
    return df


def load_np_detail_master() -> pd.DataFrame:
    """Full-range row-level detay — tek sefer yüklenir, RAM'de tutulur (thread-safe).
    PRELOAD modunda açılışta çağrılır; LAZY modda çağrılmaz."""
    global _NP_DETAIL_MASTER
    if _NP_DETAIL_MASTER is not None:
        return _NP_DETAIL_MASTER
    with _NP_DETAIL_MASTER_LOCK:
        if _NP_DETAIL_MASTER is not None:
            return _NP_DETAIL_MASTER
        nd = load_np_data()
        start = nd["DAT"].min().date().isoformat()
        end   = nd["DAT"].max().date().isoformat()
        from .data_source import load_dataframe  # port: db_source yerine
        df = load_dataframe("new_production_detail",
                            params={"DATE_START": start, "DATE_END": end}).copy()
        _NP_DETAIL_MASTER = _np_detail_enrich(df)
        return _NP_DETAIL_MASTER


def load_np_detail(start: str, end: str) -> pd.DataFrame:
    """[start,end] penceresi.
    - Master (preload) yüklüyse → in-memory gün filtresi (SQL YOK, anında).
    - Değilse → pencere-bazlı SQL + LRU memoize (eski davranış); frontend prefetch
      ile aynı pencere önceden warm edilmişse cache'ten döner.
    Gün-bazlı, uçlar dahil (VAL_DATE ∈ [start, end])."""
    if _NP_DETAIL_MASTER is not None:
        m = _NP_DETAIL_MASTER
        s = pd.Timestamp(start).normalize(); e = pd.Timestamp(end).normalize()
        return m[(m["_VAL_DATE"] >= s) & (m["_VAL_DATE"] <= e)]
    key = (start, end)
    cached = _NP_DETAIL_CACHE.get(key)
    if cached is not None:
        return cached
    with _NP_DETAIL_LOCK:
        cached = _NP_DETAIL_CACHE.get(key)   # double-check
        if cached is not None:
            return cached
        from .data_source import load_dataframe  # port: db_source yerine
        df = load_dataframe("new_production_detail",
                            params={"DATE_START": start, "DATE_END": end}).copy()
        df = _np_detail_enrich(df)
        if len(_NP_DETAIL_CACHE) >= _NP_DETAIL_MAX:
            _NP_DETAIL_CACHE.pop(next(iter(_NP_DETAIL_CACHE)))
        _NP_DETAIL_CACHE[key] = df
        return df



# ── app.py 7897-7912 ──
@mevduat_panel_bp.route("/api/np/detail_prewarm", methods=["GET"])
@login_required
def api_np_detail_prewarm():
    """Heatmap açılınca frontend fire-and-forget çağırır: o [t0,t1] penceresinin
    müşteri-detayını arka planda cache'e alır → kullanıcı çift-tıkladığında hazır
    (bekleme yok). Küçük payload döner."""
    try:
        t0 = request.args.get("t0"); t1 = request.args.get("t1")
        if not (t0 and t1):
            return _json_response({"ok": False, "error": "t0/t1 are required."}, status=400)
        start, end = sorted([t0, t1])
        df = load_np_detail(start, end)
        return _json_response({"ok": True, "rows": int(len(df)), "t0": start, "t1": end})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 7913-8137 ──
@mevduat_panel_bp.route("/api/np/cell_drilldown", methods=["GET"])
@login_required
def api_np_cell_drilldown():
    """Heatmap hücresine çift-tık → o hücrede BAĞLANAN mevduatların müşteri-
    seviyesi dökümü + fiyat (faiz) histogramı + DTM histogramı + özet KPI'lar.

    Kaynak: CACHE'li load_np_detail() (açılışta yüklenir; SQL YOK). Hücre = kanal
    (RELATED_PC) × AUM (ortak band/merge grup) × tenor + diğer section filtreleri.
    Kanal 'Bilinmiyor' ise (dev / kanalsız) kanal filtresi uygulanmaz.
    """
    try:
        channel = request.args.get("channel") or None
        aum     = request.args.get("aum") or None
        if channel == "__ALL__": channel = None   # total → o boyutta filtre yok
        if aum == "__ALL__":     aum = None
        t0      = request.args.get("t0") or None
        t1      = request.args.get("t1") or None
        if not (t0 and t1):
            return _json_response({"ok": False, "error": "t0/t1 are required."}, status=400)
        start, end = sorted([t0, t1])

        def _pl(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        cust_tp = _pl("filter_CUST_TP")
        ccy     = _pl("filter_CCY_CODE")
        segment = _pl("filter_SUB_SEGMENT")
        tenor_buckets = _pl("tenor_buckets")

        _empty = {"ok": True, "channel": channel, "aum": aum,
                  "deposits": [], "rate_histogram": [], "dtm_histogram": [],
                  "segments": [], "kpis": {}, "row_count": 0}
        df = load_np_detail(start, end)   # pencere-bazlı memoize (frontend prefetch ile hazır)
        if df.empty:
            return _json_response(_empty)

        # AUM kolon etiketi (ortak band / merge grup) → ortak band seti (yalnız X=AUM).
        group_common_members = {}
        aum_merge_raw = request.args.get("aum_merge")
        if aum_merge_raw:
            for grp in aum_merge_raw.split("|"):
                if ":" not in grp:
                    continue
                gname, mem = grp.split(":", 1)
                members = [m for m in mem.split(",") if m]
                if members:
                    group_common_members[gname] = {NP_AUM_TO_COMMON.get(m, m) for m in members}

        # Satır (channel) decomp'a, kolon (aum) decomp2'ye göre yönlendirilir.
        _dec  = (request.args.get("decomp")  or "SUB_SEGMENT").upper()
        _dec2 = (request.args.get("decomp2") or "AUM_BAND").upper()
        aum_commons = (group_common_members.get(aum, {aum} if aum else set())
                       if _dec2 == "AUM_BAND" else set())
        # X≠AUM ise section AUM filtresi (fine bandlar → ortak band) uygulanır.
        aum_flt = _pl("filter_AUM_BAND")
        if _dec2 != "AUM_BAND" and aum_flt:
            aum_commons = {NP_AUM_TO_COMMON.get(b, b) for b in aum_flt}

        def _channelish_mask(val):
            # Kanal (RELATED_PC): 'Bilinmiyor' = kanalsız (dev) → filtre uygulama.
            if val != "Bilinmiyor" and df["RELATED_PC_CODE"].astype(str).ne("Bilinmiyor").any():
                return df["RELATED_PC_CODE"].astype(str) == val
            return None

        # VAL_DT penceresi + boyut filtreleri (hepsi pandas — SQL yok).
        mask = (df["VAL_DT"] >= pd.Timestamp(start)) & (df["VAL_DT"] <= pd.Timestamp(end))
        if aum_commons:
            mask &= df["AUM_COMMON"].isin(aum_commons)
        # Tenor bir EKSEN ise heatmap VADE filtresini yok sayar — drill de yok
        # saymalı; aksi halde tıklanan tenor hücresi VADE seçimiyle kesişmeyip boş
        # drill döner (SEGMENT/CUST_TP'de sorun olmamasının nedeni bu).
        if tenor_buckets and _dec != "TENOR_GRP" and _dec2 != "TENOR_GRP":
            mask &= df["TENOR_COMMON"].isin(tenor_buckets)
        if cust_tp:
            mask &= df["CUST_TP"].isin(cust_tp)
        if ccy:
            mask &= df["CCY_CODE"].isin(ccy)
        if segment:
            mask &= df["SUB_SEGMENT"].isin(segment)
        if channel:
            if _dec == "CUST_TP":
                mask &= df["CUST_TP"].astype(str) == channel
            elif _dec == "TENOR_GRP":
                mask &= df["TENOR_COMMON"].astype(str) == channel
            elif _dec == "AUM_BAND":
                mask &= df["AUM_COMMON"].astype(str) == channel
            else:
                _cm = _channelish_mask(channel)
                if _cm is not None:
                    mask &= _cm
        if aum and _dec2 != "AUM_BAND":
            if _dec2 == "CUST_TP":
                mask &= df["CUST_TP"].astype(str) == aum
            elif _dec2 == "TENOR_GRP":
                mask &= df["TENOR_COMMON"].astype(str) == aum
            else:
                _cm = _channelish_mask(aum)
                if _cm is not None:
                    mask &= _cm
        sub = df[mask]
        if sub.empty:
            return _json_response(_empty)

        SCALE = 1e6
        tot_bal = float(sub["TRY_BALANCE"].sum())
        has_kmp = sub["_HAS_KMP"]   # cache'te önceden hesaplandı

        # ── Mevduat (deposit) dökümü — top 200, bakiye azalan ──────────────────
        sub_s = sub.sort_values("TRY_BALANCE", ascending=False).head(200)
        deposits = []
        for r in sub_s.itertuples(index=False):
            kmp = (pd.notna(r.KAMPANYA_ADI) and str(r.KAMPANYA_ADI) not in ("", "Kampanya Yok"))
            deposits.append({
                "cust_id":  int(r.CUST_ID) if pd.notna(r.CUST_ID) else None,
                "acct_id":  int(r.ACCT_ID) if pd.notna(r.ACCT_ID) else None,
                "full_nm":  _mask_full_nm(str(r.FULL_NM)),
                "segment":  str(r.SUB_SEGMENT),
                "balance_m": round(float(r.TRY_BALANCE) / SCALE, 2),
                "rate":     round(float(r.INTRST_RT), 2) if pd.notna(r.INTRST_RT) else None,
                "dtm":      int(r.DTM) if pd.notna(r.DTM) else None,
                "kampanya": bool(kmp),
                "kampanya_adi": str(r.KAMPANYA_ADI) if kmp else "",
                "share_pct": round(float(r.TRY_BALANCE) / tot_bal * 100, 2) if tot_bal else 0,
                "yeni_para_m": (round(float(r.YENI_PARA) / SCALE, 2)
                                if pd.notna(r.YENI_PARA) else None),
                "ekstrem":  (bool(r.EKSTREM) if pd.notna(r.EKSTREM) else None),
                "val_dt":   str(pd.to_datetime(r.VAL_DT).date()) if pd.notna(r.VAL_DT) else None,
                "mtrty_dt": str(pd.to_datetime(r.MTRTY_DT).date()) if pd.notna(r.MTRTY_DT) else None,
            })

        # ── Faiz (fiyatlama) histogramı — bakiye-ağırlıklı, 0.5pp bucket ──────
        # Sol-uç outlier'lar (mean − 4σ altı) tek bir "<X%" barında toplanır;
        # geri kalan 0.5% aralıklı binlere ayrılır. σ balance-weighted hesaplanır.
        rate_histogram = []
        rmask = sub["INTRST_RT"].notna()
        if rmask.any():
            sc = sub.loc[rmask].copy()
            rates = sc["INTRST_RT"].astype(float)
            bal = sc["TRY_BALANCE"].astype(float)
            wsum = float(bal.sum())
            if wsum > 0:
                mu = float((rates * bal).sum() / wsum)
                var = float((((rates - mu) ** 2) * bal).sum() / wsum)
            else:
                mu = float(rates.mean())
                var = float(rates.var(ddof=0)) if len(rates) > 1 else 0.0
            sd = var ** 0.5
            lo_thr = mu - 4.0 * sd

            rmin, rmax = float(rates.min()), float(rates.max())
            # 0.5'e hizalı taban: outlier eşiğinin üstünde kalan en düşük değer
            body_min = rmin if (sd == 0.0 or rmin >= lo_thr) else max(lo_thr, rmin)
            body_lo = float(np.floor(body_min * 2.0) / 2.0)
            body_hi = float(np.ceil(rmax * 2.0) / 2.0)
            if body_hi <= body_lo:
                body_hi = body_lo + 0.5
            edges = list(np.arange(body_lo, body_hi + 0.5, 0.5))

            # Outlier barı: eşiğin altında (ve 0.5-taban body_lo'nun altında) kalanlar
            is_outlier = rates < body_lo
            if is_outlier.any():
                out_vol = round(float(bal[is_outlier].sum()) / SCALE, 2)
                rate_histogram.append({
                    "bucket": "<{:.1f}%".format(body_lo),
                    "volume_m": out_vol,
                    "outlier": True,
                })

            body = sc.loc[~is_outlier]
            if len(body):
                body = body.copy()
                body["_rb"] = pd.cut(body["INTRST_RT"], bins=edges,
                                     include_lowest=True, right=False)
                rh = (body.groupby("_rb", observed=True)["TRY_BALANCE"].sum()
                      / SCALE).round(2)
                rate_histogram.extend([
                    {"bucket": "{:.1f}-{:.1f}%".format(float(i.left), float(i.right)),
                     "volume_m": float(v)} for i, v in rh.items()])

        # ── DTM histogramı ─────────────────────────────────────────────────────
        order = ["≤14", "15-32", "33-90", "91-180", "180+"]
        def _db(d):
            if d <= 14: return "≤14"
            if d <= 32: return "15-32"
            if d <= 90: return "33-90"
            if d <= 180: return "91-180"
            return "180+"
        sd = sub.copy()
        sd["_dtm_b"] = sd["DTM"].apply(_db)
        dh = (sd.groupby("_dtm_b")["TRY_BALANCE"].sum().reindex(order, fill_value=0) / SCALE).round(2)
        dtm_histogram = [{"bucket": k, "volume_m": float(v)} for k, v in dh.items()]

        # ── Segment split ──────────────────────────────────────────────────────
        sg = (sub.groupby("SUB_SEGMENT")["TRY_BALANCE"].sum() / SCALE).round(2)
        segments = [{"segment": s, "volume_m": float(v)}
                    for s, v in sg.sort_values(ascending=False).items()]

        # ── Özet KPI'lar ───────────────────────────────────────────────────────
        wavg_rate = (float((sub["INTRST_RT"] * sub["TRY_BALANCE"]).sum()) / tot_bal
                     if tot_bal else None)
        wavg_dtm = (float((sub["DTM"] * sub["TRY_BALANCE"]).sum()) / tot_bal
                    if tot_bal else None)
        yeni_para_m = (round(float(sub["YENI_PARA"].sum()) / SCALE, 2)
                       if sub["YENI_PARA"].notna().any() else None)
        kpis = {
            "deposit_count":  int(len(sub)),
            "customer_count": int(sub["CUST_ID"].nunique()),
            "total_balance_m": round(tot_bal / SCALE, 2),
            "wavg_rate":      round(wavg_rate, 2) if wavg_rate is not None else None,
            "wavg_dtm":       round(wavg_dtm, 0) if wavg_dtm is not None else None,
            "kampanya_pct":   round(float(sub.loc[has_kmp, "TRY_BALANCE"].sum()) / tot_bal * 100, 1)
                              if tot_bal else 0,
            "yeni_para_m":    yeni_para_m,
        }

        return _json_response({
            "ok": True, "channel": channel, "aum": aum, "t0": start, "t1": end,
            "deposits": deposits, "rate_histogram": rate_histogram,
            "dtm_histogram": dtm_histogram, "segments": segments,
            "kpis": kpis, "row_count": int(len(sub)),
        })
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 8138-8230 ──
@mevduat_panel_bp.route("/api/np/rate_volume_curve", methods=["GET"])
@login_required
def api_np_rate_volume_curve():
    """Rate–Volume concentration curve (Appendix-2 tarzı).

    Seçilen pencerede BAĞLANAN tüm mevduatları faiz oranına göre ARTAN sıralar,
    bakiyeyi kümülatif normalize eder (X = Volume %, 0→100) ve faizi (Y) çizer.
    İki eğri döner: Date(Start) penceresi + Date(End) penceresi — böylece rate
    dağılımının bakiye bazında dönemler arası nasıl kaydığı görülür.

    Pencere tanımı heatmap ile aynı (`_window_bounds`): Weekly → o tarihte biten
    rolling 7-gün; Daily → tek gün. Section filtreleri (CCY/CustTp/AUM/Segment/
    Campaign/Tenor) uygulanır. Kaynak: row-level load_np_detail (CACHE'li).
    """
    try:
        t0   = request.args.get("t0") or None
        t1   = request.args.get("t1") or None
        freq = (request.args.get("freq") or "W").upper()
        if freq not in ("D", "W"):
            freq = "W"
        if not (t0 and t1):
            return _json_response({"ok": False, "error": "t0/t1 are required."}, status=400)

        def _pl(key):
            v = request.args.get(key)
            return [x for x in v.split("|") if x] if v else None

        ccy       = _pl("filter_CCY_CODE")
        cust_tp   = _pl("filter_CUST_TP")
        segment   = _pl("filter_SUB_SEGMENT")
        campaign  = _pl("filter_RELATED_PC")
        aum_flt   = _pl("filter_AUM_BAND")
        tenor_buckets = _pl("tenor_buckets")
        aum_commons = ({NP_AUM_TO_COMMON.get(b, b) for b in aum_flt} if aum_flt else None)

        def _window_bounds(anchor):
            end = pd.Timestamp(anchor)
            start = end - pd.Timedelta(days=6) if freq == "W" else end
            return start, end

        # İki pencerenin birleşik aralığında detail'i bir kez yükle (memoize'li).
        (a0s, a0e), (a1s, a1e) = _window_bounds(t0), _window_bounds(t1)
        span_start = min(a0s, a1s).date().isoformat()
        span_end   = max(a0e, a1e).date().isoformat()
        detail = load_np_detail(span_start, span_end)

        def _section_mask(df):
            mask = pd.Series(True, index=df.index)
            if ccy:        mask &= df["CCY_CODE"].isin(ccy)
            if cust_tp:    mask &= df["CUST_TP"].isin(cust_tp)
            if segment:    mask &= df["SUB_SEGMENT"].isin(segment)
            if aum_commons is not None:
                mask &= df["AUM_COMMON"].isin(aum_commons)
            if tenor_buckets:
                mask &= df["TENOR_COMMON"].isin(tenor_buckets)
            # Kanal (RELATED_PC): dev'de 'Bilinmiyor' → filtre uygulanmaz.
            if campaign and df["RELATED_PC_CODE"].astype(str).ne("Bilinmiyor").any():
                mask &= df["RELATED_PC_CODE"].astype(str).isin(campaign)
            return mask

        SCALE = 1e6

        def _curve(anchor):
            wstart, wend = _window_bounds(anchor)
            m = (detail["VAL_DT"] >= wstart) & (detail["VAL_DT"] <= wend)
            sub = detail[m & _section_mask(detail)]
            sub = sub[sub["INTRST_RT"].notna() & (sub["TRY_BALANCE"] > 0)]
            total = float(sub["TRY_BALANCE"].sum())
            if sub.empty or total <= 0:
                return {"anchor": anchor, "window_start": wstart.date().isoformat(),
                        "window_end": wend.date().isoformat(), "total_mio": 0.0,
                        "x": [], "y": [], "deposit_count": 0}
            # Aynı faiz seviyesindeki mevduatları birleştir (bounded, temiz basamak).
            g = (sub.groupby(sub["INTRST_RT"].round(2))["TRY_BALANCE"].sum()
                    .sort_index())                     # faiz ARTAN
            cum = g.cumsum()
            x = (cum / total * 100.0).round(4).tolist()
            y = [round(float(r), 2) for r in g.index.tolist()]
            # Basamak x=0'dan başlasın: ilk seviyeyi (0, y0) ile önle.
            x = [0.0] + x
            y = [y[0]] + y
            return {"anchor": anchor,
                    "window_start": wstart.date().isoformat(),
                    "window_end": wend.date().isoformat(),
                    "total_mio": round(total / SCALE, 0),
                    "x": x, "y": y, "deposit_count": int(len(sub))}

        series = [_curve(t0), _curve(t1)]
        return _json_response({"ok": True, "freq": freq, "t0": t0, "t1": t1,
                               "series": series})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)



# ── app.py 8231-8249 ──
@mevduat_panel_bp.route("/api/np/volume_pricing", methods=["GET"])
@login_required
def api_np_volume_pricing():
    """Time series: new production volume + weighted-avg rate, grouped by CCY."""
    try:
        df = load_np_data()
        _npf = _parse_np_filters()
        df_f = np_apply_filters(df, **_npf)
        freq = _freq_param()
        ts = np_aggregate_timeseries(df_f, group_by=["CCY_CODE"], freq=freq,
                                     week_anchor=_npf.get("date_to"))
        ts = ts.rename(columns={"DATE": "date", "CCY_CODE": "ccy",
                                 "NP_HACIM": "np_hacim", "NP_FAIZ": "np_faiz",
                                 "OS_BAKIYE": "os_bakiye"})
        return _json_response({"ok": True, "freq": freq, "records": _np_records(ts)})
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)





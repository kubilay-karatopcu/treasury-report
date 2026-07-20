"""Ortak yardımcılar — paletler, oran dönüşümü, bubble/heatmap kurucuları.

Kaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları
blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları
uyarlandı (bkz. nim_panel/tools/extract_a2.py).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import logging

log = logging.getLogger("nim_panel")


# Kaynak: engine/sector_data.py::_DEMAND_SUBPRODUCTS — sector_data portu
# (Faz A6) gelene kadar sabit burada yaşar; A6'da tek kaynağa bağlanır.
_DEMAND_SUBPRODUCTS = ("KGH", "BTH")

# ── app.py 122-123 ──
_SCENARIO_PALETTE = ["#D4A574", "#4A6B8A", "#7A9B7E", "#B8826B", "#8B7BA8", "#B8946A", "#5C6478"]


# ── app.py 131-137 ──
def _scenario_color(idx: int) -> str:
    """Return a stable hex colour for zero-based scenario index."""
    return _SCENARIO_PALETTE[idx % len(_SCENARIO_PALETTE)]


# =========================
# Dashboard config

# ── app.py 145-148 ──
Y_MIN_FLOOR = 350
Y_MIN_SPAN = 80
Y_PAD_RATIO = 0.15
Y_MAX_PAD = 150

# ── app.py 227-243 ──
def _wavg(x: pd.Series, w: pd.Series) -> float:
    x = x.astype(float)
    w = w.astype(float)
    s = w.sum()
    if s == 0 or np.isnan(s):
        return float(x.mean())
    return float((x * w).sum() / s)


def _bps(x_decimal: float) -> int:
    return int(round(float(x_decimal) * 10000.0))


def _fmt_int(x: float) -> str:
    return f"{int(round(float(x))):,}"



# ── app.py 244-251 ──
def _pick_col(df: pd.DataFrame, preferred: str, fallback: str) -> str:
    if preferred in df.columns:
        return preferred
    if fallback in df.columns:
        return fallback
    raise ValueError(f"Column not found: '{preferred}' or '{fallback}'")



# ── app.py 252-266 ──
def _auto_y_range(values_bps, pad_ratio=0.15, min_span=80, min_floor=None, y_max_pad=None):
    v = np.asarray([float(x) for x in values_bps], dtype=float)
    vmin, vmax = float(np.nanmin(v)), float(np.nanmax(v))
    span = max(vmax - vmin, float(min_span))
    pad = span * float(pad_ratio)
    y0, y1 = vmin - pad, vmax + pad
    if y_max_pad is not None:
        y1 = min(y1, vmax + float(y_max_pad))
    if min_floor is not None:
        y0 = max(y0, float(min_floor))
    if y1 <= y0:
        y0, y1 = vmin - pad, vmax + pad
    return [y0, y1]



# ── app.py 267-287 ──
def _date_str(dt: pd.Timestamp) -> str:
    return pd.Timestamp(dt).strftime("%Y-%m-%d")


def _aum_numeric_key(b: str) -> float:
    """Sort key for AUM band labels like 'AUM_0_100K', 'AUM_5M_10M'.
    Returns the numeric lower bound (absolute value). Empty/unparseable → inf (sorted last)."""
    if not b or str(b).strip() in ("", "nan"):
        return float("inf")
    m = re.match(r"^AUM_(\d+(?:[.,]\d+)?)([KM]?)", str(b).strip(), re.IGNORECASE)
    if m:
        val = float(m.group(1).replace(",", "."))
        suf = m.group(2).upper()
        if suf == "K":
            val *= 1_000.0
        elif suf == "M":
            val *= 1_000_000.0
        return val
    return float("inf")



# ── app.py 956-1099 ──
def _build_bubble_charts(m: pd.DataFrame) -> tuple:
    """Two Plotly bubble charts driven by the merged-period DataFrame.

    Both engines build the same `m` shape: PRODUCT, b0, r0, b1, r1.
    Used by Cost Analysis sub-tabs (Monthly Averages + Daily Evolution).

    Bubble size encodes balance level (average of t0/t1). The Balance chart
    plots nominal vs % delta; the Rate chart plots Δbps vs ending rate level.
    Empty `m` (no overlap) returns two no-op placeholders.
    """
    if m.empty:
        empty = {"data": [], "layout": {"title": {"text": "No data"}}}
        return empty, empty

    valid = m[(m["b0"] != 0) | (m["b1"] != 0)].copy()
    if valid.empty:
        empty = {"data": [], "layout": {"title": {"text": "No data"}}}
        return empty, empty

    products = valid["PRODUCT"].astype(str).tolist()
    b0 = valid["b0"].astype(float).values
    b1 = valid["b1"].astype(float).values
    r0 = valid["r0"].astype(float).values
    r1 = valid["r1"].astype(float).values

    M = 1e6
    b0_m = b0 / M
    b1_m = b1 / M
    sizes_m = (np.abs(b0_m) + np.abs(b1_m)) / 2.0
    sizes_max = float(np.max(sizes_m)) if len(sizes_m) and np.max(sizes_m) > 0 else 1.0
    sizeref = 2.0 * sizes_max / (45.0 ** 2)

    # Opsiyonel outstanding (STOK) bakiye — yalnız NP bubble endpoint'i gönderir.
    # Varsa Balance chart customdata'sına os_b0_m, os_b1_m (pozisyon 3,4) eklenir;
    # frontend Balance X'i bu OS deltasıyla çizer (boyut/faiz new-prod'da kalır).
    # Cost bubble'larında bu kolonlar YOK → customdata 3 elemanlı (DEĞİŞMEZ).
    _has_os = ("os_b0" in valid.columns) and ("os_b1" in valid.columns)
    if _has_os:
        osb0_m = valid["os_b0"].astype(float).values / M
        osb1_m = valid["os_b1"].astype(float).values / M
        bal_customdata = [[float(a), float(b), float(r) * 100.0, float(o0), float(o1)]
                          for a, b, r, o0, o1 in zip(b0_m, b1_m, r0, osb0_m, osb1_m)]
    else:
        bal_customdata = [[float(a), float(b), float(r) * 100.0]
                          for a, b, r in zip(b0_m, b1_m, r0)]

    bal_delta_m = b1_m - b0_m
    rate_end_pct_bal = r1 * 100.0   # Y axis for Balance Evolution = ending rate level

    bubble_balance = {
        "data": [{
            "type": "scatter",
            "mode": "markers",
            "x": bal_delta_m.tolist(),
            "y": rate_end_pct_bal.tolist(),
            "text": products,
            "marker": {
                "size": sizes_m.tolist(),
                "sizemode": "area",
                "sizeref": sizeref,
                "sizemin": 4,
                "color": bal_delta_m.tolist(),
                # PRISMA tonal diverging (saf kirmizi/yesil yasak): terracotta -> slate -> adacayi
                "colorscale": [[0.0, "#B8826B"], [0.5, "#5C6478"], [1.0, "#7A9B7E"]],
                "cmid": 0,
                "showscale": False,
                "line": {"width": 1, "color": "#8B95A7"},
            },
            "customdata": bal_customdata,
            "hovertemplate": (
                "<b>%{text}</b><br>"
                "Balance t0: %{customdata[0]:,.1f} ₺M<br>"
                "Balance t1: %{customdata[1]:,.1f} ₺M<br>"
                "Δ Balance: %{x:,.1f} ₺M<br>"
                "Rate t0: %{customdata[2]:.2f}%<br>"
                "Rate t1 (end): %{y:.2f}%<br>"
                "<i>Click for details</i><extra></extra>"
            ),
        }],
        "layout": {
            "title": {"text": "Balance Evolution", "x": 0.02, "xanchor": "left",
                       "font": {"size": 15, "color": "#1a202c"}},
            "xaxis": {"title": {"text": ("Δ Outstanding Balance (₺M)" if _has_os
                                          else "Δ Balance (₺M)")},
                       # Binlik ayraç: ₺M değerleri gruplanır (ör. 70,580). Hover
                       # de aynı "," ayracını kullanıyor → grafik kendi içinde tutarlı.
                       "tickformat": ",.0f",
                       "zeroline": True, "zerolinecolor": "#a0aec0"},
            "yaxis": {"title": {"text": "Interest Rate (end %)"},
                       "zeroline": False, "ticksuffix": "%"},
            "showlegend": False,
            "margin": {"t": 40, "r": 30, "b": 60, "l": 80},
            "plot_bgcolor": "rgba(0,0,0,0)",
        },
    }

    rate_bps = (r1 - r0) * 10000.0
    rate_end_pct = r1 * 100.0

    bubble_rate = {
        "data": [{
            "type": "scatter",
            "mode": "markers",
            "x": rate_bps.tolist(),
            "y": rate_end_pct.tolist(),
            "text": products,
            "marker": {
                "size": sizes_m.tolist(),
                "sizemode": "area",
                "sizeref": sizeref,
                "sizemin": 4,
                "color": rate_bps.tolist(),
                # PRISMA tonal diverging: adacayi (dusen maliyet) -> slate -> terracotta (artan)
                "colorscale": [[0.0, "#7A9B7E"], [0.5, "#5C6478"], [1.0, "#B8826B"]],
                "cmid": 0,
                "showscale": False,
                "line": {"width": 1, "color": "#8B95A7"},
            },
            "customdata": [[float(a) * 100.0, float(bm)] for a, bm in zip(r0, b1_m)],
            "hovertemplate": (
                "<b>%{text}</b><br>"
                "Rate t0: %{customdata[0]:.2f}%<br>"
                "Rate t1: %{y:.2f}%<br>"
                "Δ Rate: %{x:+.0f} bps<br>"   # bps → tam sayı (küsürat yok)
                "Balance t1: %{customdata[1]:,.1f} ₺M<br>"
                "<i>Click for details</i><extra></extra>"
            ),
        }],
        "layout": {
            "title": {"text": "Interest Rate Evolution", "x": 0.02, "xanchor": "left",
                       "font": {"size": 15, "color": "#1a202c"}},
            "xaxis": {"title": {"text": "Δ Rate (bps)"},
                       "zeroline": True, "zerolinecolor": "#a0aec0"},
            "yaxis": {"title": {"text": "Rate (end %)"},
                       "zeroline": True, "zerolinecolor": "#a0aec0", "ticksuffix": "%"},
            "showlegend": False,
            "margin": {"t": 40, "r": 30, "b": 60, "l": 80},
            "plot_bgcolor": "rgba(0,0,0,0)",
        },
    }

    return bubble_balance, bubble_rate



# ── app.py 1100-1158 ──
_RATE_CONV_MODES = ("simple", "compound", "on")


def _convert_rate_series(rates_dec: pd.Series, tenor_days, mode: str) -> pd.Series:
    """Faiz GÖSTERİM TİPİ dönüşümü — satır bazında, kendi vadesiyle (act/365).

    Outstanding Cost Analysis "Rate Type" seçicisi (DECIMAL in/out):
      simple   → değişmez (ham veri zaten simple).
      compound → (1 + r·t/365)^(365/t) − 1        yıllık bileşik.
      on       → ((1 + r·t/365)^(1/t) − 1) · 365   O/N eşleniği: aynı dönem
                 getirisini GÜNLÜK bileşiklenmeyle veren gecelik basit oran
                 (≡ 365·((1+compound)^(1/365) − 1)).
    t<=0 / NaN → dönüşüm tanımsız, oran DEĞİŞMEZ (sessiz bozulma yok).
    Dönüşüm HAM satırda yapılır; tüm downstream ağırlıklı ortalamalar
    (waterfall, bubble, heatmap, KPI, drill) dönüşmüş oranı ağırlıklar.
    """
    if mode not in ("compound", "on"):
        return rates_dec
    r = pd.to_numeric(rates_dec, errors="coerce").astype(float).to_numpy()
    t = pd.to_numeric(tenor_days, errors="coerce").astype(float).to_numpy()
    out = r.copy()
    ok = np.isfinite(r) & np.isfinite(t) & (t > 0)
    period = 1.0 + r[ok] * (t[ok] / 365.0)
    pos = period > 0
    idx = np.where(ok)[0][pos]
    tt = t[ok][pos]
    if mode == "compound":
        out[idx] = np.power(period[pos], 365.0 / tt) - 1.0
    else:
        out[idx] = (np.power(period[pos], 1.0 / tt) - 1.0) * 365.0
    return pd.Series(out, index=rates_dec.index)


def _apply_demand_deposit(df: pd.DataFrame, demand_pct: float) -> pd.DataFrame:
    """VADESİZ (demand) etkisi — KGH/BTH satırlarına sıfır-faizli vadesiz varsayımı.

    KGH/BTH ürünlerinde normal bakiyeye ek olarak bakiyenin %demand_pct'i kadar
    %0 faizli vadesiz mevduat varmış gibi varsayılır: bakiye ×(1+p), simple
    INTEREST_RATE ÷(1+p) (faiz TUTARI B·r sabit → oran seyrelir, bakiye büyür).
    Downstream tüm bakiye-ağırlıklı görünümler (bubble, wavg, waterfall, heatmap)
    bu değerleri kullanır. df.copy() döner — cache'teki orijinali BOZMAZ (Kırmızı
    Çizgi 6). demand_pct<=0 veya DIM_SUBPRODUCT yoksa df aynen (kopya) döner.

    NOT: Bu dönüşüm rate_conv (compound/on) çevriminden ÖNCE uygulanmalı ki
    seyreltilmiş simple oran, satırın kendi vadesiyle doğru çevrilsin (karar (a):
    tenor demand'dan etkilenmez — INTEREST_RATE değişir, TENOR kolonu değişmez).
    """
    # port: _DEMAND_SUBPRODUCTS modul sabiti (kaynak: engine/sector_data.py)
    p = max(0.0, float(demand_pct or 0.0)) / 100.0
    out = df.copy()
    if p <= 0 or "DIM_SUBPRODUCT" not in out.columns:
        return out
    m = out["DIM_SUBPRODUCT"].astype(str).isin(_DEMAND_SUBPRODUCTS)
    if m.any():
        out.loc[m, "INTEREST_RATE"] = out.loc[m, "INTEREST_RATE"] / (1.0 + p)
        out.loc[m, "BALANCE"] = out.loc[m, "BALANCE"] * (1.0 + p)
    return out



# ── app.py 1159-1218 ──
def _cost_bubble_source(df0_raw: pd.DataFrame, df1_raw: pd.DataFrame,
                        ordered_dims: List[str],
                        dim_col_map: Dict[str, str]) -> tuple:
    """Cost Analysis bubble kaynağını (aktif-boyutlar × DIM_BUCKET) İNCE
    granülerlikte üretir.

    Amaç: TENOR (MATURITY_BUCKET) filtresini frontend'de client-side uygulamak.
    `_aggregateBubbles` FİLTRE'yi tüm boyutlarda, GRUPLAMA'yı yalnız activeDims
    (aktif ekran boyutları, MATURITY_BUCKET HARİÇ) üzerinde yaptığından, vade
    filtresi "tümü" iken ince hücreler ürün seviyesine geri toplanır → bubble'lar
    eski (ürün-bazlı) haliyle BİREBİR aynı görünür; bir vade kovası kapatılınca
    o kovanın ince hücreleri düşer.

    Döner: (m_bub[PRODUCT,b0,r0,b1,r1], product_dims). product_dims[label] aktif
    boyut değerleri + MATURITY_BUCKET taşır. Etiket, `_group_by_dims` / drill'in
    `_PROD` kuralıyla uyumlu: "_".join(aktif değerler + bucket).
    """
    cols = [dim_col_map[d] for d in ordered_dims if dim_col_map.get(d)]
    has_bucket = ("DIM_BUCKET" in df0_raw.columns) or ("DIM_BUCKET" in df1_raw.columns)
    grp_cols = cols + (["DIM_BUCKET"] if has_bucket else [])

    def _agg(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=["PRODUCT", "BALANCE", "INTEREST_RATE"] + grp_cols)
        g = df.copy()
        g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
        a = g.groupby(grp_cols, dropna=False)[["BALANCE", "_wr"]].sum().reset_index()
        a["INTEREST_RATE"] = np.where(a["BALANCE"] != 0, a["_wr"] / a["BALANCE"], 0.0)
        a["PRODUCT"] = (
            a[grp_cols].astype(str)
            .apply(lambda r: "_".join(v for v in r if v and v not in ("nan", "None", "")), axis=1)
        )
        return a

    a0 = _agg(df0_raw)
    a1 = _agg(df1_raw)
    m_bub = (
        a0[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
        .rename(columns={"BALANCE": "b0", "INTEREST_RATE": "r0"})
        .merge(
            a1[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
            .rename(columns={"BALANCE": "b1", "INTEREST_RATE": "r1"}),
            on="PRODUCT", how="outer",
        )
        .fillna(0.0)
    )
    product_dims: Dict[str, Dict[str, str]] = {}
    for a in (a0, a1):
        for _, row in a.iterrows():
            lbl = row["PRODUCT"]
            if lbl in product_dims:
                continue
            dd = {d: str(row[dim_col_map[d]]) for d in ordered_dims
                  if dim_col_map.get(d) in a.columns}
            if has_bucket:
                dd["MATURITY_BUCKET"] = str(row["DIM_BUCKET"])
            product_dims[lbl] = dd
    return m_bub, product_dims



# ── app.py 1219-1320 ──
def _rate_heatmap_seg_aum(raw_df0: pd.DataFrame, raw_df1: pd.DataFrame,
                          row_col: str = "DIM_SEGMENT",
                          col_col: str = "DIM_AUM") -> Optional[dict]:
    """Weighted-average interest rate heatmap (<row_col> × <col_col>) from raw data.

    Rows follow ``row_col`` (Decomposition Dim); columns follow ``col_col``
    (Second Dec. Dim, default DIM_AUM). Weighted average is balance-weighted:
    wavg = Σ(BALANCE·RATE) / Σ(BALANCE).
    Returns dict with delta_bps and rate_t1_bps grids, or None if columns are missing.
    """
    if raw_df0 is None or raw_df1 is None:
        return None
    # Row = col dejenere olur → kolonu alternatife düşür (frontend mutex'i zaten
    # engelliyor; burası API'yi doğrudan çağıranlara karşı guard).
    if col_col == row_col:
        col_col = "DIM_AUM" if row_col != "DIM_AUM" else "DIM_SEGMENT"
    if row_col not in raw_df0.columns or col_col not in raw_df0.columns:
        return None
    _row_key = _aum_numeric_key if row_col == "DIM_AUM" else str
    _col_key = _aum_numeric_key if col_col == "DIM_AUM" else str

    def _wavg_agg(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float)
        g = df.copy()
        g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
        agg = g.groupby([row_col, col_col], dropna=False)[["BALANCE", "_wr"]].sum()
        wavg = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"], 0.0)
        return pd.Series(wavg, index=agg.index)

    def _wavg_1d(df: pd.DataFrame, dim: str) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=float)
        g = df.copy()
        g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
        agg = g.groupby(dim, dropna=False)[["BALANCE", "_wr"]].sum()
        wavg = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"], 0.0)
        return pd.Series(wavg, index=agg.index)

    def _wavg_grand(df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        tot_b = float(df["BALANCE"].sum())
        return float((df["BALANCE"] * df["INTEREST_RATE"]).sum()) / tot_b if tot_b else 0.0

    r0 = _wavg_agg(raw_df0)
    r1 = _wavg_agg(raw_df1)
    all_segs = sorted({str(s) for s, _ in list(r0.index) + list(r1.index)}, key=_row_key)
    all_aums = sorted({str(a) for _, a in list(r0.index) + list(r1.index)}, key=_col_key)
    if not all_segs or not all_aums:
        return None

    delta_bps_grid: List[List[Optional[float]]] = []
    rate_t1_pct_grid: List[List[Optional[float]]] = []
    for s in all_segs:
        row_delta: List[Optional[float]] = []
        row_t1: List[Optional[float]] = []
        for a in all_aums:
            v0 = float(r0.get((s, a), 0.0))
            v1 = float(r1.get((s, a), 0.0))
            row_delta.append(round((v1 - v0) * 10000.0, 2) if v0 != 0.0 and v1 != 0.0 else None)
            row_t1.append(round(v1 * 100.0, 2) if v1 != 0.0 else None)
        delta_bps_grid.append(row_delta)
        rate_t1_pct_grid.append(row_t1)

    # Totals — per row dim (col total) and per AUM (row total)
    r0_seg = _wavg_1d(raw_df0, row_col)
    r1_seg = _wavg_1d(raw_df1, row_col)
    col_total_delta_bps: List[Optional[float]] = []
    col_total_rate_t1_pct: List[Optional[float]] = []
    for s in all_segs:
        v0 = float(r0_seg.get(s, 0.0))
        v1 = float(r1_seg.get(s, 0.0))
        col_total_delta_bps.append(round((v1 - v0) * 10000.0, 2) if v0 != 0.0 and v1 != 0.0 else None)
        col_total_rate_t1_pct.append(round(v1 * 100.0, 2) if v1 != 0.0 else None)

    r0_aum = _wavg_1d(raw_df0, col_col)
    r1_aum = _wavg_1d(raw_df1, col_col)
    row_total_delta_bps: List[Optional[float]] = []
    row_total_rate_t1_pct: List[Optional[float]] = []
    for a in all_aums:
        v0 = float(r0_aum.get(a, 0.0))
        v1 = float(r1_aum.get(a, 0.0))
        row_total_delta_bps.append(round((v1 - v0) * 10000.0, 2) if v0 != 0.0 and v1 != 0.0 else None)
        row_total_rate_t1_pct.append(round(v1 * 100.0, 2) if v1 != 0.0 else None)

    grand_r0 = _wavg_grand(raw_df0)
    grand_r1 = _wavg_grand(raw_df1)
    return {
        "rows":                   all_segs,
        "cols":                   all_aums,
        "delta_bps":              delta_bps_grid,
        "rate_t1_pct":            rate_t1_pct_grid,
        "col_total_delta_bps":    col_total_delta_bps,
        "col_total_rate_t1_pct":  col_total_rate_t1_pct,
        "row_total_delta_bps":    row_total_delta_bps,
        "row_total_rate_t1_pct":  row_total_rate_t1_pct,
        "grand_total_delta_bps":  round((grand_r1 - grand_r0) * 10000.0, 2) if grand_r0 and grand_r1 else None,
        "grand_total_rate_t1_pct": round(grand_r1 * 100.0, 2) if grand_r1 else None,
    }




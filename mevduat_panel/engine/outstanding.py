"""Outstanding üçlüsü motorları — Cost (DepositDetail/DailyDeposit), Tenor (+Swap), Balance.

Kaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları
blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları
uyarlandı (bkz. mevduat_panel/tools/extract_a2.py).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_source import load_dataframe
from .chart_builder import NIMChartBuilder
from .common import (
    _apply_demand_deposit,
    _aum_numeric_key,
    _bps,
    _build_bubble_charts,
    _convert_rate_series,
    _cost_bubble_source,
    _date_str,
    _fmt_int,
    _rate_heatmap_seg_aum,
    _wavg,
)

import logging

log = logging.getLogger("mevduat_panel")


# Port notu: kaynak config.ENV — yalnız log mesajlarında kullanılır.
_ENV = "PRODUCTION_DB"

# ── app.py 953-954 ──
_DD_CACHE: Dict[str, object] = {}


# ── app.py 1321-1724 ──
class DepositDetailEngine:
    """Decomposes change in weighted-average TRY deposit rate between two dates.

    Source: TRY_DEPOSIT_DETAIL.xlsx (MONTH, PRODUCT, DAYS, BALANCE, INTEREST_RATE)
    INTEREST_RATE stored as percent (e.g. 4.318) — converted to decimal internally.
    Output mirrors NIMChartBuilder format (type: waterfall / bar).

    PRODUCT names encode three dimensions joined by "_":
        Vadeli_G_AUM_0_100K → PRODUCT=Vadeli, CUSTOMER_TYPE=G, AUM=AUM_0_100K
        Kasa_T / O/N_G      → AUM dimension empty (only Vadeli has AUM buckets)

    SEGMENT is an Oracle-only dimension (PRODUCTION_DB path); the dev SQLite
    and the legacy Excel don't carry it, so it collapses to a single empty
    bucket on those sources and the toggle becomes a no-op.
    """

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]

    @classmethod
    def _load(cls):
        if "df" not in _DD_CACHE:
            df = load_dataframe("TRY_DEPOSIT_DETAIL")
            # SQLite returns DATE columns as ISO strings (YYYY-MM-DD) and Excel
            # delivers them as datetime cells — both parse correctly without
            # dayfirst (which would otherwise mangle "2026-02-01" into 2 Jan).
            df["MONTH"] = pd.to_datetime(df["MONTH"], errors="coerce")
            df["INTEREST_RATE"] = df["INTEREST_RATE"].astype(float) / 100.0
            df["BALANCE"] = df["BALANCE"].astype(float)
            # Müşteri adedi (CUSTOMER_NUMBER) — DEPOSITUSAGE_NEW'den gruplanmış adet.
            # Kolon yoksa/boşsa SESSİZCE 0 basma (#9) — startup log'unda nedenini söyle.
            if "CUSTOMER_NUMBER" in df.columns:
                df["CUST_COUNT"] = pd.to_numeric(df["CUSTOMER_NUMBER"], errors="coerce").fillna(0.0)
                _cc_nz = int((df["CUST_COUNT"] > 0).sum())
                if _cc_nz == 0:
                    log.warning("TRY_DEPOSIT_DETAIL: CUSTOMER_NUMBER kolonu geldi ama TÜM "
                          "değerler 0/NULL — Customer Number Heatmap sıfır gösterecek. "
                          "Kaynak tabloda (DEPOSITUSAGE_NEW) bu tarih aralığında customer_number "
                          "dolu mu kontrol et.")
                else:
                    log.info(f"TRY_DEPOSIT_DETAIL: CUSTOMER_NUMBER OK "
                          f"({_cc_nz:,}/{len(df):,} satır > 0).")
            else:
                df["CUST_COUNT"] = 0.0
                log.warning(f"TRY_DEPOSIT_DETAIL: CUSTOMER_NUMBER kolonu YOK (ENV={_ENV}) — "
                      "Customer Number Heatmap sıfır gösterecek. "
                      "PRODUCTION_DB isen queries/prod/TRY_DEPOSIT_DETAIL.sql güncel mi "
                      "(SUM(CUSTOMER_NUMBER) içeriyor mu) + Flask restart edildi mi? "
                      "PRODUCTION_EXC (Excel) bu kolonu desteklemez.")
            tokens = df["PRODUCT"].astype(str).str.split("_")
            df["DIM_PRODUCT"]  = tokens.str[0]
            df["DIM_CUSTOMER"] = tokens.str[1].fillna("")
            df["DIM_AUM"]      = tokens.str[2:].str.join("_").fillna("")
            # SUB_PRODUCT: O/N alt-ürünü (KGH/BTH/Other O/N); yoksa TYPE2(=DIM_PRODUCT).
            df["DIM_SUBPRODUCT"] = (df["SUB_PRODUCT"].astype(str)
                                    if "SUB_PRODUCT" in df.columns else df["DIM_PRODUCT"])
            # TENOR_RATE is a new column added in the updated Oracle query
            # (weighted-avg tenor in days). Load as numeric; not used in decompositions.
            if "TENOR_RATE" in df.columns:
                df["TENOR_RATE"] = pd.to_numeric(df["TENOR_RATE"], errors="coerce")
            # DTM_RATE — TENOR_RATE'in kalan-vade (remaining days to maturity)
            # muadili; Tenor Analysis DTM modunda TENOR_RATE yerine kullanılır.
            if "DTM_RATE" in df.columns:
                df["DTM_RATE"] = pd.to_numeric(df["DTM_RATE"], errors="coerce")
            if "SEGMENT" in df.columns:
                df["DIM_SEGMENT"] = df["SEGMENT"].astype(str).fillna("").replace("nan", "")
                # Oracle PRODUCT format (old): TYPE2_CUST_TP_AUM_TYPE_SEGMENT
                # Oracle PRODUCT format (new): TYPE2_CUST_TP_AUM_TYPE_SEGMENT_INF-SUP
                # The token-joined DIM_AUM contains the trailing SEGMENT and, for the
                # new query, a vade_bucket suffix (_INF-SUP e.g. _0-30, _30-90).
                # 1) Extract vade_bucket into its own dimension (DIM_BUCKET)
                #    before stripping it from DIM_AUM. Empty for non-Vadeli/Kasa/O/N.
                bucket_match = df["DIM_AUM"].astype(str).str.extract(r"_(\d+-\d+)$")[0]
                df["DIM_BUCKET"] = bucket_match.fillna("")
                # 2) Strip vade_bucket first (regex _digits-digits at end), then SEGMENT.
                def _strip_seg(aum: str, seg: str) -> str:
                    aum = re.sub(r"_\d+-\d+$", "", aum)  # remove vade_bucket if present
                    if not seg:
                        return aum
                    if aum == seg:          # no real AUM bucket; slot held only SEGMENT
                        return ""
                    suf = "_" + seg
                    return aum[: -len(suf)] if aum.endswith(suf) else aum
                df["DIM_AUM"] = [
                    _strip_seg(a, s)
                    for a, s in zip(df["DIM_AUM"], df["DIM_SEGMENT"])
                ]
            else:
                df["DIM_SEGMENT"] = ""
                # Even without SEGMENT, attempt to derive bucket from PRODUCT
                # (defensive: if a different code path produces VADE_BUCKET suffix).
                bucket_match = df["DIM_AUM"].astype(str).str.extract(r"_(\d+-\d+)$")[0]
                df["DIM_BUCKET"] = bucket_match.fillna("")
            # Explicit VADE_BUCKET / KALAN_VADE_BUCKET kolonları (prod'un güncel
            # TRY_DEPOSIT_DETAIL.sql'i + dev fabrikasyonu). VARSA yukarıdaki PRODUCT
            # token-extraction'ını EZER → monthly DIM_BUCKET / DIM_BUCKET_DTM artık
            # DailyDepositEngine ile birebir aynı şekilde açık kolondan gelir; böylece
            # Cost/Balance "Tenor" kırılımı ve Tenor Analysis TENOR/DTM modu monthly
            # kaynakta da çalışır. DIM_BUCKET_DTM = kalan-vade (DTM modu).
            def _clean_bucket(s):
                return s.fillna("").astype(str).replace("None", "").replace("nan", "")
            if "VADE_BUCKET" in df.columns:
                df["DIM_BUCKET"] = _clean_bucket(df["VADE_BUCKET"])
            df["DIM_BUCKET_DTM"] = (_clean_bucket(df["KALAN_VADE_BUCKET"])
                                    if "KALAN_VADE_BUCKET" in df.columns else "")
            _DD_CACHE["df"] = df
            _DD_CACHE["dates"] = [
                _date_str(pd.Timestamp(d))
                for d in sorted(df["MONTH"].dropna().unique())
            ]
        return _DD_CACHE["df"], _DD_CACHE["dates"]

    @classmethod
    def _group_by_dims(cls, df_month: pd.DataFrame, dims: List[str]) -> pd.DataFrame:
        """Collapse rows to the selected dimensions.

        Returns a df with PRODUCT (joined label), BALANCE (sum) and
        INTEREST_RATE (balance-weighted average). With all three dimensions
        selected the labels equal the original PRODUCT names.
        """
        dim_cols = {
            "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
            "CUSTOMER_TYPE": "DIM_CUSTOMER",
            "AUM":           "DIM_AUM",
            "SEGMENT":       "DIM_SEGMENT",
        }
        cols = [dim_cols[d] for d in cls.DIMENSIONS if d in dims]
        g = df_month.copy()
        g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
        agg = g.groupby(cols, dropna=False)[["BALANCE", "_wr"]].sum().reset_index()
        agg["INTEREST_RATE"] = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"], 0.0)
        agg["PRODUCT"] = (
            agg[cols].astype(str)
            .apply(lambda r: "_".join(v for v in r if v), axis=1)
        )
        return agg[["PRODUCT", "BALANCE", "INTEREST_RATE"] + cols]

    @classmethod
    def get_dates(cls) -> List[str]:
        _, dates = cls._load()
        return dates

    @classmethod
    def build_waterfalls(cls, date_0: str, date_1: str,
                         dims: Optional[List[str]] = None, top_n: int = 7,
                         include_tenor: bool = False,
                         rate_conv: str = "simple", demand_pct: float = 0.0) -> tuple:
        df, _ = cls._load()
        if dims is None:
            dims = list(cls.DIMENSIONS)
        dims = [d for d in cls.DIMENSIONS if d in dims]
        if not dims:
            raise ValueError("Select at least one dimension (PRODUCT / CUSTOMER_TYPE / AUM / SEGMENT).")
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        df0 = df[df["MONTH"] == d0].copy()
        df1 = df[df["MONTH"] == d1].copy()
        if df0.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_1={date_1}.")
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        # VADESİZ (demand) etkisi rate_conv'dan ÖNCE: KGH/BTH bakiye ×(1+p),
        # simple oran ÷(1+p). Böylece seyreltilmiş simple oran kendi vadesiyle
        # doğru compound/on'a çevrilir. demand_pct<=0 → no-op.
        if demand_pct and demand_pct > 0:
            df0 = _apply_demand_deposit(df0, demand_pct)
            df1 = _apply_demand_deposit(df1, demand_pct)
        # Rate Type dönüşümü (simple|compound|on): HAM satırda, satırın kendi
        # wavg vadesiyle — downstream her şey (waterfall/bubble/heatmap/KPI)
        # dönüşmüş oranı ağırlıklar. Tenor kolonu: monthly TENOR_RATE,
        # daily AGIRLIKLI_ORT_TENOR.
        if rate_conv in ("compound", "on"):
            _tcol = "TENOR_RATE" if "TENOR_RATE" in df0.columns else "AGIRLIKLI_ORT_TENOR"
            if _tcol in df0.columns:
                df0["INTEREST_RATE"] = _convert_rate_series(df0["INTEREST_RATE"], df0[_tcol], rate_conv)
                df1["INTEREST_RATE"] = _convert_rate_series(df1["INTEREST_RATE"], df1[_tcol], rate_conv)
        _has_seg_aum = "DIM_SEGMENT" in df0.columns and "DIM_AUM" in df0.columns
        raw_df0_hm = df0[["BALANCE", "INTEREST_RATE", "DIM_SEGMENT", "DIM_AUM"]].copy() if _has_seg_aum else None
        raw_df1_hm = df1[["BALANCE", "INTEREST_RATE", "DIM_SEGMENT", "DIM_AUM"]].copy() if _has_seg_aum else None
        _dim_col_map = {
            "PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT", "CUSTOMER_TYPE": "DIM_CUSTOMER",
            "AUM": "DIM_AUM", "SEGMENT": "DIM_SEGMENT",
        }
        # Filtre paneli META'sı GRUPLAMADAN ÖNCE ve TÜM boyutlardan toplanır —
        # panel "Dimensions" seçiminden bağımsız SABİT kalır (bkz. DailyDeposit-
        # Engine'deki eş not; heatmap eksen gruplama her durumda çalışsın).
        bubble_filter_meta: Dict[str, List[str]] = {}
        for d in cls.DIMENSIONS:
            col = _dim_col_map.get(d)
            if col and col in df0.columns:
                vals = sorted(set(
                    str(v) for src in [df0, df1]
                    for v in src[col].dropna() if str(v).strip()
                ))
                bubble_filter_meta[d] = vals
        # TENOR (MATURITY_BUCKET) filtre boyutu — numerik sıralı; DIM_BUCKET üzerinden.
        # Yalnız include_tenor True iken panele eklenir (Outstanding Cost Analysis =
        # ca-mon/ddd). Aynı build_waterfalls'ı paylaşan legacy Deposit Detail (dd)
        # sekmesine SIZMAZ (o çağrı tenor_filter göndermez).
        if include_tenor and "DIM_BUCKET" in df0.columns:
            _bk = sorted(
                {str(v) for src in [df0, df1] for v in src["DIM_BUCKET"].dropna() if str(v).strip()},
                key=TenorAnalysisEngine._bucket_lower,
            )
            if _bk:
                bubble_filter_meta["MATURITY_BUCKET"] = _bk
        # Bubble kaynağı: (TÜM boyutlar × DIM_BUCKET) ince granülerlik — aktif
        # "Dimensions" seçiminden BAĞIMSIZ. Frontend _aggregateBubbles activeDims
        # ile ekran gruplamasına geri toplar; ince kaynakta tüm boyutların
        # bulunması hem TENOR filtresini hem de per-bubble SPLIT'i (tek-tık seçim
        # + Enter: kapalı bir boyuta göre bölme, ör. KASA_T → AUM) mümkün kılar.
        # df0/df1 HÂLÂ ham snapshot (gruplama AŞAĞIDA); helper ham df ister.
        m_bub, bubble_product_dims = _cost_bubble_source(df0, df1, list(cls.DIMENSIONS), _dim_col_map)
        df0 = cls._group_by_dims(df0, dims)
        df1 = cls._group_by_dims(df1, dims)

        tot_b0 = float(df0["BALANCE"].sum())
        tot_b1 = float(df1["BALANCE"].sum())
        wavg_r0 = _wavg(df0["INTEREST_RATE"], df0["BALANCE"])
        wavg_r1 = _wavg(df1["INTEREST_RATE"], df1["BALANCE"])

        m = (
            df0[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
            .rename(columns={"BALANCE": "b0", "INTEREST_RATE": "r0"})
            .merge(
                df1[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
                .rename(columns={"BALANCE": "b1", "INTEREST_RATE": "r1"}),
                on="PRODUCT", how="outer",
            )
            .fillna(0.0)
        )
        m["w0"] = m["b0"] / tot_b0 if tot_b0 > 0 else 0.0
        m["w1"] = m["b1"] / tot_b1 if tot_b1 > 0 else 0.0
        m["dw"] = m["w1"] - m["w0"]
        m["w_avg"] = (m["w0"] + m["w1"]) / 2.0
        m["r_avg"] = (m["r0"] + m["r1"]) / 2.0
        # Bennet (symmetric) decomposition — interaction term split evenly between Mix & Pricing
        m["mix_eff"]   = m["dw"]    * m["r_avg"]
        m["price_eff"] = m["w_avg"] * (m["r1"] - m["r0"])
        mix_total   = float(m["mix_eff"].sum())
        price_total = float(m["price_eff"].sum())

        start_bps = _bps(wavg_r0)
        end_bps   = _bps(wavg_r1)
        mix_bps   = float(mix_total   * 10000.0)
        price_bps = float(price_total * 10000.0)
        y_floor   = min(start_bps, end_bps) - 30

        # ── WF1: Mix vs Pricing ───────────────────────────────────────────────
        chart1 = NIMChartBuilder._wf_data(
            "TRY Deposit Rate Waterfall (bps): Mix vs Pricing",
            ["Start Rate", "Mix / Interaction", "Pricing (rate, detailed)", "End Rate"],
            [start_bps, mix_bps, price_bps, end_bps],
            ["absolute", "relative", "relative", "total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
        )

        # ── WF2: Pricing Drivers ──────────────────────────────────────────────
        det2 = m.copy()
        det2["price_eff_bps"] = det2["price_eff"] * 10000.0
        det2 = det2.sort_values("price_eff_bps", key=lambda s: s.abs(), ascending=False)
        top2  = det2.head(top_n)
        other2 = det2.iloc[top_n:]
        other_sum2 = float(other2["price_eff_bps"].sum())
        baseline2  = start_bps + int(round(mix_bps))
        wf2_meta = (
            [None]
            + [{"rate_t0": round(float(r["r0"]), 6), "rate_t1": round(float(r["r1"]), 6)}
               for _, r in top2.iterrows()]
            + [None, None]
        )
        chart2 = NIMChartBuilder._wf_data(
            f"TRY Deposit Pricing Drivers (Top {len(top2)} + Other, bps)",
            ["After Mix"] + top2["PRODUCT"].tolist() + ["Other Items", "End Rate"],
            [baseline2] + [int(round(v)) for v in top2["price_eff_bps"]] + [int(round(other_sum2))] + [end_bps],
            ["absolute"] + ["relative"] * len(top2) + ["relative"] + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
            meta=wf2_meta,
        )

        # ── WF4: Mix Drivers (Bennet) ─────────────────────────────────────────
        # dw × (r̄ − w̄avg) where r̄ = (r0+r1)/2, w̄avg = (wavg_r0+wavg_r1)/2
        # Σ(dw × (r̄ − w̄avg)) = Σ(dw × r̄) − w̄avg × Σ(dw) = mix_total (Σ(dw)=0)
        wavg_r_avg = (wavg_r0 + wavg_r1) / 2.0
        det4 = m.copy()
        det4["mix_eff_bps"] = (det4["dw"] * (det4["r_avg"] - wavg_r_avg)) * 10000.0
        det4 = det4.sort_values("mix_eff_bps", key=lambda s: s.abs(), ascending=False)
        top4   = det4.head(top_n)
        other4 = det4.iloc[top_n:]
        other_sum4 = float(other4["mix_eff_bps"].sum())
        other_dw4  = float(other4["dw"].sum())
        chart4 = NIMChartBuilder._wf_data(
            f"TRY Deposit Mix Drivers (Top {len(top4)} + Other, bps)",
            ["Start Rate"] + top4["PRODUCT"].tolist() + ["Other Items", "After Mix"],
            [start_bps] + [int(round(v)) for v in top4["mix_eff_bps"]] + [int(round(other_sum4))] + [start_bps + int(round(mix_bps))],
            ["absolute"] + ["relative"] * len(top4) + ["relative"] + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
        )

        # ── WF3: Weight Changes bar chart ─────────────────────────────────────
        def _fmt(v):
            return round(float(v) * 10000, 2) if v is not None and np.isfinite(float(v)) else None

        wavg_avg_bps_val = _fmt(wavg_r_avg)
        weight_rows = []
        for _, r in top4.iterrows():
            weight_rows.append({
                "label":            r["PRODUCT"],
                "dw_pct":           float(r["dw"]) * 100.0,
                "rate_avg_bps":     _fmt(r.get("r_avg")),
                "wavg_avg_bps":     wavg_avg_bps_val,
                "rate_1_bps":       None,
                "bench_rate_1_bps": None,
                "bs_type":          None,
            })
        if np.isfinite(other_dw4):
            weight_rows.append({
                "label": "Other Items", "dw_pct": float(other_dw4) * 100.0,
                "rate_avg_bps": None,
                "wavg_avg_bps": wavg_avg_bps_val,
                "rate_1_bps": None,
                "bench_rate_1_bps": None, "bs_type": None,
            })
        weight_df = pd.DataFrame(weight_rows)
        if weight_df.empty:
            dw_vals = np.array([-1.0, 1.0])
        else:
            dw_vals = weight_df["dw_pct"].astype(float).to_numpy()
        vmin, vmax = float(np.nanmin(dw_vals)), float(np.nanmax(dw_vals))
        span = max(vmax - vmin, 0.5)
        pad  = max(0.25, 0.25 * span)
        prod_labels   = weight_df["label"].tolist()   if not weight_df.empty else []
        prod_values   = [round(v, 4) for v in weight_df["dw_pct"].tolist()] if not weight_df.empty else []
        _tip_cols = ["rate_avg_bps", "wavg_avg_bps", "rate_1_bps", "bench_rate_1_bps", "bs_type"]
        prod_tooltips = (
            weight_df[_tip_cols]
            .where(weight_df[_tip_cols].notna(), other=None)
            .to_dict("records")
        ) if not weight_df.empty else []
        chart3 = {
            "type": "bar",
            "title": f"TRY Deposit Weight Changes (Top {len(top4)} + Other)",
            "yaxis_title": "Δ Weight (%)",
            "categories": ["Start Rate"] + prod_labels + ["After Mix"],
            "values":     [0.0] + prod_values + [0.0],
            "y_range": [round(vmin - pad, 4), round(vmax + pad, 4)],
            "tooltips": [None] + prod_tooltips + [None],
        }

        # ── WF2 Companion: Balance Growth bar ────────────────────────────────
        # Same X-axis categories as WF2 so bars align perfectly.
        # Anchor bars ("After Mix", "End Rate") → None (no bar rendered).
        bal_cats = ["After Mix"] + top2["PRODUCT"].tolist() + ["Other Items", "End Rate"]
        bal_vals: List[Optional[float]] = [None]
        bal_tips: List[Optional[dict]] = [None]
        SCALE = 1e6  # display in ₺M
        for _, r in top2.iterrows():
            delta = float(r["b1"]) - float(r["b0"])
            pct   = (delta / float(r["b0"]) * 100.0) if float(r["b0"]) != 0 else None
            bal_vals.append(round(delta / SCALE, 2))
            bal_tips.append({"nominal_m": round(delta / SCALE, 2), "pct": round(pct, 2) if pct is not None else None,
                              "b0_m": round(float(r["b0"]) / SCALE, 2), "b1_m": round(float(r["b1"]) / SCALE, 2)})
        # "Other Items" balance delta
        other_b0 = float(other2["b0"].sum())
        other_b1 = float(other2["b1"].sum())
        other_delta = other_b1 - other_b0
        other_pct = (other_delta / other_b0 * 100.0) if other_b0 != 0 else None
        bal_vals.append(round(other_delta / SCALE, 2))
        bal_tips.append({"nominal_m": round(other_delta / SCALE, 2),
                          "pct": round(other_pct, 2) if other_pct is not None else None,
                          "b0_m": round(other_b0 / SCALE, 2), "b1_m": round(other_b1 / SCALE, 2)})
        bal_vals.append(None)
        bal_tips.append(None)
        non_null = [v for v in bal_vals if v is not None]
        b_min = min(non_null) if non_null else -1.0
        b_max = max(non_null) if non_null else 1.0
        b_span = max(b_max - b_min, 1.0)
        b_pad  = max(0.5, 0.15 * b_span)
        chart_bg = {
            "type": "bar-growth",
            "title": "TRY Deposit Balance Growth (₺M)",
            "yaxis_title": "Δ Balance (₺M)",
            "categories": bal_cats,
            "values": bal_vals,
            "tooltips": bal_tips,
            "y_range": [round(b_min - b_pad, 1), round(b_max + b_pad, 1)],
        }

        dep_info = {
            "rate_start":        round(wavg_r0 * 100, 4),
            "rate_end":          round(wavg_r1 * 100, 4),
            "rate_change_bps":   _bps(wavg_r1 - wavg_r0),
            "total_balance_t0":  round(tot_b0, 0),
            "total_balance_t1":  round(tot_b1, 0),
        }
        bubble_bal, bubble_rate = _build_bubble_charts(m_bub)
        rate_heatmap = _rate_heatmap_seg_aum(raw_df0_hm, raw_df1_hm)
        return dep_info, chart1, chart2, chart3, chart4, chart_bg, bubble_bal, bubble_rate, bubble_filter_meta, bubble_product_dims, rate_heatmap


# =============================================================================
# Daily Deposit Engine
# =============================================================================

# ── app.py 1725-2106 ──
_DAILY_DD_CACHE: Dict[str, object] = {}


class DailyDepositEngine:
    """Daily-granularity counterpart of DepositDetailEngine.

    Source: daily_deposit.xlsx
        DAT, GRUP_KEY, TYPE2, CUST_TP, AUM_TYPE, SEGMENT,
        GUNLUK_TRY_BAKIYE, AGIRLIKLI_ORT_FAIZ
    AGIRLIKLI_ORT_FAIZ stored as percent (e.g. 4.485) — converted to decimal.
    Same waterfall output schema as DepositDetailEngine; adds SEGMENT as a
    fourth dimension and lets the user pick arbitrary daily date pairs.
    """

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }

    @classmethod
    def _load(cls):
        if "df" not in _DAILY_DD_CACHE:
            df = load_dataframe("daily_deposit")
            # See DepositDetailEngine._load() — keep dayfirst off so SQLite ISO
            # strings parse correctly.
            df["DAT"] = pd.to_datetime(df["DAT"], errors="coerce")
            df["AGIRLIKLI_ORT_FAIZ"] = df["AGIRLIKLI_ORT_FAIZ"].astype(float) / 100.0
            df["BALANCE"] = df["GUNLUK_TRY_BAKIYE"].astype(float)
            df["INTEREST_RATE"] = df["AGIRLIKLI_ORT_FAIZ"]
            # Müşteri adedi (CUSTOMER_NUMBER) — kolon yoksa/boşsa SESSİZCE 0 basma (#9).
            if "CUSTOMER_NUMBER" in df.columns:
                df["CUST_COUNT"] = pd.to_numeric(df["CUSTOMER_NUMBER"], errors="coerce").fillna(0.0)
                _cc_nz = int((df["CUST_COUNT"] > 0).sum())
                if _cc_nz == 0:
                    log.warning("daily_deposit: CUSTOMER_NUMBER kolonu geldi ama TÜM değerler "
                          "0/NULL — Customer Number Heatmap (daily) sıfır gösterecek.")
                else:
                    log.info(f"daily_deposit: CUSTOMER_NUMBER OK "
                          f"({_cc_nz:,}/{len(df):,} satır > 0).")
            else:
                df["CUST_COUNT"] = 0.0
                log.warning(f"daily_deposit: CUSTOMER_NUMBER kolonu YOK (ENV={_ENV}) — "
                      "Customer Number Heatmap (daily) sıfır gösterecek. "
                      "PRODUCTION_DB isen queries/prod/daily_deposit.sql güncel mi "
                      "(SUM(CUSTOMER_NUMBER) içeriyor mu) + Flask restart edildi mi? "
                      "PRODUCTION_EXC (Excel) bu kolonu desteklemez.")
            df["DIM_PRODUCT"]  = df["TYPE2"].astype(str)
            # SUB_PRODUCT: O/N alt-ürünü (KGH/BTH/Other O/N); yoksa TYPE2.
            df["DIM_SUBPRODUCT"] = (df["SUB_PRODUCT"].astype(str)
                                    if "SUB_PRODUCT" in df.columns else df["DIM_PRODUCT"])
            df["DIM_CUSTOMER"] = df["CUST_TP"].astype(str)
            df["DIM_AUM"]      = df["AUM_TYPE"].astype(str)
            df["DIM_SEGMENT"]  = df["SEGMENT"].astype(str)
            if "VADE_BUCKET" in df.columns:
                df["VADE_BUCKET"] = df["VADE_BUCKET"].fillna("").astype(str).replace("None", "").replace("nan", "")
            else:
                df["VADE_BUCKET"] = ""
            # Mirror DIM_BUCKET name used by TenorAnalysisEngine for symmetry with
            # the monthly engine's DIM_BUCKET column.
            df["DIM_BUCKET"] = df["VADE_BUCKET"]
            # KALAN_VADE_BUCKET — vadeye KALAN güne göre bucket (DTM modu).
            if "KALAN_VADE_BUCKET" in df.columns:
                df["KALAN_VADE_BUCKET"] = (df["KALAN_VADE_BUCKET"].fillna("").astype(str)
                                           .replace("None", "").replace("nan", ""))
            else:
                df["KALAN_VADE_BUCKET"] = ""
            df["DIM_BUCKET_DTM"] = df["KALAN_VADE_BUCKET"]
            if "AGIRLIKLI_ORT_TENOR" in df.columns:
                df["AGIRLIKLI_ORT_TENOR"] = pd.to_numeric(df["AGIRLIKLI_ORT_TENOR"], errors="coerce")
            if "AGIRLIKLI_ORT_DTM" in df.columns:
                df["AGIRLIKLI_ORT_DTM"] = pd.to_numeric(df["AGIRLIKLI_ORT_DTM"], errors="coerce")
            # Drop weekends (dayofweek: 0=Mon … 4=Fri, 5=Sat, 6=Sun)
            df = df[df["DAT"].dt.dayofweek < 5].copy()
            _DAILY_DD_CACHE["df"] = df
            _DAILY_DD_CACHE["dates"] = [
                _date_str(pd.Timestamp(d))
                for d in sorted(df["DAT"].dropna().unique())
            ]
        return _DAILY_DD_CACHE["df"], _DAILY_DD_CACHE["dates"]

    @classmethod
    def _group_by_dims(cls, df_day: pd.DataFrame, dims: List[str]) -> pd.DataFrame:
        cols = [cls._DIM_COL[d] for d in cls.DIMENSIONS if d in dims]
        g = df_day.copy()
        g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]
        agg = g.groupby(cols, dropna=False)[["BALANCE", "_wr"]].sum().reset_index()
        agg["INTEREST_RATE"] = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"], 0.0)
        agg["PRODUCT"] = (
            agg[cols].astype(str)
            .apply(lambda r: "_".join(v for v in r if v), axis=1)
        )
        return agg[["PRODUCT", "BALANCE", "INTEREST_RATE"] + cols]

    @classmethod
    def get_dates(cls) -> List[str]:
        _, dates = cls._load()
        return dates

    @classmethod
    def build_waterfalls(cls, date_0: str, date_1: str,
                         dims: Optional[List[str]] = None, top_n: int = 7,
                         include_tenor: bool = False,
                         rate_conv: str = "simple", demand_pct: float = 0.0) -> tuple:
        df, _ = cls._load()
        if dims is None:
            dims = list(cls.DIMENSIONS)
        dims = [d for d in cls.DIMENSIONS if d in dims]
        if not dims:
            raise ValueError("Select at least one dimension (PRODUCT / CUSTOMER_TYPE / AUM / SEGMENT).")
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        df0 = df[df["DAT"] == d0].copy()
        df1 = df[df["DAT"] == d1].copy()
        if df0.empty:
            raise ValueError(f"No daily_deposit records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No daily_deposit records for date_1={date_1}.")
        # VADESİZ (demand) etkisi rate_conv'dan ÖNCE — KGH/BTH bakiye ×(1+p),
        # simple oran ÷(1+p) (bkz. DepositDetailEngine muadili). demand_pct<=0 → no-op.
        if demand_pct and demand_pct > 0:
            df0 = _apply_demand_deposit(df0, demand_pct)
            df1 = _apply_demand_deposit(df1, demand_pct)
        # Rate Type dönüşümü (simple|compound|on): HAM satırda, satırın kendi
        # wavg vadesiyle — downstream her şey (waterfall/bubble/heatmap/KPI)
        # dönüşmüş oranı ağırlıklar. Tenor kolonu: monthly TENOR_RATE,
        # daily AGIRLIKLI_ORT_TENOR.
        if rate_conv in ("compound", "on"):
            _tcol = "TENOR_RATE" if "TENOR_RATE" in df0.columns else "AGIRLIKLI_ORT_TENOR"
            if _tcol in df0.columns:
                df0["INTEREST_RATE"] = _convert_rate_series(df0["INTEREST_RATE"], df0[_tcol], rate_conv)
                df1["INTEREST_RATE"] = _convert_rate_series(df1["INTEREST_RATE"], df1[_tcol], rate_conv)
        _has_seg_aum = "DIM_SEGMENT" in df0.columns and "DIM_AUM" in df0.columns
        raw_df0_hm = df0[["BALANCE", "INTEREST_RATE", "DIM_SEGMENT", "DIM_AUM"]].copy() if _has_seg_aum else None
        raw_df1_hm = df1[["BALANCE", "INTEREST_RATE", "DIM_SEGMENT", "DIM_AUM"]].copy() if _has_seg_aum else None
        _dim_col_map = cls._DIM_COL
        # Filtre paneli META'sı GRUPLAMADAN ÖNCE ve TÜM boyutlardan toplanır —
        # panel "Dimensions" seçiminden bağımsız SABİT kalır. Böylece heatmap
        # eksen-başlığı gruplama/filtre (Ctrl+tık → Enter) toggle'ı kapalı bir
        # boyutta da çalışır (heatmap/drill sorguları filter_/merges alır).
        # Bubble'lar taşımadığı dim'in filtresini frontend'de zaten yok sayar.
        bubble_filter_meta: Dict[str, List[str]] = {}
        for d in cls.DIMENSIONS:
            col = _dim_col_map.get(d)
            if col and col in df0.columns:
                vals = sorted(set(
                    str(v) for src in [df0, df1]
                    for v in src[col].dropna() if str(v).strip()
                ))
                bubble_filter_meta[d] = vals
        # TENOR (MATURITY_BUCKET) filtre boyutu — numerik sıralı; DIM_BUCKET üzerinden.
        # Yalnız include_tenor True iken panele eklenir (Outstanding Cost Analysis =
        # ca-mon/ddd). Aynı build_waterfalls'ı paylaşan legacy Deposit Detail (dd)
        # sekmesine SIZMAZ (o çağrı tenor_filter göndermez).
        if include_tenor and "DIM_BUCKET" in df0.columns:
            _bk = sorted(
                {str(v) for src in [df0, df1] for v in src["DIM_BUCKET"].dropna() if str(v).strip()},
                key=TenorAnalysisEngine._bucket_lower,
            )
            if _bk:
                bubble_filter_meta["MATURITY_BUCKET"] = _bk
        # Bubble kaynağı: (TÜM boyutlar × DIM_BUCKET) ince granülerlik — aktif
        # "Dimensions" seçiminden BAĞIMSIZ. Frontend _aggregateBubbles activeDims
        # ile ekran gruplamasına geri toplar; ince kaynakta tüm boyutların
        # bulunması hem TENOR filtresini hem de per-bubble SPLIT'i (tek-tık seçim
        # + Enter: kapalı bir boyuta göre bölme, ör. KASA_T → AUM) mümkün kılar.
        # df0/df1 HÂLÂ ham snapshot (gruplama AŞAĞIDA); helper ham df ister.
        m_bub, bubble_product_dims = _cost_bubble_source(df0, df1, list(cls.DIMENSIONS), _dim_col_map)
        df0 = cls._group_by_dims(df0, dims)
        df1 = cls._group_by_dims(df1, dims)

        tot_b0 = float(df0["BALANCE"].sum())
        tot_b1 = float(df1["BALANCE"].sum())
        wavg_r0 = _wavg(df0["INTEREST_RATE"], df0["BALANCE"])
        wavg_r1 = _wavg(df1["INTEREST_RATE"], df1["BALANCE"])

        m = (
            df0[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
            .rename(columns={"BALANCE": "b0", "INTEREST_RATE": "r0"})
            .merge(
                df1[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
                .rename(columns={"BALANCE": "b1", "INTEREST_RATE": "r1"}),
                on="PRODUCT", how="outer",
            )
            .fillna(0.0)
        )
        m["w0"] = m["b0"] / tot_b0 if tot_b0 > 0 else 0.0
        m["w1"] = m["b1"] / tot_b1 if tot_b1 > 0 else 0.0
        m["dw"] = m["w1"] - m["w0"]
        m["w_avg"] = (m["w0"] + m["w1"]) / 2.0
        m["r_avg"] = (m["r0"] + m["r1"]) / 2.0
        # Bennet (symmetric) decomposition — interaction term split evenly
        m["mix_eff"]   = m["dw"]    * m["r_avg"]
        m["price_eff"] = m["w_avg"] * (m["r1"] - m["r0"])
        mix_total   = float(m["mix_eff"].sum())
        price_total = float(m["price_eff"].sum())

        start_bps = _bps(wavg_r0)
        end_bps   = _bps(wavg_r1)
        mix_bps   = float(mix_total   * 10000.0)
        price_bps = float(price_total * 10000.0)
        y_floor   = min(start_bps, end_bps) - 30

        chart1 = NIMChartBuilder._wf_data(
            "Daily Deposit Rate Waterfall (bps): Mix vs Pricing",
            ["Start Rate", "Mix / Interaction", "Pricing (rate, detailed)", "End Rate"],
            [start_bps, mix_bps, price_bps, end_bps],
            ["absolute", "relative", "relative", "total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
        )

        det2 = m.copy()
        det2["price_eff_bps"] = det2["price_eff"] * 10000.0
        det2 = det2.sort_values("price_eff_bps", key=lambda s: s.abs(), ascending=False)
        top2  = det2.head(top_n)
        other2 = det2.iloc[top_n:]
        other_sum2 = float(other2["price_eff_bps"].sum())
        baseline2  = start_bps + int(round(mix_bps))
        wf2_meta = (
            [None]
            + [{"rate_t0": round(float(r["r0"]), 6), "rate_t1": round(float(r["r1"]), 6)}
               for _, r in top2.iterrows()]
            + [None, None]
        )
        chart2 = NIMChartBuilder._wf_data(
            f"Daily Deposit Pricing Drivers (Top {len(top2)} + Other, bps)",
            ["After Mix"] + top2["PRODUCT"].tolist() + ["Other Items", "End Rate"],
            [baseline2] + [int(round(v)) for v in top2["price_eff_bps"]] + [int(round(other_sum2))] + [end_bps],
            ["absolute"] + ["relative"] * len(top2) + ["relative"] + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
            meta=wf2_meta,
        )

        # WF4 — Mix Drivers (Bennet): dw × (r̄ − w̄avg) where r̄=(r0+r1)/2, w̄avg=(wavg_r0+wavg_r1)/2
        wavg_r_avg = (wavg_r0 + wavg_r1) / 2.0
        det4 = m.copy()
        det4["mix_eff_bps"] = (det4["dw"] * (det4["r_avg"] - wavg_r_avg)) * 10000.0
        det4 = det4.sort_values("mix_eff_bps", key=lambda s: s.abs(), ascending=False)
        top4   = det4.head(top_n)
        other4 = det4.iloc[top_n:]
        other_sum4 = float(other4["mix_eff_bps"].sum())
        other_dw4  = float(other4["dw"].sum())
        chart4 = NIMChartBuilder._wf_data(
            f"Daily Deposit Mix Drivers (Top {len(top4)} + Other, bps)",
            ["Start Rate"] + top4["PRODUCT"].tolist() + ["Other Items", "After Mix"],
            [start_bps] + [int(round(v)) for v in top4["mix_eff_bps"]] + [int(round(other_sum4))] + [start_bps + int(round(mix_bps))],
            ["absolute"] + ["relative"] * len(top4) + ["relative"] + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
        )

        def _fmt(v):
            return round(float(v) * 10000, 2) if v is not None and np.isfinite(float(v)) else None

        wavg_avg_bps_val = _fmt(wavg_r_avg)
        weight_rows = []
        for _, r in top4.iterrows():
            weight_rows.append({
                "label":            r["PRODUCT"],
                "dw_pct":           float(r["dw"]) * 100.0,
                "rate_avg_bps":     _fmt(r.get("r_avg")),
                "wavg_avg_bps":     wavg_avg_bps_val,
                "rate_1_bps":       None,
                "bench_rate_1_bps": None,
                "bs_type":          None,
            })
        if np.isfinite(other_dw4):
            weight_rows.append({
                "label": "Other Items", "dw_pct": float(other_dw4) * 100.0,
                "rate_avg_bps": None,
                "wavg_avg_bps": wavg_avg_bps_val,
                "rate_1_bps": None,
                "bench_rate_1_bps": None, "bs_type": None,
            })
        weight_df = pd.DataFrame(weight_rows)
        if weight_df.empty:
            dw_vals = np.array([-1.0, 1.0])
        else:
            dw_vals = weight_df["dw_pct"].astype(float).to_numpy()
        vmin, vmax = float(np.nanmin(dw_vals)), float(np.nanmax(dw_vals))
        span = max(vmax - vmin, 0.5)
        pad  = max(0.25, 0.25 * span)
        prod_labels   = weight_df["label"].tolist()   if not weight_df.empty else []
        prod_values   = [round(v, 4) for v in weight_df["dw_pct"].tolist()] if not weight_df.empty else []
        _tip_cols = ["rate_avg_bps", "wavg_avg_bps", "rate_1_bps", "bench_rate_1_bps", "bs_type"]
        prod_tooltips = (
            weight_df[_tip_cols]
            .where(weight_df[_tip_cols].notna(), other=None)
            .to_dict("records")
        ) if not weight_df.empty else []
        chart3 = {
            "type": "bar",
            "title": f"Daily Deposit Weight Changes (Top {len(top4)} + Other)",
            "yaxis_title": "Δ Weight (%)",
            "categories": ["Start Rate"] + prod_labels + ["After Mix"],
            "values":     [0.0] + prod_values + [0.0],
            "y_range": [round(vmin - pad, 4), round(vmax + pad, 4)],
            "tooltips": [None] + prod_tooltips + [None],
        }

        # WF2 companion: balance growth bar (same X axis as WF2)
        bal_cats = ["After Mix"] + top2["PRODUCT"].tolist() + ["Other Items", "End Rate"]
        bal_vals: List[Optional[float]] = [None]
        bal_tips: List[Optional[dict]] = [None]
        SCALE = 1e6  # ₺M
        for _, r in top2.iterrows():
            delta = float(r["b1"]) - float(r["b0"])
            pct   = (delta / float(r["b0"]) * 100.0) if float(r["b0"]) != 0 else None
            bal_vals.append(round(delta / SCALE, 2))
            bal_tips.append({"nominal_m": round(delta / SCALE, 2), "pct": round(pct, 2) if pct is not None else None,
                              "b0_m": round(float(r["b0"]) / SCALE, 2), "b1_m": round(float(r["b1"]) / SCALE, 2)})
        other_b0 = float(other2["b0"].sum())
        other_b1 = float(other2["b1"].sum())
        other_delta = other_b1 - other_b0
        other_pct = (other_delta / other_b0 * 100.0) if other_b0 != 0 else None
        bal_vals.append(round(other_delta / SCALE, 2))
        bal_tips.append({"nominal_m": round(other_delta / SCALE, 2),
                          "pct": round(other_pct, 2) if other_pct is not None else None,
                          "b0_m": round(other_b0 / SCALE, 2), "b1_m": round(other_b1 / SCALE, 2)})
        bal_vals.append(None)
        bal_tips.append(None)
        non_null = [v for v in bal_vals if v is not None]
        b_min = min(non_null) if non_null else -1.0
        b_max = max(non_null) if non_null else 1.0
        b_span = max(b_max - b_min, 1.0)
        b_pad  = max(0.5, 0.15 * b_span)
        chart_bg = {
            "type": "bar-growth",
            "title": "Daily Deposit Balance Growth (₺M)",
            "yaxis_title": "Δ Balance (₺M)",
            "categories": bal_cats,
            "values": bal_vals,
            "tooltips": bal_tips,
            "y_range": [round(b_min - b_pad, 1), round(b_max + b_pad, 1)],
        }

        dep_info = {
            "rate_start":        round(wavg_r0 * 100, 4),
            "rate_end":          round(wavg_r1 * 100, 4),
            "rate_change_bps":   _bps(wavg_r1 - wavg_r0),
            "total_balance_t0":  round(tot_b0, 0),
            "total_balance_t1":  round(tot_b1, 0),
        }
        bubble_bal, bubble_rate = _build_bubble_charts(m_bub)
        rate_heatmap = _rate_heatmap_seg_aum(raw_df0_hm, raw_df1_hm)
        return dep_info, chart1, chart2, chart3, chart4, chart_bg, bubble_bal, bubble_rate, bubble_filter_meta, bubble_product_dims, rate_heatmap


# =============================================================================
# Tenor Analysis Engine — maturity-bucket-centric analysis
# =============================================================================
# Reuses DepositDetailEngine._load() (monthly) and DailyDepositEngine._load() (daily)
# caches — no extra disk reads. Filters by the same DIMENSIONS as the Cost
# Analysis tab (PRODUCT / CUSTOMER_TYPE / AUM / SEGMENT) and then aggregates by
# the maturity bucket dimension (DIM_BUCKET / VADE_BUCKET).
#
# Output shape (single dict, JSON-serialisable):
#   {
#     "buckets":          ["0-30", "30-90", ...]            sorted by lower bound
#     "balance_t0_m":     [...]                              ₺M per bucket
#     "balance_t1_m":     [...]
#     "rate_t0_pct":      [...]                              weighted-avg, %
#     "rate_t1_pct":      [...]
#     "weight_t0_pct":    [...]                              composition, %
#     "weight_t1_pct":    [...]
#     "waterfall":        {NIMChartBuilder._wf_data payload, bucket-level mix vs price}
#     "wat":              {"t0": <days>, "t1": <days>, "delta": <days>}
#     "totals":           {"balance_t0_m", "balance_t1_m",
#                          "rate_t0_pct",  "rate_t1_pct",
#                          "dropped_pct":  <share of total balance dropped (no bucket)>}
#   }
# For the Daily endpoint an extra block is appended:
#   "daily_evolution":    {
#       "dates":          [...]                              ISO day strings
#       "buckets":        [...]                              same set as snapshot
#       "balance_m":      {bucket: [.., .., ..]}            stacked area data
#       "rate_pct":       {bucket: [.., .., ..]}            per-bucket rate evolution
#       "wat_series":     [...]                              overall WAT per day
#   }

# ── app.py 2107-2348 ──
class TenorAnalysisEngine:
    """Maturity-bucket aggregation on the monthly deposit dataset."""

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }
    DROP_WARNING_PCT = 5.0  # surface a UI warning when dropped balance > this %

    @staticmethod
    def _bucket_lower(b: str) -> int:
        """Parse '0-30' / '30-90' → 0 / 30 for numeric sort. Empty → very large."""
        if not b:
            return 10**9
        try:
            return int(str(b).split("-", 1)[0])
        except (ValueError, IndexError):
            return 10**9

    @classmethod
    def _filter_by_dims(cls, df: pd.DataFrame, dim_filters: Dict[str, List[str]]) -> pd.DataFrame:
        """Restrict df to rows whose dimension values match the user's filter set.

        dim_filters is {dimension_name: [allowed_values]}. An empty/missing list
        means "all values for that dimension" (no constraint).
        MATURITY_BUCKET key filters on DIM_BUCKET directly.
        """
        if not dim_filters:
            return df
        out = df
        for d, vals in dim_filters.items():
            if d == "MATURITY_BUCKET":
                if vals and "DIM_BUCKET" in out.columns:
                    out = out[out["DIM_BUCKET"].astype(str).isin([str(v) for v in vals])]
                continue
            col = cls._DIM_COL.get(d)
            if not col or not vals or col not in out.columns:
                continue
            out = out[out[col].astype(str).isin([str(v) for v in vals])]
        return out

    @classmethod
    def _bucket_aggregate(cls, df_snap: pd.DataFrame) -> tuple:
        """Aggregate a single-date snapshot by DIM_BUCKET.

        Returns (buckets_sorted, df_bucket, dropped_share).
        Rows with empty DIM_BUCKET (non-Vadeli/Kasa/O/N) are dropped — tenor
        analysis is only meaningful for bucketed products.
        """
        total = float(df_snap["BALANCE"].sum())
        bucketed = df_snap[df_snap["DIM_BUCKET"].astype(str).str.len() > 0].copy()
        dropped = total - float(bucketed["BALANCE"].sum())
        dropped_share = (dropped / total * 100.0) if total > 0 else 0.0
        if bucketed.empty:
            return [], pd.DataFrame(columns=["DIM_BUCKET", "BALANCE", "INTEREST_RATE"]), dropped_share
        bucketed["_wr"] = bucketed["BALANCE"] * bucketed["INTEREST_RATE"]
        if "TENOR_RATE" in bucketed.columns:
            bucketed["_wt"] = bucketed["BALANCE"] * pd.to_numeric(bucketed["TENOR_RATE"], errors="coerce").fillna(0.0)
        elif "AGIRLIKLI_ORT_TENOR" in bucketed.columns:
            bucketed["_wt"] = bucketed["BALANCE"] * pd.to_numeric(bucketed["AGIRLIKLI_ORT_TENOR"], errors="coerce").fillna(0.0)
        else:
            bucketed["_wt"] = 0.0
        agg = (
            bucketed.groupby("DIM_BUCKET", dropna=False)[["BALANCE", "_wr", "_wt"]]
            .sum()
            .reset_index()
        )
        agg["INTEREST_RATE"] = np.where(agg["BALANCE"] != 0, agg["_wr"] / agg["BALANCE"], 0.0)
        agg["TENOR_DAYS"]    = np.where(agg["BALANCE"] != 0, agg["_wt"] / agg["BALANCE"], 0.0)
        buckets = sorted(agg["DIM_BUCKET"].astype(str).unique(), key=cls._bucket_lower)
        return buckets, agg, dropped_share

    @classmethod
    def build_snapshot(cls, date_0: str, date_1: str,
                       dim_filters: Optional[Dict[str, List[str]]] = None,
                       mode: str = "tenor",
                       merges: Optional[Dict[str, List[Dict]]] = None) -> Dict:
        df, _ = DepositDetailEngine._load()
        # Aylık kaynak artık KALAN_VADE_BUCKET → DIM_BUCKET_DTM taşıyor (güncel
        # TRY_DEPOSIT_DETAIL.sql). Varsa DTM modu gerçek kalan-vade kovalarıyla
        # kırılım yapar; yoksa (eski veri) uyarı notu düşülür.
        _has_dtm_buckets = ("DIM_BUCKET_DTM" in df.columns
                            and df["DIM_BUCKET_DTM"].astype(str).str.len().gt(0).any())
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        df0 = _apply_tenor_mode(df[df["MONTH"] == d0].copy(), mode)
        df1 = _apply_tenor_mode(df[df["MONTH"] == d1].copy(), mode)
        if df0.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_1={date_1}.")
        df0 = cls._filter_by_dims(df0, dim_filters or {})
        df1 = cls._filter_by_dims(df1, dim_filters or {})
        # Gruplama (merge) — filtre sonrası bucket/dim değerlerini grup adına
        # relabel et ki grafiklerde birleşik bucket görünsün (mode sonrası,
        # çünkü DIM_BUCKET aktif moda göre set edilir).
        df0 = _apply_balance_merges(df0, merges or {})
        df1 = _apply_balance_merges(df1, merges or {})
        payload = _build_tenor_payload(df0, df1)
        payload["mode"] = mode
        if mode == "dtm" and not _has_dtm_buckets:
            # Eski/kalan-vade kolonu olmayan kaynak: bucket kırılımı orijinal vade
            # kovalarıyla gösterilir; yalnız Weighted Avg gün metriği (DTM_RATE)
            # kalan vadeyi yansıtır. Güncel TRY_DEPOSIT_DETAIL.sql'de KALAN_VADE_BUCKET
            # geldiğinden bu not artık normalde görünmez.
            payload["mode_note"] = (
                "DTM mode (monthly): this source has no remaining-maturity bucket info; "
                "the bucket breakdown uses original-tenor buckets, only the "
                "Weighted Avg days metric is computed on remaining maturity (DTM_RATE).")
        # Swap hedge overlay — Monthly Averages'ta da gösterilir: her snapshot
        # ayının GÜNLÜK yaşayan notional ortalaması (mevduatın aylık ortalama
        # bakiyesiyle tutarlı). Alan adları daily ile aynı → renderer paylaşımlı.
        buckets = payload.get("buckets") or []
        if buckets:
            h0_amt, h0_ten, h0_rate = SwapHedgeEngine.hedge_by_bucket_month(d0, mode, buckets)
            h1_amt, h1_ten, h1_rate = SwapHedgeEngine.hedge_by_bucket_month(d1, mode, buckets)
            payload["hedge_t0_m"] = h0_amt
            payload["hedge_t1_m"] = h1_amt
            payload["hedge_t0_tenor"] = h0_ten
            payload["hedge_t1_tenor"] = h1_ten
            payload["hedge_t0_rate"] = h0_rate
            payload["hedge_t1_rate"] = h1_rate
        return payload

    @classmethod
    def get_filter_meta(cls) -> Dict[str, List[str]]:
        df, _ = DepositDetailEngine._load()
        meta: Dict[str, List[str]] = {}
        for d, col in cls._DIM_COL.items():
            if col in df.columns:
                vals = sorted(set(
                    str(v) for v in df[col].dropna() if str(v).strip()
                ))
                meta[d] = vals
        # Include maturity buckets as a filter dimension
        if "DIM_BUCKET" in df.columns:
            buckets = sorted(
                {str(v) for v in df["DIM_BUCKET"].dropna() if str(v).strip()},
                key=cls._bucket_lower,
            )
            if buckets:
                meta["MATURITY_BUCKET"] = buckets
        return meta


class DailyTenorEngine:
    """Daily counterpart of TenorAnalysisEngine."""

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }
    DROP_WARNING_PCT = 5.0

    @classmethod
    def _filter_by_dims(cls, df: pd.DataFrame, dim_filters: Dict[str, List[str]]) -> pd.DataFrame:
        if not dim_filters:
            return df
        out = df
        for d, vals in dim_filters.items():
            if d == "MATURITY_BUCKET":
                if vals and "DIM_BUCKET" in out.columns:
                    out = out[out["DIM_BUCKET"].astype(str).isin([str(v) for v in vals])]
                continue
            col = cls._DIM_COL.get(d)
            if not col or not vals or col not in out.columns:
                continue
            out = out[out[col].astype(str).isin([str(v) for v in vals])]
        return out

    @classmethod
    def build_snapshot(cls, date_0: str, date_1: str,
                       dim_filters: Optional[Dict[str, List[str]]] = None,
                       mode: str = "tenor",
                       merges: Optional[Dict[str, List[Dict]]] = None) -> Dict:
        df, _ = DailyDepositEngine._load()
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        df0 = _apply_tenor_mode(df[df["DAT"] == d0].copy(), mode)
        df1 = _apply_tenor_mode(df[df["DAT"] == d1].copy(), mode)
        if df0.empty:
            raise ValueError(f"No daily_deposit records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No daily_deposit records for date_1={date_1}.")
        df0 = cls._filter_by_dims(df0, dim_filters or {})
        df1 = cls._filter_by_dims(df1, dim_filters or {})
        df0 = _apply_balance_merges(df0, merges or {})
        df1 = _apply_balance_merges(df1, merges or {})
        payload = _build_tenor_payload(df0, df1)
        payload["mode"] = mode
        # Time-series block: aggregate by (DAT, DIM_BUCKET) over the whole range.
        df_range = _apply_tenor_mode(df[(df["DAT"] >= d0) & (df["DAT"] <= d1)].copy(), mode)
        df_range = cls._filter_by_dims(df_range, dim_filters or {})
        df_range = _apply_balance_merges(df_range, merges or {})
        payload["daily_evolution"] = _build_tenor_daily_evolution(df_range)
        # Swap hedge overlay — yaşayan "HEDGE UZUN VADELİ TRY MEVDUAT" swap'ları
        # her snapshot (t0/t1) için aynı bucket'lara hizalanır (Maturity Ladder'da
        # mevduatın altında negatif bar). Yalnız Daily Evolution tab'ine özgü.
        buckets = payload.get("buckets") or []
        if buckets:
            h0_amt, h0_ten, h0_rate = SwapHedgeEngine.hedge_by_bucket(d0, mode, buckets)
            h1_amt, h1_ten, h1_rate = SwapHedgeEngine.hedge_by_bucket(d1, mode, buckets)
            payload["hedge_t0_m"] = h0_amt
            payload["hedge_t1_m"] = h1_amt
            payload["hedge_t0_tenor"] = h0_ten     # bucket bazında hedge wavg gün
            payload["hedge_t1_tenor"] = h1_ten
            payload["hedge_t0_rate"] = h0_rate     # wavg yıllık effective faiz (%)
            payload["hedge_t1_rate"] = h1_rate
        return payload

    @classmethod
    def get_filter_meta(cls) -> Dict[str, List[str]]:
        df, _ = DailyDepositEngine._load()
        meta: Dict[str, List[str]] = {}
        for d, col in cls._DIM_COL.items():
            if col in df.columns:
                vals = sorted(set(
                    str(v) for v in df[col].dropna() if str(v).strip()
                ))
                meta[d] = vals
        # Include maturity buckets as a filter dimension — TENOR ve DTM
        # modlarının bucket'larının BİRLEŞİMİ (panel mod değişiminde
        # yeniden çizilmeden her iki modda da çalışsın).
        bucket_vals = set()
        for col in ("DIM_BUCKET", "DIM_BUCKET_DTM"):
            if col in df.columns:
                bucket_vals |= {str(v) for v in df[col].dropna() if str(v).strip()}
        if bucket_vals:
            meta["MATURITY_BUCKET"] = sorted(bucket_vals, key=TenorAnalysisEngine._bucket_lower)
        return meta



# ── app.py 2349-2373 ──
def _apply_tenor_mode(df: pd.DataFrame, mode: str) -> pd.DataFrame:
    """TENOR ↔ DTM mod anahtarı — mode="dtm" ise bucket/vade kolonlarını kalan-
    vade muadilleriyle DEĞİŞTİRİR; downstream (payload builder, daily evolution,
    MATURITY_BUCKET filtresi) hep DIM_BUCKET / AGIRLIKLI_ORT_TENOR / TENOR_RATE
    okuduğundan başka hiçbir yer değişmez.

      DIM_BUCKET          ← DIM_BUCKET_DTM      (daily; KALAN_VADE_BUCKET)
      AGIRLIKLI_ORT_TENOR ← AGIRLIKLI_ORT_DTM   (daily)
      TENOR_RATE          ← DTM_RATE            (monthly)

    Kaynakta DTM kolonu yoksa ilgili kolon olduğu gibi kalır (aylık kaynakta
    kalan-vade bucket kırılımı yok — yalnız gün metriği değişir).
    """
    if mode != "dtm":
        return df
    df = df.copy()
    if "DIM_BUCKET_DTM" in df.columns:
        df["DIM_BUCKET"] = df["DIM_BUCKET_DTM"]
    if "AGIRLIKLI_ORT_DTM" in df.columns:
        df["AGIRLIKLI_ORT_TENOR"] = df["AGIRLIKLI_ORT_DTM"]
    if "DTM_RATE" in df.columns:
        df["TENOR_RATE"] = df["DTM_RATE"]
    return df



# ── app.py 2374-2581 ──
def _build_tenor_payload(df0: pd.DataFrame, df1: pd.DataFrame) -> Dict:
    """Shared snapshot builder used by both monthly and daily tenor engines."""
    buckets0, agg0, drop0 = TenorAnalysisEngine._bucket_aggregate(df0)
    buckets1, agg1, drop1 = TenorAnalysisEngine._bucket_aggregate(df1)
    buckets = sorted(set(buckets0) | set(buckets1), key=TenorAnalysisEngine._bucket_lower)
    a0 = agg0.set_index("DIM_BUCKET").reindex(buckets).fillna(0.0)
    a1 = agg1.set_index("DIM_BUCKET").reindex(buckets).fillna(0.0)

    SCALE = 1e6
    bal0_m = (a0["BALANCE"] / SCALE).round(2).tolist()
    bal1_m = (a1["BALANCE"] / SCALE).round(2).tolist()
    r0_pct = (a0["INTEREST_RATE"] * 100.0).round(4).tolist()
    r1_pct = (a1["INTEREST_RATE"] * 100.0).round(4).tolist()
    # Bucket bazında ağırlıklı ortalama vade (gün) — TENOR modunda orijinal vade,
    # DTM modunda kalan vade (kaynak _apply_tenor_mode ile değişir). Ladder hover'ı
    # bu değeri gösterir.
    ten0 = (a0["TENOR_DAYS"] if "TENOR_DAYS" in a0.columns else pd.Series(0.0, index=a0.index)).round(1).tolist()
    ten1 = (a1["TENOR_DAYS"] if "TENOR_DAYS" in a1.columns else pd.Series(0.0, index=a1.index)).round(1).tolist()

    tot_b0 = float(a0["BALANCE"].sum())
    tot_b1 = float(a1["BALANCE"].sum())
    w0_pct = [round((v / tot_b0 * 100.0) if tot_b0 > 0 else 0.0, 4) for v in a0["BALANCE"].tolist()]
    w1_pct = [round((v / tot_b1 * 100.0) if tot_b1 > 0 else 0.0, 4) for v in a1["BALANCE"].tolist()]

    def _safe(x: float) -> float:
        try:
            xf = float(x)
            return xf if np.isfinite(xf) else 0.0
        except (TypeError, ValueError):
            return 0.0
    wavg_r0 = _safe(_wavg(a0["INTEREST_RATE"], a0["BALANCE"])) if buckets else 0.0
    wavg_r1 = _safe(_wavg(a1["INTEREST_RATE"], a1["BALANCE"])) if buckets else 0.0
    wat0 = _safe(_wavg(a0["TENOR_DAYS"], a0["BALANCE"])) if buckets else 0.0
    wat1 = _safe(_wavg(a1["TENOR_DAYS"], a1["BALANCE"])) if buckets else 0.0

    # ── Bucket-level decomposition waterfall (3 slides) ─────────────────────
    start_bps = _bps(wavg_r0)
    end_bps   = _bps(wavg_r1)

    # Balance delta per bucket (₺M, signed)
    bal_delta_m = [round((b1 - b0) / SCALE, 2)
                   for b0, b1 in zip(a0["BALANCE"].tolist(), a1["BALANCE"].tolist())]

    if tot_b0 > 0 and tot_b1 > 0 and buckets:
        w0_arr = a0["BALANCE"].to_numpy() / tot_b0
        w1_arr = a1["BALANCE"].to_numpy() / tot_b1
        r0_dec = a0["INTEREST_RATE"].to_numpy()
        r1_dec = a1["INTEREST_RATE"].to_numpy()
        # Bennet (symmetric) decomposition
        w_avg_arr = (w0_arr + w1_arr) / 2.0
        r_avg_arr = (r0_dec + r1_dec) / 2.0
        mix_eff_arr   = (w1_arr - w0_arr) * r_avg_arr
        price_eff_arr = w_avg_arr * (r1_dec - r0_dec)
        mix_bps   = float(mix_eff_arr.sum()   * 10000.0)
        price_bps = float(price_eff_arr.sum() * 10000.0)
    else:
        w0_arr = w1_arr = r0_dec = r1_dec = np.array([])
        r_avg_arr = w_avg_arr = np.array([])
        mix_eff_arr = price_eff_arr = np.array([])
        mix_bps = price_bps = 0.0

    y_floor = min(start_bps, end_bps) - 30

    # WF1 — Summary
    wf1 = NIMChartBuilder._wf_data(
        "Bucket Rate Waterfall (bps): Mix vs Pricing",
        ["Start Rate", "Bucket Mix", "Bucket Pricing", "End Rate"],
        [start_bps, mix_bps, price_bps, end_bps],
        ["absolute", "relative", "relative", "total"],
        y_floor, y_min_span=20, y_pad_ratio=0.15,
    )

    # WF2 — Pricing Drivers (per bucket)
    wf2 = wf2_bg = None
    wf4 = None
    if buckets and len(price_eff_arr) > 0:
        price_bps_per_bucket = (price_eff_arr * 10000.0).tolist()
        baseline2 = start_bps + int(round(mix_bps))

        # Sort by absolute pricing effect
        order2 = sorted(range(len(buckets)),
                        key=lambda i: abs(price_bps_per_bucket[i]), reverse=True)
        cats2   = [buckets[i]                    for i in order2]
        vals2   = [int(round(price_bps_per_bucket[i])) for i in order2]
        # Per-bucket rate_t0/rate_t1 so the tooltip can show "Rate Change"
        # (same convention as the Cost Analysis Pricing Drivers waterfall).
        wf2_meta = (
            [None]
            + [{"rate_t0": round(float(r0_dec[i]), 6),
                "rate_t1": round(float(r1_dec[i]), 6)}
               for i in order2]
            + [None]
        )
        wf2 = NIMChartBuilder._wf_data(
            "Bucket Pricing Drivers (bps)",
            ["After Mix"] + cats2 + ["End Rate"],
            [baseline2]   + vals2 + [end_bps],
            ["absolute"]  + ["relative"] * len(cats2) + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
            meta=wf2_meta,
        )

        # WF2 companion: balance delta per bucket (same ordering as wf2)
        bal_cats2 = ["After Mix"] + cats2 + ["End Rate"]
        bal_vals2 = [None]
        bal_tips2 = [None]
        for i in order2:
            b0v = float(a0["BALANCE"].iloc[i]) / SCALE
            b1v = float(a1["BALANCE"].iloc[i]) / SCALE
            delta = b1v - b0v
            pct = (delta / b0v * 100.0) if b0v != 0 else None
            bal_vals2.append(round(delta, 2))
            bal_tips2.append({
                "nominal_m": round(delta, 2),
                "pct":       round(pct, 2) if pct is not None else None,
                "b0_m":      round(b0v, 2),
                "b1_m":      round(b1v, 2),
            })
        bal_vals2.append(None); bal_tips2.append(None)
        non_null2 = [v for v in bal_vals2 if v is not None]
        bmin2 = min(non_null2) if non_null2 else -1.0
        bmax2 = max(non_null2) if non_null2 else 1.0
        bspan2 = max(bmax2 - bmin2, 1.0)
        bpad2  = max(0.5, 0.15 * bspan2)
        wf2_bg = {
            "type": "bar-growth",
            "title": "Bucket Balance Δ (₺M)",
            "yaxis_title": "Δ Balance (₺M)",
            "categories": bal_cats2,
            "values":     bal_vals2,
            "tooltips":   bal_tips2,
            "y_range":    [round(bmin2 - bpad2, 1), round(bmax2 + bpad2, 1)],
        }

        # WF4 — Mix Drivers (Bennet): dw × (r̄ − w̄avg)
        # Σ(dw × (r̄ − w̄avg)) = Σ(dw × r̄) − w̄avg × Σ(dw) = mix_bps (Σ(dw)=0)
        dw_arr = (w1_arr - w0_arr)
        wavg_r_avg = (wavg_r0 + wavg_r1) / 2.0
        mix_bps_relative = (dw_arr * (r_avg_arr - wavg_r_avg) * 10000.0).tolist()
        order4 = sorted(range(len(buckets)),
                        key=lambda i: abs(mix_bps_relative[i]), reverse=True)
        cats4 = [buckets[i]                       for i in order4]
        vals4 = [int(round(mix_bps_relative[i]))  for i in order4]
        wf4 = NIMChartBuilder._wf_data(
            "Bucket Mix Drivers (bps)",
            ["Start Rate"] + cats4 + ["After Mix"],
            [start_bps]    + vals4 + [start_bps + int(round(mix_bps))],
            ["absolute"]   + ["relative"] * len(cats4) + ["total"],
            y_floor, y_min_span=20, y_pad_ratio=0.15,
        )

        # WF3 — Weight Changes companion bar (same ordering as wf4)
        dw_pct_ordered = [round(float(dw_arr[i]) * 100.0, 4) for i in order4]
        dw_min = min(dw_pct_ordered) if dw_pct_ordered else -1.0
        dw_max = max(dw_pct_ordered) if dw_pct_ordered else 1.0
        dw_span = max(dw_max - dw_min, 0.5)
        dw_pad  = max(0.25, 0.25 * dw_span)
        wavg_avg_bps_val = round(float(wavg_r_avg) * 10000.0, 2) if np.isfinite(float(wavg_r_avg)) else None
        wf3_tooltips = [
            {
                "rate_avg_bps":  round(float(r_avg_arr[i]) * 10000.0, 2) if np.isfinite(float(r_avg_arr[i])) else None,
                "wavg_avg_bps": wavg_avg_bps_val,
            }
            for i in order4
        ]
        wf3 = {
            "type": "bar",
            "title": "Bucket Weight Changes",
            "yaxis_title": "Δ Weight (%)",
            "categories": ["Start Rate"] + cats4 + ["After Mix"],
            "values":     [0.0] + dw_pct_ordered + [0.0],
            "tooltips":   [None] + wf3_tooltips + [None],
            "y_range":    [round(dw_min - dw_pad, 4), round(dw_max + dw_pad, 4)],
        }

    return {
        "buckets":         buckets,
        "balance_t0_m":    bal0_m,
        "balance_t1_m":    bal1_m,
        "balance_delta_m": bal_delta_m,
        "rate_t0_pct":     r0_pct,
        "rate_t1_pct":     r1_pct,
        "tenor_t0":        ten0,
        "tenor_t1":        ten1,
        "weight_t0_pct":   w0_pct,
        "weight_t1_pct":   w1_pct,
        "wf1":             wf1,
        "wf2":             wf2,
        "wf2_bg":          wf2_bg,
        "wf3":             wf3 if buckets and len(price_eff_arr) > 0 else None,
        "wf4":             wf4,
        "wat": {
            "t0":     round(wat0, 1),
            "t1":     round(wat1, 1),
            "delta":  round(wat1 - wat0, 1),
        },
        "totals": {
            "balance_t0_m":  round(tot_b0 / SCALE, 2),
            "balance_t1_m":  round(tot_b1 / SCALE, 2),
            "rate_t0_pct":   round(wavg_r0 * 100.0, 4),
            "rate_t1_pct":   round(wavg_r1 * 100.0, 4),
            "rate_delta_bps": _bps(wavg_r1 - wavg_r0),
            "dropped_t0_pct": round(drop0, 2),
            "dropped_t1_pct": round(drop1, 2),
        },
    }



# ── app.py 2582-2635 ──
def _build_tenor_daily_evolution(df_range: pd.DataFrame) -> Dict:
    """Build the per-day bucket-level time series for the Daily Evolution tab."""
    if df_range.empty:
        return {"dates": [], "buckets": [], "balance_m": {}, "rate_pct": {}, "wat_series": []}
    df = df_range[df_range["DIM_BUCKET"].astype(str).str.len() > 0].copy()
    if df.empty:
        return {"dates": [], "buckets": [], "balance_m": {}, "rate_pct": {}, "wat_series": []}
    df["_wr"] = df["BALANCE"] * df["INTEREST_RATE"]
    if "AGIRLIKLI_ORT_TENOR" in df.columns:
        df["_wt"] = df["BALANCE"] * pd.to_numeric(df["AGIRLIKLI_ORT_TENOR"], errors="coerce").fillna(0.0)
    else:
        df["_wt"] = 0.0
    g = (
        df.groupby(["DAT", "DIM_BUCKET"], dropna=False)[["BALANCE", "_wr", "_wt"]]
        .sum()
        .reset_index()
    )
    g["RATE_PCT"] = np.where(g["BALANCE"] != 0, g["_wr"] / g["BALANCE"] * 100.0, 0.0)
    g["TENOR"]    = np.where(g["BALANCE"] != 0, g["_wt"] / g["BALANCE"], 0.0)
    dates = sorted(g["DAT"].unique())
    buckets = sorted(g["DIM_BUCKET"].astype(str).unique(), key=TenorAnalysisEngine._bucket_lower)
    date_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates]
    SCALE = 1e6
    balance_m: Dict[str, List[float]] = {b: [] for b in buckets}
    rate_pct:  Dict[str, List[Optional[float]]] = {b: [] for b in buckets}
    pivot_b = g.pivot_table(index="DAT", columns="DIM_BUCKET", values="BALANCE", fill_value=0.0).reindex(dates).reindex(columns=buckets, fill_value=0.0)
    pivot_r = g.pivot_table(index="DAT", columns="DIM_BUCKET", values="RATE_PCT", fill_value=np.nan).reindex(dates).reindex(columns=buckets)
    for b in buckets:
        balance_m[b] = (pivot_b[b] / SCALE).round(2).tolist()
        rate_pct[b]  = [None if pd.isna(v) else round(float(v), 4) for v in pivot_r[b].tolist()]
    # Overall WAT per day (balance-weighted across buckets)
    wat_series: List[float] = []
    daily = g.groupby("DAT")[["BALANCE", "_wt"]].sum()
    for d in dates:
        bal = float(daily.loc[d, "BALANCE"])
        wt  = float(daily.loc[d, "_wt"])
        wat_series.append(round(wt / bal, 1) if bal > 0 else 0.0)
    return {
        "dates":      date_strs,
        "buckets":    buckets,
        "balance_m":  balance_m,
        "rate_pct":   rate_pct,
        "wat_series": wat_series,
    }


# ════════════════════════════════════════════════════════════════════════════
# Swap Hedge Engine — "HEDGE UZUN VADELİ TRY MEVDUAT" swap'larını Tenor Analysis
# Maturity Ladder'ında mevduat bucket'larıyla hizalar. Query (queries/*/swaps.sql)
# yalnız ISLEMAMACI filtreler; yaşayan-işlem seçimi + bucketleme burada yapılır.
# ════════════════════════════════════════════════════════════════════════════
_SWAP_CACHE: Dict[str, object] = {}



# ── app.py 2636-2794 ──
class SwapHedgeEngine:
    """Living TRY-hedge swaps bucketed to match the deposit tenor buckets.

    A swap is "living" on snapshot date X iff VALORTARIHI <= X <= VADETARIHI.
    The TRY leg notional is ALINANMIKTAR (if ALINANDOVIZCINSI='TRY') else
    VERILENMIKTAR (if VERILENDOVIZCINSI='TRY'). Tenor (days) follows the active
    mode: TENOR = VADETARIHI - VALORTARIHI (original), DTM = VADETARIHI - X
    (remaining). Each living swap is dropped into the deposit bucket whose
    [lo, hi] range contains its day count (fallback: nearest lower bucket).
    """

    @classmethod
    def _load(cls) -> pd.DataFrame:
        if "df" not in _SWAP_CACHE:
            try:
                raw = load_dataframe("swaps")
            except Exception as e:
                log.warning(f"swaps yüklenemedi ({e}) — Tenor Analysis hedge "
                      "overlay boş gösterilecek.")
                _SWAP_CACHE["df"] = pd.DataFrame(
                    columns=["VALOR", "VADE", "TRY_NOTIONAL", "TRY_RATE_EFF"])
                return _SWAP_CACHE["df"]
            df = raw.copy()
            df["VALOR"] = pd.to_datetime(df["VALORTARIHI"], errors="coerce")
            df["VADE"]  = pd.to_datetime(df["VADETARIHI"],  errors="coerce")
            al  = pd.to_numeric(df.get("ALINANMIKTAR"),  errors="coerce")
            ve  = pd.to_numeric(df.get("VERILENMIKTAR"), errors="coerce")
            alc = df.get("ALINANDOVIZCINSI",  "").astype(str).str.upper()
            vec = df.get("VERILENDOVIZCINSI", "").astype(str).str.upper()
            # TRY bacak: alınan TRY ise onu, değilse verilen TRY ise onu al.
            df["TRY_NOTIONAL"] = np.where(alc == "TRY", al,
                                          np.where(vec == "TRY", ve, np.nan))
            # TRY bacak nominal faiz oranı (% — quarterly kupon). Bacağa göre
            # ALIS/SATIS faiz oranından alınır.
            alr = pd.to_numeric(df.get("ALISFAIZORANI"),  errors="coerce")
            ser = pd.to_numeric(df.get("SATISFAIZORANI"), errors="coerce")
            _rate_nom = np.where(alc == "TRY", alr, np.where(vec == "TRY", ser, np.nan))
            # HARDCODED: swap kuponu QUARTERLY ödenir; mevduatla (annual) kıyas için
            # yıllık EFFECTIVE'e compound et → (1 + r/4)^4 - 1 (daha yüksek oran).
            _rn = pd.to_numeric(pd.Series(_rate_nom, index=df.index), errors="coerce") / 100.0
            df["TRY_RATE_EFF"] = ((1.0 + _rn / 4.0) ** 4 - 1.0) * 100.0
            df = df.dropna(subset=["VALOR", "VADE", "TRY_NOTIONAL"])
            # Aynı deal birden çok satırda gelebilir (günlük MTM snapshot'ları) →
            # nominal sabit olduğundan REFERANSNO bazında tekilleştir.
            if "REFERANSNO" in df.columns:
                df = df.drop_duplicates(subset=["REFERANSNO"])
            _nz = int((df["TRY_NOTIONAL"] > 0).sum())
            log.info(f"swaps (HEDGE UZUN VADELİ TRY MEVDUAT): "
                  f"{_nz:,}/{len(df):,} yaşayabilir TRY-hedge deal yüklendi.")
            _SWAP_CACHE["df"] = df[["VALOR", "VADE", "TRY_NOTIONAL", "TRY_RATE_EFF"]].reset_index(drop=True)
        return _SWAP_CACHE["df"]

    @staticmethod
    def _bucket_bounds(b: str) -> tuple:
        """'366-725' → (366, 725). Parse edilemezse (inf, inf)."""
        try:
            lo, hi = str(b).split("-", 1)
            return int(lo), int(hi)
        except (ValueError, AttributeError):
            return 10**9, 10**9

    @classmethod
    def hedge_by_bucket(cls, snapshot: pd.Timestamp, mode: str,
                        buckets: List[str]) -> tuple:
        """Yaşayan hedge'leri mevduat bucket'larına dağıt. buckets sırasıyla hizalı
        ÜÇ liste döner: (amount_m, wavg_tenor_days, wavg_rate_eff_pct). amount
        POZİTİF (frontend negatife çevirir); tenor = nominal-ağırlıklı ortalama gün
        (TENOR: orijinal, DTM: kalan vade); rate = nominal-ağırlıklı ortalama
        YILLIK EFFECTIVE faiz (swap kuponu quarterly → (1+r/4)^4-1)."""
        SCALE = 1e6
        n = len(buckets)
        amt = [0.0] * n
        wten = [0.0] * n   # Σ(days × notional)
        wrat = [0.0] * n   # Σ(eff_rate × notional)
        if not buckets:
            return amt, [], []
        df = cls._load()
        if df.empty:
            return amt, [None] * n, [None] * n
        alive = df[(df["VALOR"] <= snapshot) & (df["VADE"] >= snapshot)]
        if alive.empty:
            return amt, [None] * n, [None] * n
        # Gün sayısı moda göre: DTM = kalan (VADE - snapshot); TENOR = orijinal.
        if mode == "dtm":
            days = (alive["VADE"] - snapshot).dt.days
        else:
            days = (alive["VADE"] - alive["VALOR"]).dt.days
        # Bucket sınırları (payload sırasına göre) + _bucket_lower ile artan.
        bounds = [cls._bucket_bounds(b) for b in buckets]
        order = sorted(range(n), key=lambda i: TenorAnalysisEngine._bucket_lower(buckets[i]))
        notion_sum = [0.0] * n
        _rates = (alive["TRY_RATE_EFF"].tolist() if "TRY_RATE_EFF" in alive.columns
                  else [None] * len(alive))
        for d_days, notion, eff in zip(days.tolist(), alive["TRY_NOTIONAL"].tolist(), _rates):
            idx = None
            for i in order:
                lo, hi = bounds[i]
                if lo <= d_days <= hi:
                    idx = i
                    break
            if idx is None:
                # Aralık dışı → en yakın alt bucket (lo <= days olan en büyük);
                # hepsinden küçükse ilk bucket.
                cand = [i for i in order if bounds[i][0] <= d_days]
                idx = cand[-1] if cand else order[0]
            amt[idx] += float(notion) / SCALE
            wten[idx] += float(d_days) * float(notion)
            notion_sum[idx] += float(notion)
            if eff is not None and not (isinstance(eff, float) and np.isnan(eff)):
                wrat[idx] += float(eff) * float(notion)
        tenor = [round(wten[i] / notion_sum[i], 1) if notion_sum[i] > 0 else None for i in range(n)]
        rate  = [round(wrat[i] / notion_sum[i], 2) if notion_sum[i] > 0 else None for i in range(n)]
        return [round(v, 2) for v in amt], tenor, rate

    @classmethod
    def hedge_by_bucket_month(cls, month: pd.Timestamp, mode: str,
                              buckets: List[str]) -> tuple:
        """hedge_by_bucket'ın AYLIK ORTALAMA muadili (Monthly Averages sekmesi).

        Ayın TÜM takvim günleri için günlük yaşayan notional alınır ve gün
        sayısına bölünür → mevduatın aylık ortalama bakiyesiyle tutarlı ortalama
        hedge bakiyesi. Tenor/rate, günlük notional ağırlıklı ortalamadır
        (DTM modunda kalan vade gün gün değişir — ortalaması buradan çıkar).
        """
        n = len(buckets)
        if not buckets:
            return [], [], []
        start = pd.Timestamp(month).normalize().replace(day=1)
        days = pd.date_range(start, start + pd.offsets.MonthEnd(0), freq="D")
        amt_sum = [0.0] * n
        wten = [0.0] * n
        wten_w = [0.0] * n
        wrat = [0.0] * n
        wrat_w = [0.0] * n
        for d in days:
            a, t, r = cls.hedge_by_bucket(d, mode, buckets)
            for i in range(n):
                if a[i] <= 0:
                    continue
                amt_sum[i] += a[i]
                if t[i] is not None:
                    wten[i] += t[i] * a[i]
                    wten_w[i] += a[i]
                if r[i] is not None:
                    wrat[i] += r[i] * a[i]
                    wrat_w[i] += a[i]
        nd = float(len(days))
        amt   = [round(v / nd, 2) for v in amt_sum]
        tenor = [round(wten[i] / wten_w[i], 1) if wten_w[i] > 0 else None for i in range(n)]
        rate  = [round(wrat[i] / wrat_w[i], 2) if wrat_w[i] > 0 else None for i in range(n)]
        return amt, tenor, rate


# ════════════════════════════════════════════════════════════════════════════
# Balance Analysis — Outstanding Balance growth by Segment / AUM / Product /
# Customer Type. Reuses DepositDetailEngine / DailyDepositEngine caches; no
# additional disk reads. The decomposition dimension is user-selectable; the
# growth heatmap is always Segment × AUM (richest cross-tab).
# ════════════════════════════════════════════════════════════════════════════

# ── app.py 2795-2869 ──
class BalanceAnalysisEngine:
    """Monthly balance-growth decomposition on the deposit dataset."""

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }
    TOP_N = 8  # bridge / ranked-bar truncation

    @classmethod
    def _filter_by_dims(cls, df: pd.DataFrame, dim_filters: Dict[str, List[str]]) -> pd.DataFrame:
        if not dim_filters:
            return df
        out = df
        for d, vals in dim_filters.items():
            # TENOR (MATURITY_BUCKET) → DIM_BUCKET (Tenor Analysis ile aynı; vade
            # kovası filtresi). Hem monthly (DepositDetailEngine) hem daily
            # (DailyDepositEngine) df'i DIM_BUCKET taşır → Balance monthly/daily ve
            # Cost rate-heatmap (bu fonksiyonu paylaşır) TENOR filtresine uyar.
            if d == "MATURITY_BUCKET":
                if vals and "DIM_BUCKET" in out.columns:
                    out = out[out["DIM_BUCKET"].astype(str).isin([str(v) for v in vals])]
                continue
            col = cls._DIM_COL.get(d)
            if not col or not vals or col not in out.columns:
                continue
            out = out[out[col].astype(str).isin([str(v) for v in vals])]
        return out

    @classmethod
    def get_filter_meta(cls) -> Dict[str, List[str]]:
        df, _ = DepositDetailEngine._load()
        meta: Dict[str, List[str]] = {}
        for d, col in cls._DIM_COL.items():
            if col in df.columns:
                vals = sorted({str(v) for v in df[col].dropna() if str(v).strip()})
                meta[d] = vals
        # TENOR (MATURITY_BUCKET) filtre boyutu — numerik sıralı (vade kovası).
        if "DIM_BUCKET" in df.columns:
            buckets = sorted(
                {str(v) for v in df["DIM_BUCKET"].dropna() if str(v).strip()},
                key=TenorAnalysisEngine._bucket_lower,
            )
            if buckets:
                meta["MATURITY_BUCKET"] = buckets
        return meta

    @classmethod
    def build_snapshot(cls, date_0: str, date_1: str, decomp_dim: str,
                       dim_filters: Optional[Dict[str, List[str]]] = None,
                       merges: Optional[Dict[str, List[Dict]]] = None,
                       decomp2_dim: str = "AUM") -> Dict:
        df, _ = DepositDetailEngine._load()
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        df0 = df[df["MONTH"] == d0].copy()
        df1 = df[df["MONTH"] == d1].copy()
        if df0.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No TRY_DEPOSIT_DETAIL records for date_1={date_1}.")
        df0 = cls._filter_by_dims(df0, dim_filters or {})
        df1 = cls._filter_by_dims(df1, dim_filters or {})
        df0 = _apply_balance_merges(df0, merges or {})
        df1 = _apply_balance_merges(df1, merges or {})
        col  = cls._DIM_COL.get(decomp_dim, cls._DIM_COL["SEGMENT"])
        col2 = cls._DIM_COL.get(decomp2_dim, cls._DIM_COL["AUM"])
        return _build_balance_payload(df0, df1, col, col_col=col2)



# ── app.py 2870-2928 ──
class DailyBalanceEngine:
    """Daily counterpart of BalanceAnalysisEngine."""

    DIMENSIONS = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    _DIM_COL = {
        "PRODUCT":       "DIM_PRODUCT",  "SUBPRODUCT": "DIM_SUBPRODUCT",
        "CUSTOMER_TYPE": "DIM_CUSTOMER",
        "AUM":           "DIM_AUM",
        "SEGMENT":       "DIM_SEGMENT",
    }
    TOP_N = 8

    @classmethod
    def _filter_by_dims(cls, df: pd.DataFrame, dim_filters: Dict[str, List[str]]) -> pd.DataFrame:
        return BalanceAnalysisEngine._filter_by_dims.__func__(cls, df, dim_filters)

    @classmethod
    def get_filter_meta(cls) -> Dict[str, List[str]]:
        df, _ = DailyDepositEngine._load()
        meta: Dict[str, List[str]] = {}
        for d, col in cls._DIM_COL.items():
            if col in df.columns:
                vals = sorted({str(v) for v in df[col].dropna() if str(v).strip()})
                meta[d] = vals
        # TENOR (MATURITY_BUCKET) filtre boyutu — numerik sıralı (vade kovası).
        if "DIM_BUCKET" in df.columns:
            buckets = sorted(
                {str(v) for v in df["DIM_BUCKET"].dropna() if str(v).strip()},
                key=TenorAnalysisEngine._bucket_lower,
            )
            if buckets:
                meta["MATURITY_BUCKET"] = buckets
        return meta

    @classmethod
    def build_snapshot(cls, date_0: str, date_1: str, decomp_dim: str,
                       dim_filters: Optional[Dict[str, List[str]]] = None,
                       merges: Optional[Dict[str, List[Dict]]] = None,
                       decomp2_dim: str = "AUM") -> Dict:
        df, _ = DailyDepositEngine._load()
        d0 = pd.to_datetime(date_0)
        d1 = pd.to_datetime(date_1)
        if d0 == d1:
            raise ValueError("date_0 and date_1 cannot be the same.")
        df0 = df[df["DAT"] == d0].copy()
        df1 = df[df["DAT"] == d1].copy()
        if df0.empty:
            raise ValueError(f"No daily_deposit records for date_0={date_0}.")
        if df1.empty:
            raise ValueError(f"No daily_deposit records for date_1={date_1}.")
        df0 = cls._filter_by_dims(df0, dim_filters or {})
        df1 = cls._filter_by_dims(df1, dim_filters or {})
        df0 = _apply_balance_merges(df0, merges or {})
        df1 = _apply_balance_merges(df1, merges or {})
        col  = cls._DIM_COL.get(decomp_dim, cls._DIM_COL["SEGMENT"])
        col2 = cls._DIM_COL.get(decomp2_dim, cls._DIM_COL["AUM"])
        return _build_balance_payload(df0, df1, col, col_col=col2)



# ── app.py 2929-2958 ──
def _apply_balance_merges(df: pd.DataFrame, merges: Dict[str, List[Dict]]) -> pd.DataFrame:
    """Remap dimension column values to their merge-group name.

    `merges` is {DIM: [{"name": str, "members": [str,...]}, ...]}. For each
    group, rows whose dim column value is in `members` are rewritten to the
    group name, so downstream groupby/composition/heatmap aggregations show
    the merged label instead of the underlying sub-buckets.
    """
    if not merges:
        return df
    # MATURITY_BUCKET → DIM_BUCKET: Tenor Analysis'te vade bucket gruplaması da
    # aynı mekanizmayla relabel edilir (Balance/Cost bu dim'i hiç göndermez).
    _DIM_COL = {"PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT", "CUSTOMER_TYPE": "DIM_CUSTOMER",
                "AUM": "DIM_AUM", "SEGMENT": "DIM_SEGMENT", "MATURITY_BUCKET": "DIM_BUCKET"}
    out = df.copy()
    for dim, groups in merges.items():
        col = _DIM_COL.get(dim)
        if not col or col not in out.columns or not groups:
            continue
        for g in groups:
            name = str(g.get("name", "")).strip()
            members = [str(m) for m in (g.get("members") or [])]
            if not name or not members:
                continue
            mask = out[col].astype(str).isin(members)
            if mask.any():
                out.loc[mask, col] = name
    return out



# ── app.py 2959-3213 ──
def _build_balance_payload(df0: pd.DataFrame, df1: pd.DataFrame, decomp_col: str,
                           col_col: str = "DIM_AUM") -> Dict:
    """Shared monthly snapshot builder.

    decomp_col is the DataFrame column to group by (e.g. DIM_SEGMENT).
    col_col is the heatmap X-axis column ("Second Dec. Dim"; default DIM_AUM).
    """
    SCALE = 1e6
    TOP_N = BalanceAnalysisEngine.TOP_N

    def _agg(df: pd.DataFrame) -> pd.Series:
        if df.empty or decomp_col not in df.columns:
            return pd.Series(dtype=float)
        return df.groupby(decomp_col, dropna=False)["BALANCE"].sum()

    s0 = _agg(df0)
    s1 = _agg(df1)
    all_cats = sorted(set(s0.index.astype(str)) | set(s1.index.astype(str)))
    s0 = s0.reindex(all_cats, fill_value=0.0)
    s1 = s1.reindex(all_cats, fill_value=0.0)
    # Sort categories by balance_t1 desc (largest first for ranked bar)
    order = s1.sort_values(ascending=False).index.tolist()
    s0 = s0.reindex(order)
    s1 = s1.reindex(order)

    bal0_m = (s0 / SCALE).round(2).tolist()
    bal1_m = (s1 / SCALE).round(2).tolist()
    delta_m = [round(b1 - b0, 2) for b0, b1 in zip(bal0_m, bal1_m)]
    growth_pct = [round((b1 - b0) / b0 * 100.0, 2) if b0 > 0 else None
                  for b0, b1 in zip(bal0_m, bal1_m)]

    tot_b0 = float(s0.sum())
    tot_b1 = float(s1.sum())
    w0_pct = [round(v / tot_b0 * 100.0, 4) if tot_b0 > 0 else 0.0 for v in s0.tolist()]
    w1_pct = [round(v / tot_b1 * 100.0, 4) if tot_b1 > 0 else 0.0 for v in s1.tolist()]

    # ── Balance Bridge (waterfall: Start → +Δ per top-N category → +Other → End)
    # Sort categories by |Δ| desc for the bridge
    bridge_order = sorted(range(len(order)),
                          key=lambda i: abs(delta_m[i] if delta_m[i] is not None else 0.0),
                          reverse=True)
    top_idx = bridge_order[:TOP_N]
    other_idx = bridge_order[TOP_N:]
    top_cats   = [order[i] for i in top_idx]
    top_delta  = [delta_m[i] for i in top_idx]
    other_sum  = round(sum(delta_m[i] for i in other_idx), 2)

    start_m = round(tot_b0 / SCALE, 2)
    end_m   = round(tot_b1 / SCALE, 2)
    bridge_x   = ["Start"] + top_cats + (["Other"] if other_idx else []) + ["End"]
    bridge_y   = [start_m] + [float(v) for v in top_delta] + ([other_sum] if other_idx else []) + [end_m]
    bridge_meas = ["absolute"] + ["relative"] * len(top_cats) + (["relative"] if other_idx else []) + ["total"]
    # Compute y_floor for nice scaling
    running_lows = [start_m]
    run = start_m
    for v in top_delta + ([other_sum] if other_idx else []):
        run += float(v)
        running_lows.append(run)
    y_floor = min(running_lows + [end_m, 0.0]) - max(abs(end_m - start_m) * 0.15, 1.0)
    bridge = NIMChartBuilder._wf_data(
        f"Balance Bridge (₺M, Top {len(top_cats)})",
        bridge_x, bridge_y, bridge_meas,
        y_floor, y_min_span=1.0, y_pad_ratio=0.10,
    )
    bridge["yaxis_title"] = "Balance (₺M)"
    bridge["unit"] = "M"

    # ── AUM Composition (always computed, independent of decomp_col) ─────────
    aum_composition = None
    cols0 = set(df0.columns) | set(df1.columns)
    if "DIM_AUM" in cols0:
        aum_col = "DIM_AUM"

        def _aum_agg(df: pd.DataFrame) -> pd.Series:
            if df.empty or aum_col not in df.columns:
                return pd.Series(dtype=float)
            return df.groupby(aum_col, dropna=False)["BALANCE"].sum()

        a0 = _aum_agg(df0)
        a1 = _aum_agg(df1)
        all_aum_set = set(a0.index.astype(str)) | set(a1.index.astype(str))
        # Sort AUM bands by numeric lower bound (AUM_0_100K < AUM_100K_500K < ...)
        all_aum = sorted(all_aum_set, key=_aum_numeric_key)
        a0 = a0.reindex(all_aum, fill_value=0.0)
        a1 = a1.reindex(all_aum, fill_value=0.0)
        at0 = float(a0.sum())
        at1 = float(a1.sum())
        aum_w0 = [round(v / at0 * 100.0, 4) if at0 > 0 else 0.0 for v in a0.tolist()]
        aum_w1 = [round(v / at1 * 100.0, 4) if at1 > 0 else 0.0 for v in a1.tolist()]
        aum_composition = {
            "categories": all_aum,
            "weight_t0_pct": aum_w0,
            "weight_t1_pct": aum_w1,
        }

    # ── Growth Heatmap: Y = Decomposition Dim, X = Second Dec. Dim ─────────
    heatmap = None
    customer_heatmap = None   # Müşteri adedi heatmap'i (Balance ile aynı yapı)
    cols0 = set(df0.columns)
    # Heatmap Y ekseni (satır) = Decomposition Dim (decomp_col); X = col_col.
    # Satır = kolon dejenere olur → kolonu alternatife düşür (frontend mutex'i
    # zaten engelliyor; API'yi doğrudan çağıranlara karşı guard).
    row_col = decomp_col
    if col_col == row_col:
        col_col = "DIM_AUM" if row_col != "DIM_AUM" else "DIM_SEGMENT"
    _row_key = _aum_numeric_key if row_col == "DIM_AUM" else str
    _col_key = _aum_numeric_key if col_col == "DIM_AUM" else str
    if row_col in cols0 and col_col in cols0:
        h0 = (df0.groupby([row_col, col_col], dropna=False)["BALANCE"].sum()
              if not df0.empty else pd.Series(dtype=float))
        h1 = (df1.groupby([row_col, col_col], dropna=False)["BALANCE"].sum()
              if not df1.empty else pd.Series(dtype=float))
        # Müşteri adedi (CUST_COUNT) — aynı satır × kolon kırılımı, toplam.
        hc0 = (df0.groupby([row_col, col_col], dropna=False)["CUST_COUNT"].sum()
               if (not df0.empty and "CUST_COUNT" in df0.columns) else pd.Series(dtype=float))
        hc1 = (df1.groupby([row_col, col_col], dropna=False)["CUST_COUNT"].sum()
               if (not df1.empty and "CUST_COUNT" in df1.columns) else pd.Series(dtype=float))
        all_rows = sorted({str(s) for s, _ in list(h0.index) + list(h1.index)}, key=_row_key)
        all_cols_set = {str(a) for _, a in list(h0.index) + list(h1.index)}
        # AUM ekseni numeric lower bound'a göre sıralanır; diğer dim'ler alfabetik.
        all_cols = sorted(all_cols_set, key=_col_key)
        if all_rows and all_cols:
            growth_grid = []
            delta_grid  = []
            balance_t1_grid = []
            for r in all_rows:
                row_growth = []
                row_delta  = []
                row_bal1   = []
                for c in all_cols:
                    b0v = float(h0.get((r, c), 0.0))
                    b1v = float(h1.get((r, c), 0.0))
                    row_delta.append(round((b1v - b0v) / SCALE, 2))
                    row_bal1.append(round(b1v / SCALE, 2))
                    row_growth.append(round((b1v - b0v) / b0v * 100.0, 2) if b0v > 0 else None)
                growth_grid.append(row_growth)
                delta_grid.append(row_delta)
                balance_t1_grid.append(row_bal1)

            # Col totals — per segment (summed across all AUM bands)
            col_total_delta_m: List[float] = []
            col_total_balance_t1: List[float] = []
            col_total_growth_pct: List[Optional[float]] = []
            for r in all_rows:
                b0s = sum(float(h0.get((r, c), 0.0)) for c in all_cols)
                b1s = sum(float(h1.get((r, c), 0.0)) for c in all_cols)
                col_total_delta_m.append(round((b1s - b0s) / SCALE, 2))
                col_total_balance_t1.append(round(b1s / SCALE, 2))
                col_total_growth_pct.append(round((b1s - b0s) / b0s * 100.0, 2) if b0s > 0 else None)

            # Row totals — per AUM band (summed across all segments)
            row_total_delta_m: List[float] = []
            row_total_balance_t1: List[float] = []
            row_total_growth_pct: List[Optional[float]] = []
            for c in all_cols:
                b0s = sum(float(h0.get((r, c), 0.0)) for r in all_rows)
                b1s = sum(float(h1.get((r, c), 0.0)) for r in all_rows)
                row_total_delta_m.append(round((b1s - b0s) / SCALE, 2))
                row_total_balance_t1.append(round(b1s / SCALE, 2))
                row_total_growth_pct.append(round((b1s - b0s) / b0s * 100.0, 2) if b0s > 0 else None)

            grand_b0 = float(h0.sum())
            grand_b1 = float(h1.sum())
            heatmap = {
                "rows":       all_rows,
                "cols":       all_cols,
                "growth_pct": growth_grid,
                "delta_m":    delta_grid,
                "balance_t1": balance_t1_grid,
                "col_total_delta_m":       col_total_delta_m,
                "col_total_balance_t1":    col_total_balance_t1,
                "col_total_growth_pct":    col_total_growth_pct,
                "row_total_delta_m":       row_total_delta_m,
                "row_total_balance_t1":    row_total_balance_t1,
                "row_total_growth_pct":    row_total_growth_pct,
                "grand_total_delta_m":     round((grand_b1 - grand_b0) / SCALE, 2),
                "grand_total_balance_t1":  round(grand_b1 / SCALE, 2),
                "grand_total_growth_pct":  round((grand_b1 - grand_b0) / grand_b0 * 100.0, 2) if grand_b0 > 0 else None,
            }

            # ── Customer Number Heatmap — aynı ızgara, CUST_COUNT (adet; SCALE YOK).
            # Balance Heatmap ile AYNI alan adları → frontend aynı render'ı kullanır.
            cc_t1_grid, cc_delta_grid, cc_growth_grid = [], [], []
            for r in all_rows:
                rc1, rd, rg = [], [], []
                for c in all_cols:
                    c0v = float(hc0.get((r, c), 0.0))
                    c1v = float(hc1.get((r, c), 0.0))
                    rc1.append(round(c1v, 0))
                    rd.append(round(c1v - c0v, 0))
                    rg.append(round((c1v - c0v) / c0v * 100.0, 2) if c0v > 0 else None)
                cc_t1_grid.append(rc1); cc_delta_grid.append(rd); cc_growth_grid.append(rg)
            cc_col_t1, cc_col_d, cc_col_g = [], [], []   # per-segment (row) totals
            for r in all_rows:
                c0s = sum(float(hc0.get((r, c), 0.0)) for c in all_cols)
                c1s = sum(float(hc1.get((r, c), 0.0)) for c in all_cols)
                cc_col_t1.append(round(c1s, 0)); cc_col_d.append(round(c1s - c0s, 0))
                cc_col_g.append(round((c1s - c0s) / c0s * 100.0, 2) if c0s > 0 else None)
            cc_row_t1, cc_row_d, cc_row_g = [], [], []   # per-AUM totals
            for c in all_cols:
                c0s = sum(float(hc0.get((r, c), 0.0)) for r in all_rows)
                c1s = sum(float(hc1.get((r, c), 0.0)) for r in all_rows)
                cc_row_t1.append(round(c1s, 0)); cc_row_d.append(round(c1s - c0s, 0))
                cc_row_g.append(round((c1s - c0s) / c0s * 100.0, 2) if c0s > 0 else None)
            gc0 = float(hc0.sum()); gc1 = float(hc1.sum())
            customer_heatmap = {
                "rows": all_rows, "cols": all_cols,
                "growth_pct": cc_growth_grid, "delta_m": cc_delta_grid, "balance_t1": cc_t1_grid,
                "col_total_delta_m": cc_col_d, "col_total_balance_t1": cc_col_t1,
                "col_total_growth_pct": cc_col_g,
                "row_total_delta_m": cc_row_d, "row_total_balance_t1": cc_row_t1,
                "row_total_growth_pct": cc_row_g,
                "grand_total_delta_m": round(gc1 - gc0, 0),
                "grand_total_balance_t1": round(gc1, 0),
                "grand_total_growth_pct": round((gc1 - gc0) / gc0 * 100.0, 2) if gc0 > 0 else None,
            }

    return {
        "categories":      order,
        "balance_t0_m":    bal0_m,
        "balance_t1_m":    bal1_m,
        "delta_m":         delta_m,
        "growth_pct":      growth_pct,
        "weight_t0_pct":   w0_pct,
        "weight_t1_pct":   w1_pct,
        "bridge":          bridge,
        "heatmap":         heatmap,
        "customer_heatmap": customer_heatmap,
        "aum_composition": aum_composition,
        "totals": {
            "balance_t0_m": round(tot_b0 / SCALE, 2),
            "balance_t1_m": round(tot_b1 / SCALE, 2),
            "delta_m":      round((tot_b1 - tot_b0) / SCALE, 2),
            "growth_pct":   round((tot_b1 - tot_b0) / tot_b0 * 100.0, 2) if tot_b0 > 0 else None,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# WeeklyRollingsEngine — Mevduat Dönüşleri (Weekly Report)
#
# Data source: queries/{dev,prod}/weekly_rollings.sql — bind params DATE_START,
# DATE_END. SQL returns ROLL_DATE × CURRENCY × CCY_CODE × CUST_TP × AUM_LOWER
# aggregates with weighted-avg numerators (Σ(B×r), Σ(B×DTM)).
#
# The engine collapses the 11 AUM_LOWER values to 6 display bands and produces
# three pivot tables (1: TRY+FX, 2: TRY-Gerçek, 3: TRY-Tüzel). Numbers are
# returned in million-₺ units, rounded to the nearest integer to match the
# report style. Weighted averages are NOT computed here (the spec's slide 1
# only shows balances) but the numerator columns are surfaced unchanged so
# future slides can derive them via Σ(B×r)/ΣB.
#
# See docs/weekly_rollings_veri_dokumantasyon.md for column semantics, the
# AUM band map, and red-line rules.
# ════════════════════════════════════════════════════════════════════════════


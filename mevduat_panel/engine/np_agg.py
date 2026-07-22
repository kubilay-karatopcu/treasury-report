# NOT: Bu dosya simple↔compound faiz dönüşüm helper'larını ve TENOR_DAYS
# kolonunu içerir (New Business heatmap için). app.py bu fonksiyonları import
# eder — push doğrulama amaçlı yeniden işaretlendi.
"""
engine/np_agg.py — New Production (Yeni Para) aggregation helpers.

Data source: load_dataframe("new_production_analysis") via engine/db_source.py
  DEVELOPMENT  → queries/dev/new_production_analysis.sql  (SQLite)
  PRODUCTION_DB → queries/prod/new_production_analysis.sql (Oracle)

SQL returns aggregated rows grouped by (VAL_DT, CCY_CODE, CUST_TP, KAMPANYA_ADI,
AUM_LOWER, VADE_BUCKET, SUB_SEGMENT). _normalize_sql_df() maps these to the
internal schema expected by aggregate_timeseries / aggregate_distribution.

Internal schema
---------------
DAT           datetime64  valuation date
CCY_CODE      category    TRY / USD / EUR
CUST_TP       category    G / T
RELATED_PC    category    campaign name (KAMPANYA_ADI)
AUM_BAND      category    human-readable AUM bracket e.g. "1M-2M"
TENOR_GRP     category    VADE_BUCKET value e.g. "02_4-31"
SUB_SEGMENT   category    e.g. "Bireysel-Private"
NP_HACIM      float       bağlanan TOPLAM bakiye (TL mn) ← TRY_BAKIYE_TOPLAM / 1e6
                          (yeni para DEĞİL — heatmap "Bağlanan" ve drill
                          "TOPLAM BAKİYE" KPI'ı bu kolonla eşleşir)
YENI_PARA     float       yeni para tutarı (TL mn)   ← YENI_PARA_TOPLAM / 1e6
NP_FAIZ       float       wavg interest rate (%)     ← WAVG_INTRST_RT
                          (SQL'de TRY_BALANCE-ağırlıklı → yeniden ağırlıklandırma
                          da NP_HACIM=bakiye ile yapılır, tutarlı)
OS_BAKIYE     float       outstanding balance (TL mn) ← TRY_BAKIYE_TOPLAM / 1e6

Public API
----------
load_np_data()                          -> pd.DataFrame  (cached)
apply_filters(df, *, ccy, ...)          -> pd.DataFrame
aggregate_timeseries(df, group_by, freq) -> pd.DataFrame
aggregate_distribution(df, group_by)    -> pd.DataFrame
get_dimension_values(df)                -> dict
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

_NP_CACHE: Optional[pd.DataFrame] = None

# Day-count convention (Türkiye standart: 365)
_DAY_COUNT = 365.0


# ── Simple ↔ Compound rate conversion ────────────────────────────────────────
# Ham veri BASİT faiz tutuyor. Farklı vadeli mevduatları apples-to-apples
# karşılaştırmak için tüm aggregation BİLEŞİK (yıllık annualize) faiz üzerinden
# yapılır; sadece gösterim aşamasında (INTEREST RATE modu) tekrar basit faize
# çevrilir. Scalar helper'lar DECIMAL (0.42 = %42) ile çalışır — prompt'taki
# birim testle birebir uyumlu. Vectorized `*_pct_series` helper'ları PERCENT
# (42.0 = %42) ile çalışır — np_agg/app.py içindeki NP_FAIZ birimine uyumlu.


def simple_to_compound(simple_rate: Optional[float],
                       tenor_days: Optional[float]) -> Optional[float]:
    """Basit faiz + vade(gün) → yıllık bileşik faiz. DECIMAL in, DECIMAL out.

        period_return  = 1 + simple_rate * tenor_days / 365
        compound_rate  = period_return ^ (365 / tenor_days) - 1
    """
    if simple_rate is None or tenor_days is None or tenor_days <= 0:
        return None
    period_return = 1.0 + simple_rate * (tenor_days / _DAY_COUNT)
    if period_return <= 0:
        return None
    return period_return ** (_DAY_COUNT / tenor_days) - 1.0


def compound_to_simple(compound_rate: Optional[float],
                       tenor_days: Optional[float]) -> Optional[float]:
    """Yıllık bileşik faiz + vade(gün) → yıllık basit faiz. DECIMAL in/out.

        period_return = (1 + compound_rate) ^ (tenor_days / 365)
        simple_rate   = (period_return - 1) * (365 / tenor_days)
    """
    if compound_rate is None or tenor_days is None or tenor_days <= 0:
        return None
    base = 1.0 + compound_rate
    if base <= 0:
        return None
    period_return = base ** (tenor_days / _DAY_COUNT)
    return (period_return - 1.0) * (_DAY_COUNT / tenor_days)


def simple_to_compound_pct_series(simple_pct: pd.Series,
                                  tenor_days: pd.Series) -> pd.Series:
    """Vectorized forward conversion. PERCENT in → PERCENT out.

    tenor_days <= 0 veya NaN olan satırlar NaN döner (aggregation'da exclude).
    """
    r = simple_pct.to_numpy(dtype=float) / 100.0
    t = tenor_days.to_numpy(dtype=float)
    valid = (t > 0) & np.isfinite(t) & np.isfinite(r)
    out = np.full(r.shape, np.nan)
    period = 1.0 + r[valid] * (t[valid] / _DAY_COUNT)
    ok = period > 0
    idx = np.where(valid)[0][ok]
    out[idx] = (np.power(period[ok], _DAY_COUNT / t[valid][ok]) - 1.0) * 100.0
    return pd.Series(out, index=simple_pct.index)


def compound_to_simple_pct(compound_pct: Optional[float],
                           tenor_days: Optional[float]) -> Optional[float]:
    """Scalar reverse conversion. PERCENT in → PERCENT out (gösterim için)."""
    if compound_pct is None or tenor_days is None or tenor_days <= 0:
        return None
    res = compound_to_simple(compound_pct / 100.0, tenor_days)
    return None if res is None else res * 100.0


def compound_to_on_pct(compound_pct: Optional[float]) -> Optional[float]:
    """Yıllık bileşik (%) → O/N eşleniği (%): 365·((1+c)^(1/365)−1)·100.

    Bileşik oran zaten yıllık EFEKTİF olduğundan dönüşüm vadeden bağımsızdır
    (bubble Rate Type'ındaki simple→on zinciriyle tutarlı: simple→compound
    kendi vadesiyle, compound→on 365 günle).
    """
    if compound_pct is None:
        return None
    base = 1.0 + compound_pct / 100.0
    if base <= 0:
        return None
    return (base ** (1.0 / 365.0) - 1.0) * 365.0 * 100.0

_CAT_COLS = ["CCY_CODE", "CUST_TP", "RELATED_PC", "AUM_BAND", "TENOR_GRP", "SUB_SEGMENT"]

_AUM_LABELS = {
    0:          "0-1M",
    1_000_000:  "1M-2M",
    2_000_000:  "2M-5M",
    5_000_000:  "5M-10M",
    10_000_000: "10M-25M",
    25_000_000: "25M-50M",
    50_000_000: "50M-100M",
    100_000_000:"100M-200M",
    200_000_000:"200M-500M",
    500_000_000:"500M-1B",
    1_000_000_000: "1B+",
}


def _normalize_sql_df(df: pd.DataFrame) -> pd.DataFrame:
    """Map SQL output columns → internal schema."""
    out = pd.DataFrame()
    out["DAT"]        = pd.to_datetime(df["VAL_DT"])
    out["CCY_CODE"]   = df["CCY_CODE"]
    out["CUST_TP"]    = df["CUST_TP"]
    # RELATED_PC comes from RELATED_PC_CODE (NOT KAMPANYA_ADI — that is the
    # campaign name, a separate dimension). In dev, RELATED_PC_CODE column
    # may be NULL, which is honest dev/prod consistency.
    out["RELATED_PC"] = df["RELATED_PC_CODE"].fillna("Bilinmiyor") \
        if "RELATED_PC_CODE" in df.columns else "Bilinmiyor"
    out["AUM_BAND"]   = df["AUM_LOWER"].map(_AUM_LABELS).fillna("Bilinmiyor")
    out["TENOR_GRP"]  = df["VADE_BUCKET"].fillna("99_DIGER")
    out["SUB_SEGMENT"]= df["SUB_SEGMENT"].fillna("Diger")
    # Volumes in TL-million.
    # NP_HACIM = bağlanan mevduatın TOPLAM bakiyesi (TRY_BAKIYE_TOPLAM) — yeni
    # para DEĞİL. Heatmap "Bağlanan" hücresi drill'deki "TOPLAM BAKİYE" KPI'ına
    # eşleşir; WAVG_INTRST_RT de SQL'de bakiye-ağırlıklı olduğundan yeniden
    # ağırlıklandırma bu kolonla matematiksel olarak tutarlıdır.
    out["NP_HACIM"]   = df["TRY_BAKIYE_TOPLAM"].fillna(0) / 1e6
    out["YENI_PARA"]  = df["YENI_PARA_TOPLAM"].fillna(0) / 1e6
    out["OS_BAKIYE"]  = df["TRY_BAKIYE_TOPLAM"].fillna(0) / 1e6
    out["NP_FAIZ"]    = df["WAVG_INTRST_RT"].fillna(0)
    # Volume-weighted tenor in days (WAVG_DTM). Needed for simple↔compound
    # rate conversion in the New Business heatmap. SQL'de TRY_BALANCE ile
    # ağırlıklandırılmış DTM; her satır tek bir ortalama vade taşır.
    out["TENOR_DAYS"] = df["WAVG_DTM"].fillna(0) if "WAVG_DTM" in df.columns else 0.0
    for col in _CAT_COLS:
        out[col] = out[col].astype("category")
    return out


# ── Load & cache ───────────────────────────────────────────────────────────────

def load_np_data() -> pd.DataFrame:
    global _NP_CACHE
    if _NP_CACHE is None:
        from ..data_source import load_dataframe  # port: db_source yerine
        raw = load_dataframe("new_production_analysis")
        _NP_CACHE = _normalize_sql_df(raw)
    return _NP_CACHE


def reset_caches() -> None:
    """Process-ömrü cache'ini boşaltır — sonraki load_np_data yeniden çeker.
    Data-refresh endpoint'i (prewarm.refresh_all) çağırır."""
    global _NP_CACHE
    _NP_CACHE = None


# ── Filter ─────────────────────────────────────────────────────────────────────

def apply_filters(
    df: pd.DataFrame,
    *,
    ccy: Optional[List[str]] = None,
    cust_tp: Optional[List[str]] = None,
    segment: Optional[List[str]] = None,
    aum_band: Optional[List[str]] = None,
    campaign: Optional[List[str]] = None,
    tenor_grp: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    if ccy:
        mask &= df["CCY_CODE"].isin(ccy)
    if cust_tp:
        mask &= df["CUST_TP"].isin(cust_tp)
    if segment:
        mask &= df["SUB_SEGMENT"].isin(segment)
    if aum_band:
        mask &= df["AUM_BAND"].isin(aum_band)
    if campaign:
        mask &= df["RELATED_PC"].isin(campaign)
    if tenor_grp:
        mask &= df["TENOR_GRP"].isin(tenor_grp)
    if date_from:
        mask &= df["DAT"] >= pd.Timestamp(date_from)
    if date_to:
        mask &= df["DAT"] <= pd.Timestamp(date_to)
    return df.loc[mask].copy()


# ── Aggregation helpers ────────────────────────────────────────────────────────

def _week_start(s: pd.Series) -> pd.Series:
    """Thursday-aligned week start (Thursday → Wednesday).

    `W-WED` = week ending Wednesday → start_time is Thursday.
    """
    return s.dt.to_period("W-WED").dt.start_time


def aggregate_timeseries(
    df: pd.DataFrame,
    *,
    group_by: List[str],
    freq: str = "W",
    week_anchor=None,
) -> pd.DataFrame:
    """Aggregate to a time series grouped by `group_by` dimensions.

    freq: "D" (daily) or "W" (weekly)
    week_anchor: haftalık binleme dayanağı.
      - None  → takvim haftası (W-WED, Perşembe başlangıç). Eski davranış.
      - tarih → o TARİHTE BİTEN 7-günlük yuvarlanan (rolling) haftalar. Binler
        7'şer gün geriye adımlanır; her bin PENCERE SONU tarihiyle etiketlenir.
        Böylece "son hafta" tam olarak [anchor-6, anchor] olur ve Rate-Delta
        heatmap'inin t0/t1 pencereleriyle (aynı rolling tanım) BİREBİR hizalanır
        — hover grafiği hücredeki bps değişimiyle çelişmez, partial son-hafta
        oluşmaz. (Frontend'in `date <= Date(End)` kırpması da bu etiketle uyumlu.)
    Returns columns: DATE, *group_by, NP_HACIM, OS_BAKIYE, NP_FAIZ (wavg)
    """
    df = df.copy()
    df["_wr"]     = df["NP_HACIM"] * df["NP_FAIZ"]
    if freq == "W":
        if week_anchor is not None:
            anchor = pd.Timestamp(week_anchor).normalize()
            days_back = (anchor - df["DAT"].dt.normalize()).dt.days
            idx = (days_back // 7).clip(lower=0)   # anchor sonrası satır (varsa) son binde
            df["_period"] = anchor - pd.to_timedelta(idx * 7, unit="D")
        else:
            df["_period"] = _week_start(df["DAT"])
    else:
        df["_period"] = df["DAT"]

    agg = (
        df.groupby(["_period"] + group_by, observed=True)
        .agg(
            NP_HACIM=("NP_HACIM", "sum"),
            OS_BAKIYE=("OS_BAKIYE", "sum"),
            _wr=("_wr", "sum"),
        )
        .reset_index()
    )
    agg["NP_FAIZ"]  = (agg["_wr"] / agg["NP_HACIM"].where(agg["NP_HACIM"] > 0)).round(4)
    agg["_period"]  = pd.to_datetime(agg["_period"])
    return agg.drop(columns=["_wr"]).rename(columns={"_period": "DATE"})


def aggregate_distribution(
    df: pd.DataFrame,
    *,
    group_by: List[str],
) -> pd.DataFrame:
    """Cross-sectional aggregation (no time dimension).

    Returns: *group_by, NP_HACIM, OS_BAKIYE, NP_FAIZ (wavg)
    """
    df = df.copy()
    df["_wr"] = df["NP_HACIM"] * df["NP_FAIZ"]

    agg = (
        df.groupby(group_by, observed=True)
        .agg(
            NP_HACIM=("NP_HACIM", "sum"),
            OS_BAKIYE=("OS_BAKIYE", "sum"),
            _wr=("_wr", "sum"),
        )
        .reset_index()
    )
    agg["NP_FAIZ"] = (agg["_wr"] / agg["NP_HACIM"].where(agg["NP_HACIM"] > 0)).round(4)
    return agg.drop(columns=["_wr"])


def get_dimension_values(df: pd.DataFrame) -> dict:
    """Return sorted unique values for each filter dimension."""
    # AUM_BAND must keep its numeric order ("0-1M","1M-2M",…,"1B+"); alphabetic
    # sort would scramble it ("100M-200M" before "10M-25M"). Use _AUM_LABELS as
    # the canonical order and append any unexpected values ("Bilinmiyor") at end.
    aum_canonical = list(_AUM_LABELS.values())
    aum_present   = set(df["AUM_BAND"].cat.categories.tolist())
    aum_ordered   = [b for b in aum_canonical if b in aum_present]
    aum_extra     = sorted(aum_present - set(aum_canonical))
    return {
        "ccy":       sorted(df["CCY_CODE"].cat.categories.tolist()),
        "cust_tp":   sorted(df["CUST_TP"].cat.categories.tolist()),
        "segment":   sorted(df["SUB_SEGMENT"].cat.categories.tolist()),
        "aum_band":  aum_ordered + aum_extra,
        "campaign":  sorted(df["RELATED_PC"].cat.categories.tolist()),
        "tenor_grp": sorted(df["TENOR_GRP"].cat.categories.tolist()),
    }

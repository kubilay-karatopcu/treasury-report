"""deposits_pipeline.py — Deposits dashboard'unun 5 sayfasını besleyen nihai
df'leri Oracle'dan üretip PRISMA_DEP_* / PRISMA_NP_* tablolarına yazar ve
tablo dokümanlarını S3'e günceller. TEK script, ofiste çalışır:

    python jobs/deposits_pipeline.py

Ne yapar (3 adım):

  1. ÇEK    — repo'daki DataClient.get_connection() ile queries/deposits/
              altındaki prod sorgularını çalıştırır (NIM_calculation
              queries/prod kopyaları; weekly_rollings :DATE_START/:DATE_END
              bind'i DD/MM/YYYY alır, diğerleri bind'siz tam geçmiş).
  2. DÖNÜŞTÜR— dashboard pipeline'ının yaptığı HER ŞEYİ yapar: kaynak
              normalizasyonları NIM_calculation motorlarından satır referanslı
              birebir port edilmiştir (aşağıda her fonksiyonun başında kaynak
              dosya/satır yazar). Çıktı: 5 sayfanın plotlarını besleyen 8
              nihai DataFrame. Oranlar oran olarak DEĞİL pay/payda olarak
              taşınır (WR_SUM=Σ B·faiz, WT_SUM=Σ B·vade, WC_SUM=Σ B·bileşik)
              — tüketici hangi seviyede gruplarsa gruplasın ağırlıklı
              ortalamayı kendisi böler.
  3. YAZ    — tabloları yoksa CREATE eder (DDL'ler bu dosyada), DELETE +
              executemany INSERT ile tam yeniler (idempotent) ve her tablonun
              Prisma tablo dokümanını (kolon açıklamaları + filtre ipuçları +
              suggested_semantic_tag'ler) S3TableDocStore ile S3'e upsert eder.

Tablolar (hepsi bağlanan kullanıcının şemasında; --schema ile değiştirilebilir):

  Tablo                       Gren                                Sayfa
  PRISMA_DEP_OUT_MONTHLY      ay × 5 DIM_*                        Cost + Balance (aylık)
  PRISMA_DEP_OUT_DAILY        gün × 5 DIM_*                       Cost + Balance (günlük, drill)
  PRISMA_DEP_TENOR_MONTHLY    ay × MOD × kova × 5 DIM_*           Tenor (aylık)
  PRISMA_DEP_TENOR_DAILY      gün × MOD × kova × 5 DIM_*          Tenor (günlük + evrim)
  PRISMA_DEP_ROLL_AGG         dönüş günü × ccy × müşteri × band   Future Rollings pivotları
  PRISMA_DEP_ROLL_DETAIL      hesap-dönüş satırı (ad maskeli)     Rollings segment/drill/DTM
  PRISMA_NP_FLOW_DAILY        gün × 6 boyut                       New Business (tümü)
  PRISMA_NP_OUT_DAILY         gün × 4 ortak boyut                 New Business stok (payda)

Seçenekler:
    --rollings-start / --rollings-end   DD/MM/YYYY (varsayılan bugün → +28g)
    --only T1,T2      yalnız bu tabloları üret/yaz
    --schema X        tabloların/dokümanların şeması (varsayılan: bağlantı kullanıcısı)
    --grant-to U1,U2  tablolara SELECT verilecek kullanıcılar (varsayılan: A63837)
    --skip-db         DB'ye yazma (yalnız üret + doküman)
    --skip-docs       S3 dokümanlarını yazma

NOT: Vade modu kolonu TENOR_MODE'dur — MODE Oracle'da rezerve kelime
(ORA-00904), kolon adı olarak kullanılamaz.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QUERIES_DIR = REPO_ROOT / "queries" / "deposits"

log = logging.getLogger("deposits_pipeline")

DIM_COLS = ["DIM_PRODUCT", "DIM_SUBPRODUCT", "DIM_CUSTOMER", "DIM_AUM", "DIM_SEGMENT"]


# ════════════════════════════════════════════════════════════════════════════
# 1. ÇEK — DataClient bağlantısı üstünden prod sorguları
# ════════════════════════════════════════════════════════════════════════════

def _sql(name: str) -> str:
    return (QUERIES_DIR / f"{name}.sql").read_text(encoding="utf-8")


def fetch_all(dc, con, rollings_start: str, rollings_end: str) -> dict[str, pd.DataFrame]:
    """4 kaynak sorguyu çek. weekly_rollings* DD/MM/YYYY bind alır
    (SQL içindeki TO_DATE(:X,'DD/MM/YYYY') ile uyumlu), diğerleri bind'siz."""
    raws = {}
    for name in ("TRY_DEPOSIT_DETAIL", "daily_deposit", "new_production_analysis"):
        log.info("SQL çekiliyor: %s", name)
        raws[name] = dc.edw_query_to_pandas(con, _sql(name))
        log.info("   %s: %d satır", name, len(raws[name]))
    binds = {"DATE_START": rollings_start, "DATE_END": rollings_end}
    for name in ("weekly_rollings", "weekly_rollings_full"):
        log.info("SQL çekiliyor: %s (%s → %s)", name, rollings_start, rollings_end)
        raws[name] = dc.edw_query_to_pandas(con, _sql(name), params=binds)
        log.info("   %s: %d satır", name, len(raws[name]))
    return raws


# ════════════════════════════════════════════════════════════════════════════
# 2. DÖNÜŞTÜR — NIM_calculation pipeline portları (satır referanslı)
# ════════════════════════════════════════════════════════════════════════════

def normalize_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """TRY_DEPOSIT_DETAIL → aylık normalize df.
    Port: NIM_calculation app.py DepositDetailEngine._load (L1203-1291)."""
    df = df.copy()
    df["MONTH"] = pd.to_datetime(df["MONTH"], errors="coerce")
    df["INTEREST_RATE"] = df["INTEREST_RATE"].astype(float) / 100.0
    df["BALANCE"] = df["BALANCE"].astype(float)
    if "CUSTOMER_NUMBER" in df.columns:
        df["CUST_COUNT"] = pd.to_numeric(df["CUSTOMER_NUMBER"], errors="coerce").fillna(0.0)
    else:
        df["CUST_COUNT"] = 0.0

    tokens = df["PRODUCT"].astype(str).str.split("_")
    df["DIM_PRODUCT"] = tokens.str[0]
    df["DIM_CUSTOMER"] = tokens.str[1].fillna("")
    df["DIM_AUM"] = tokens.str[2:].str.join("_").fillna("")
    df["DIM_SUBPRODUCT"] = (df["SUB_PRODUCT"].astype(str)
                            if "SUB_PRODUCT" in df.columns else df["DIM_PRODUCT"])
    if "TENOR_RATE" in df.columns:
        df["TENOR_RATE"] = pd.to_numeric(df["TENOR_RATE"], errors="coerce")
    if "DTM_RATE" in df.columns:
        df["DTM_RATE"] = pd.to_numeric(df["DTM_RATE"], errors="coerce")

    if "SEGMENT" in df.columns:
        df["DIM_SEGMENT"] = df["SEGMENT"].astype(str).fillna("").replace("nan", "")
        bucket_match = df["DIM_AUM"].astype(str).str.extract(r"_(\d+-\d+)$")[0]
        df["DIM_BUCKET"] = bucket_match.fillna("")

        def _strip_seg(aum: str, seg: str) -> str:
            aum = re.sub(r"_\d+-\d+$", "", aum)
            if not seg:
                return aum
            if aum == seg:
                return ""
            suf = "_" + seg
            return aum[: -len(suf)] if aum.endswith(suf) else aum

        df["DIM_AUM"] = [_strip_seg(a, s) for a, s in zip(df["DIM_AUM"], df["DIM_SEGMENT"])]
    else:
        df["DIM_SEGMENT"] = ""
        bucket_match = df["DIM_AUM"].astype(str).str.extract(r"_(\d+-\d+)$")[0]
        df["DIM_BUCKET"] = bucket_match.fillna("")

    def _clean_bucket(s):
        return s.fillna("").astype(str).replace("None", "").replace("nan", "")

    if "VADE_BUCKET" in df.columns:
        df["DIM_BUCKET"] = _clean_bucket(df["VADE_BUCKET"])
    df["DIM_BUCKET_DTM"] = (_clean_bucket(df["KALAN_VADE_BUCKET"])
                            if "KALAN_VADE_BUCKET" in df.columns else "")
    return df


def normalize_daily(df: pd.DataFrame) -> pd.DataFrame:
    """daily_deposit → günlük normalize df (haftasonları düşer).
    Port: NIM_calculation app.py DailyDepositEngine._load (L1588-1645)."""
    df = df.copy()
    df["DAT"] = pd.to_datetime(df["DAT"], errors="coerce")
    df["AGIRLIKLI_ORT_FAIZ"] = df["AGIRLIKLI_ORT_FAIZ"].astype(float) / 100.0
    df["BALANCE"] = df["GUNLUK_TRY_BAKIYE"].astype(float)
    df["INTEREST_RATE"] = df["AGIRLIKLI_ORT_FAIZ"]
    if "CUSTOMER_NUMBER" in df.columns:
        df["CUST_COUNT"] = pd.to_numeric(df["CUSTOMER_NUMBER"], errors="coerce").fillna(0.0)
    else:
        df["CUST_COUNT"] = 0.0

    df["DIM_PRODUCT"] = df["TYPE2"].astype(str)
    df["DIM_SUBPRODUCT"] = (df["SUB_PRODUCT"].astype(str)
                            if "SUB_PRODUCT" in df.columns else df["DIM_PRODUCT"])
    df["DIM_CUSTOMER"] = df["CUST_TP"].astype(str)
    df["DIM_AUM"] = df["AUM_TYPE"].astype(str)
    df["DIM_SEGMENT"] = df["SEGMENT"].astype(str)

    def _clean(colname):
        if colname in df.columns:
            return (df[colname].fillna("").astype(str)
                    .replace("None", "").replace("nan", ""))
        return pd.Series([""] * len(df), index=df.index)

    df["DIM_BUCKET"] = _clean("VADE_BUCKET")
    df["DIM_BUCKET_DTM"] = _clean("KALAN_VADE_BUCKET")
    if "AGIRLIKLI_ORT_TENOR" in df.columns:
        df["AGIRLIKLI_ORT_TENOR"] = pd.to_numeric(df["AGIRLIKLI_ORT_TENOR"], errors="coerce")
    if "AGIRLIKLI_ORT_DTM" in df.columns:
        df["AGIRLIKLI_ORT_DTM"] = pd.to_numeric(df["AGIRLIKLI_ORT_DTM"], errors="coerce")
    return df[df["DAT"].dt.dayofweek < 5].copy()


# ── Weekly Rollings (port: app.py WeeklyRollingsEngine L2955-3212) ──────────

ROLL_BAND_ORDER = ["0-5M", "5M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+"]
ROLL_AUM_TO_BAND = {
    0: "0-5M", 1_000_000: "0-5M", 2_000_000: "0-5M",
    5_000_000: "5M-25M", 10_000_000: "5M-25M",
    25_000_000: "25M-50M",
    50_000_000: "50M-100M",
    100_000_000: "100M-200M",
    200_000_000: "200M+", 500_000_000: "200M+", 1_000_000_000: "200M+",
}


def _roll_band(df: pd.DataFrame) -> pd.Series:
    band = df["AUM_LOWER"].map(ROLL_AUM_TO_BAND)
    if band.isna().any():
        unmapped = sorted(df.loc[band.isna(), "AUM_LOWER"].unique().tolist())
        raise ValueError(f"AUM_LOWER {unmapped!r} ROLL_AUM_TO_BAND haritasında yok "
                         "(sessiz drop yasak — haritayı güncelle).")
    return band


def normalize_weekly_agg(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["ROLL_DATE"] = pd.to_datetime(df["ROLL_DATE"], errors="coerce")
    df["AUM_BAND"] = _roll_band(df)
    return df


def _mask_name(v) -> str:
    """KVKK isim maskesi — dashboard'un _mask_full_nm'iyle BİREBİR aynı
    (app.py L3406-3411): ilk harf + soyadın ilk harfi. PII düz metin olarak
    PRISMA tablosuna yazılmaz."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    parts = str(v).split()
    if len(parts) >= 2:
        return f"{parts[0][:1]}*** {parts[-1][:1]}***"
    return str(v)[:1] + "***"


def normalize_weekly_full(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["ROLL_DATE"] = pd.to_datetime(df["ROLL_DATE"], errors="coerce")
    if "VAL_DT" in df.columns:
        df["VAL_DT"] = pd.to_datetime(df["VAL_DT"], errors="coerce")
    df["AUM_BAND"] = _roll_band(df)

    def _segment(r):
        if r.get("ISNPO") == 1:
            return "NPO"
        if r.get("CUST_TP") == "T":
            return "Tüzel"
        if r.get("ISPRIVATE") == 1:
            return "Private"
        if r.get("ISAFFLUENT") == 1:
            return "Affluent"
        if r.get("ISMAASLI") == 1:
            return "Maaşlı"
        return "Diğer"

    df["SEGMENT"] = df.apply(_segment, axis=1)
    df["HAS_KAMPANYA"] = (df["KAMPANYA_ADI"].notna()
                          & (df["KAMPANYA_ADI"] != "")).astype(int)
    if "FULL_NM" in df.columns:
        df["FULL_NM"] = df["FULL_NM"].map(_mask_name)
    return df


# ── New Business (port: engine/np_agg.py L64-166) ───────────────────────────

_NP_AUM_LABELS = {
    0: "0-1M", 1_000_000: "1M-2M", 2_000_000: "2M-5M", 5_000_000: "5M-10M",
    10_000_000: "10M-25M", 25_000_000: "25M-50M", 50_000_000: "50M-100M",
    100_000_000: "100M-200M", 200_000_000: "200M-500M", 500_000_000: "500M-1B",
    1_000_000_000: "1B+",
}
_DAY_COUNT = 365.0


def simple_to_compound_pct_series(simple_pct: pd.Series, tenor_days: pd.Series) -> pd.Series:
    """Basit→bileşik faiz, YÜZDE in → YÜZDE out (vektörize).
    Port: engine/np_agg.py simple_to_compound_pct_series (L95-110) — birebir."""
    r = simple_pct.to_numpy(dtype=float) / 100.0
    t = tenor_days.to_numpy(dtype=float)
    valid = (t > 0) & np.isfinite(t) & np.isfinite(r)
    out = np.full(r.shape, np.nan)
    period = 1.0 + r[valid] * (t[valid] / _DAY_COUNT)
    ok = period > 0
    idx = np.where(valid)[0][ok]
    out[idx] = (np.power(period[ok], _DAY_COUNT / t[valid][ok]) - 1.0) * 100.0
    return pd.Series(out, index=simple_pct.index)


def normalize_np(df: pd.DataFrame) -> pd.DataFrame:
    """new_production_analysis → NP iç şeması.
    Port: engine/np_agg.py _normalize_sql_df (L137-166)."""
    out = pd.DataFrame()
    out["DAT"] = pd.to_datetime(df["VAL_DT"])
    out["CCY_CODE"] = df["CCY_CODE"]
    out["CUST_TP"] = df["CUST_TP"]
    out["RELATED_PC"] = (df["RELATED_PC_CODE"].fillna("Bilinmiyor")
                         if "RELATED_PC_CODE" in df.columns else "Bilinmiyor")
    out["AUM_BAND"] = df["AUM_LOWER"].map(_NP_AUM_LABELS).fillna("Bilinmiyor")
    out["TENOR_GRP"] = df["VADE_BUCKET"].fillna("99_DIGER")
    out["SUB_SEGMENT"] = df["SUB_SEGMENT"].fillna("Diger")
    out["NP_HACIM"] = df["TRY_BAKIYE_TOPLAM"].fillna(0) / 1e6
    out["YENI_PARA"] = df["YENI_PARA_TOPLAM"].fillna(0) / 1e6
    out["OS_BAKIYE"] = df["TRY_BAKIYE_TOPLAM"].fillna(0) / 1e6
    out["NP_FAIZ"] = df["WAVG_INTRST_RT"].fillna(0)
    out["TENOR_DAYS"] = (df["WAVG_DTM"].fillna(0)
                         if "WAVG_DTM" in df.columns else 0.0)
    return out


# ── Outstanding ortak şema (port: engine/outstanding_daily.py L36-176) ──────

OS_AUM_TO_COMMON = {
    "AUM_0_100K": "0-1M", "AUM_100K_500K": "0-1M", "AUM_500K_1M": "0-1M",
    "AUM_1M_5M": "1M-5M",
    "AUM_5M_10M": "5M-10M",
    "AUM_10M_20M": "10M-25M", "AUM_20M_25M": "10M-25M",
    "AUM_25M_30M": "25M-50M", "AUM_30M_50M": "25M-50M",
    "AUM_50M_75M": "50M-100M", "AUM_75M_100M": "50M-100M",
    "AUM_100M_200M": "100M-200M",
    "AUM_200M+": "200M+",
}
OS_TENOR_TO_COMMON = {
    "1-3": "1-3", "4-31": "4-31", "32-45": "32-45",
    "46-60": "46-91", "61-91": "46-91",
    "92-149": "92-181", "150-181": "92-181",
    "182-273": "182-273", "274-365": "274-365",
    "366-725": "366+", "726-10000": "366+",
}


def _require_mapped(series: pd.Series, mapping: dict, label: str) -> pd.Series:
    mapped = series.map(mapping)
    unknown = sorted(set(series[mapped.isna() & series.notna()].astype(str).unique()))
    if unknown:
        raise ValueError(f"outstanding: '{label}' içinde haritada olmayan değer(ler): "
                         f"{unknown} — eşleme tablosunu güncelle (sessiz drop yasak).")
    return mapped


def normalize_outstanding(raw: pd.DataFrame) -> pd.DataFrame:
    """daily_deposit → New Business stok ortak şeması.
    Port: engine/outstanding_daily.py load_outstanding_daily (L127-176)."""
    if "TYPE2" in raw.columns:
        raw = raw[raw["TYPE2"].astype(str).isin(["Vadeli", "Kasa", "O/N"])].copy()

    out = pd.DataFrame()
    out["DAT"] = pd.to_datetime(raw["DAT"])
    out["CHANNEL"] = raw["SEGMENT"].astype(str)
    out["CUST_TP"] = raw["CUST_TP"].astype(str) if "CUST_TP" in raw.columns else "?"
    out["AUM_COMMON"] = _require_mapped(raw["AUM_TYPE"].astype(str),
                                        OS_AUM_TO_COMMON, "AUM_TYPE")
    tc = pd.Series([None] * len(raw), index=raw.index, dtype=object)
    if "TYPE2" in raw.columns:
        _t2 = raw["TYPE2"].astype(str)
        tc[_t2.isin(["Kasa", "O/N"])] = "1-3"
        if "VADE_BUCKET" in raw.columns:
            _vm = _t2 == "Vadeli"
            if _vm.any():
                tc.loc[_vm] = _require_mapped(
                    raw.loc[_vm, "VADE_BUCKET"].astype(str),
                    OS_TENOR_TO_COMMON, "VADE_BUCKET")
    out["TENOR_COMMON"] = tc
    out["OS_BAKIYE"] = pd.to_numeric(raw["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0) / 1e6
    out["OS_FAIZ"] = pd.to_numeric(raw["AGIRLIKLI_ORT_FAIZ"], errors="coerce")
    return out


# ── Nihai df'ler (plot-hazır kesim) ─────────────────────────────────────────

def snapshot_final(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """date × DIM_* → BALANCE, WR_SUM, CUST_COUNT — Cost/Balance sayfalarının
    waterfall/heatmap/bar/drill'lerinin tamamı bu grenin iki-snapshot saf
    fonksiyonudur (motorlardaki _wr = BALANCE·INTEREST_RATE ile aynı)."""
    g = df.copy()
    g["WR_SUM"] = g["BALANCE"] * g["INTEREST_RATE"]
    return (g.groupby([date_col] + DIM_COLS, dropna=False)
            .agg({"BALANCE": "sum", "WR_SUM": "sum", "CUST_COUNT": "sum"})
            .reset_index())


_TENOR_MODES = {  # mod → (kova kolonu, vade-günü kolon adayları)
    "tenor": ("DIM_BUCKET", ["TENOR_RATE", "AGIRLIKLI_ORT_TENOR"]),
    "dtm":   ("DIM_BUCKET_DTM", ["DTM_RATE", "AGIRLIKLI_ORT_DTM"]),
}


def tenor_final(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """date × MODE × kova × DIM_* → BALANCE, WR_SUM, WT_SUM. Tenor sayfasının
    snapshot + günlük-evrim grafikleri (motorlardaki _wr/_wt ile aynı)."""
    frames = []
    for mode, (bucket_col, wt_candidates) in _TENOR_MODES.items():
        wt_col = next((c for c in wt_candidates if c in df.columns), None)
        if bucket_col not in df.columns or wt_col is None:
            log.warning("tenor: %s modu atlandı (%s/%s yok)", mode, bucket_col, wt_candidates)
            continue
        g = df.copy()
        g["WR_SUM"] = g["BALANCE"] * g["INTEREST_RATE"]
        g["WT_SUM"] = g["BALANCE"] * g[wt_col].fillna(0.0)
        g = (g.groupby([date_col, bucket_col] + DIM_COLS, dropna=False)
             .agg({"BALANCE": "sum", "WR_SUM": "sum", "WT_SUM": "sum"})
             .reset_index()
             .rename(columns={bucket_col: "DIM_BUCKET"}))
        g.insert(1, "TENOR_MODE", mode)
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def np_flow_final(np_df: pd.DataFrame) -> pd.DataFrame:
    """gün × 6 boyut; WC_SUM/WT_SUM motorun _agg_window'uyla birebir
    (app.py L6119-6129: _comp = pct_series(NP_FAIZ, TENOR_DAYS); _wc=_comp·hacim)."""
    df = np_df.copy()
    df["_comp"] = simple_to_compound_pct_series(df["NP_FAIZ"], df["TENOR_DAYS"])
    df["WC_SUM"] = df["_comp"] * df["NP_HACIM"]
    df["WT_SUM"] = df["TENOR_DAYS"] * df["NP_HACIM"]
    dims = ["CCY_CODE", "CUST_TP", "RELATED_PC", "AUM_BAND", "TENOR_GRP", "SUB_SEGMENT"]
    return (df.groupby(["DAT"] + dims, dropna=False)
            .agg({"NP_HACIM": "sum", "YENI_PARA": "sum", "OS_BAKIYE": "sum",
                  "WC_SUM": "sum", "WT_SUM": "sum"})
            .reset_index())


def np_out_final(od: pd.DataFrame) -> pd.DataFrame:
    """gün × ortak boyutlar → BAL_SUM, WR_SUM (stok AS-OF; New Business
    heatmap/bubble'ın payda tarafı)."""
    g = od.copy()
    g["WR_SUM"] = g["OS_BAKIYE"] * g["OS_FAIZ"]
    g["TENOR_COMMON"] = g["TENOR_COMMON"].fillna("")
    return (g.groupby(["DAT", "CHANNEL", "CUST_TP", "AUM_COMMON", "TENOR_COMMON"],
                      dropna=False)
            .agg(BAL_SUM=("OS_BAKIYE", "sum"), WR_SUM=("WR_SUM", "sum"))
            .reset_index())


def build_final_dfs(raws: dict, rollings_start: str, rollings_end: str) -> dict[str, pd.DataFrame]:
    monthly = normalize_monthly(raws["TRY_DEPOSIT_DETAIL"])
    daily = normalize_daily(raws["daily_deposit"])
    roll_agg = normalize_weekly_agg(raws["weekly_rollings"])
    roll_full = normalize_weekly_full(raws["weekly_rollings_full"])
    np_df = normalize_np(raws["new_production_analysis"])
    od = normalize_outstanding(raws["daily_deposit"])

    ws = pd.to_datetime(rollings_start, format="%d/%m/%Y")
    we = pd.to_datetime(rollings_end, format="%d/%m/%Y")
    for r in (roll_agg, roll_full):
        if not r.empty:
            r.insert(0, "WINDOW_START", ws)
            r.insert(1, "WINDOW_END", we)

    return {
        "PRISMA_DEP_OUT_MONTHLY":   snapshot_final(monthly, "MONTH"),
        "PRISMA_DEP_OUT_DAILY":     snapshot_final(daily, "DAT"),
        "PRISMA_DEP_TENOR_MONTHLY": tenor_final(monthly, "MONTH"),
        "PRISMA_DEP_TENOR_DAILY":   tenor_final(daily, "DAT"),
        "PRISMA_DEP_ROLL_AGG":      roll_agg,
        "PRISMA_DEP_ROLL_DETAIL":   roll_full,
        "PRISMA_NP_FLOW_DAILY":     np_flow_final(np_df),
        "PRISMA_NP_OUT_DAILY":      np_out_final(od),
    }


# ════════════════════════════════════════════════════════════════════════════
# 3a. YAZ — DDL'ler + tam-yenileme insert
# ════════════════════════════════════════════════════════════════════════════

DDL = {
    "PRISMA_DEP_OUT_MONTHLY": """CREATE TABLE {t} (
        MONTH DATE, DIM_PRODUCT VARCHAR2(64), DIM_SUBPRODUCT VARCHAR2(64),
        DIM_CUSTOMER VARCHAR2(16), DIM_AUM VARCHAR2(64), DIM_SEGMENT VARCHAR2(32),
        BALANCE NUMBER, WR_SUM NUMBER, CUST_COUNT NUMBER, LOADED_AT DATE)""",
    "PRISMA_DEP_OUT_DAILY": """CREATE TABLE {t} (
        DAT DATE, DIM_PRODUCT VARCHAR2(64), DIM_SUBPRODUCT VARCHAR2(64),
        DIM_CUSTOMER VARCHAR2(16), DIM_AUM VARCHAR2(64), DIM_SEGMENT VARCHAR2(32),
        BALANCE NUMBER, WR_SUM NUMBER, CUST_COUNT NUMBER, LOADED_AT DATE)""",
    "PRISMA_DEP_TENOR_MONTHLY": """CREATE TABLE {t} (
        MONTH DATE, TENOR_MODE VARCHAR2(8), DIM_BUCKET VARCHAR2(32),
        DIM_PRODUCT VARCHAR2(64), DIM_SUBPRODUCT VARCHAR2(64),
        DIM_CUSTOMER VARCHAR2(16), DIM_AUM VARCHAR2(64), DIM_SEGMENT VARCHAR2(32),
        BALANCE NUMBER, WR_SUM NUMBER, WT_SUM NUMBER, LOADED_AT DATE)""",
    "PRISMA_DEP_TENOR_DAILY": """CREATE TABLE {t} (
        DAT DATE, TENOR_MODE VARCHAR2(8), DIM_BUCKET VARCHAR2(32),
        DIM_PRODUCT VARCHAR2(64), DIM_SUBPRODUCT VARCHAR2(64),
        DIM_CUSTOMER VARCHAR2(16), DIM_AUM VARCHAR2(64), DIM_SEGMENT VARCHAR2(32),
        BALANCE NUMBER, WR_SUM NUMBER, WT_SUM NUMBER, LOADED_AT DATE)""",
    "PRISMA_DEP_ROLL_AGG": """CREATE TABLE {t} (
        WINDOW_START DATE, WINDOW_END DATE, ROLL_DATE DATE,
        CURRENCY VARCHAR2(8), CCY_CODE VARCHAR2(8), CUST_TP VARCHAR2(4),
        AUM_LOWER NUMBER, AUM_BAND VARCHAR2(16),
        ISLEM_SAYISI NUMBER, MUSTERI_SAYISI NUMBER,
        TRY_BAKIYE_TOPLAM NUMBER, ORIG_BAKIYE_TOPLAM NUMBER,
        TRY_X_INTRST NUMBER, TRY_X_DTM NUMBER, LOADED_AT DATE)""",
    "PRISMA_DEP_ROLL_DETAIL": """CREATE TABLE {t} (
        WINDOW_START DATE, WINDOW_END DATE, CUST_ID NUMBER, ACCT_ID NUMBER,
        FULL_NM VARCHAR2(128), ROLL_DATE DATE, VAL_DT DATE,
        CURRENCY VARCHAR2(8), CCY_CODE VARCHAR2(8), CUST_TP VARCHAR2(4),
        AUM_LOWER NUMBER, TOTAL_AUM NUMBER, AUM_BAND VARCHAR2(16),
        TRY_BALANCE NUMBER, ORIG_BALANCE NUMBER, INTRST_RT NUMBER,
        EFF_INTRST NUMBER, DTM NUMBER, KAMPANYA_ADI VARCHAR2(256),
        HAS_KAMPANYA NUMBER(1), SEGMENT VARCHAR2(16),
        ISPRIVATE NUMBER(1), ISAFFLUENT NUMBER(1), ISMAASLI NUMBER(1),
        ISNPO NUMBER(1), LOADED_AT DATE)""",
    "PRISMA_NP_FLOW_DAILY": """CREATE TABLE {t} (
        DAT DATE, CCY_CODE VARCHAR2(8), CUST_TP VARCHAR2(4),
        RELATED_PC VARCHAR2(64), AUM_BAND VARCHAR2(16), TENOR_GRP VARCHAR2(16),
        SUB_SEGMENT VARCHAR2(32), NP_HACIM NUMBER, YENI_PARA NUMBER,
        OS_BAKIYE NUMBER, WC_SUM NUMBER, WT_SUM NUMBER, LOADED_AT DATE)""",
    "PRISMA_NP_OUT_DAILY": """CREATE TABLE {t} (
        DAT DATE, CHANNEL VARCHAR2(8), CUST_TP VARCHAR2(4),
        AUM_COMMON VARCHAR2(16), TENOR_COMMON VARCHAR2(16),
        BAL_SUM NUMBER, WR_SUM NUMBER, LOADED_AT DATE)""",
}


def _ddl_columns(name: str) -> list[str]:
    """DDL gövdesinden kolon adlarını sırayla çıkar. Tanımlar virgülle
    ayrılır; tip parantezlerinde virgül YOK (NUMBER/DATE/VARCHAR2(n)/NUMBER(1))
    — bu sözleşme bozulursa (örn. NUMBER(18,2)) burayı da güncelle."""
    body = DDL[name].split("(", 1)[1].rsplit(")", 1)[0]
    return [seg.strip().split()[0] for seg in body.split(",") if seg.strip()]


def grant_tables(con, schema: str, tables: list[str], grantees: list[str]) -> None:
    """Üretilen tablolara SELECT grant'i ver (idempotent — Oracle'da yeniden
    grant hata değildir). Grant hatası koşuyu düşürmez, görünür loglanır."""
    cur = con.cursor()
    try:
        for table in tables:
            for grantee in grantees:
                try:
                    cur.execute(f"GRANT SELECT ON {schema}.{table} TO {grantee}")
                    log.info("   ✓ GRANT SELECT ON %s.%s TO %s", schema, table, grantee)
                except Exception as exc:
                    log.error("   ✗ grant başarısız (%s.%s → %s): %s",
                              schema, table, grantee, exc)
        con.commit()
    finally:
        cur.close()


def _table_exists(con, schema: str, table: str) -> bool:
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = :t",
            {"o": schema.upper(), "t": table.upper()},
        )
        return cur.fetchone()[0] > 0
    finally:
        cur.close()


def _to_db_value(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if v is pd.NaT:
        return None
    return v


def write_table(con, schema: str, name: str, df: pd.DataFrame) -> None:
    """CREATE (yoksa) + DELETE + batched executemany INSERT (tam yenileme)."""
    qualified = f"{schema}.{name}"
    if not _table_exists(con, schema, name):
        ddl = DDL[name].format(t=qualified)
        log.info("   CREATE TABLE %s", qualified)
        cur = con.cursor()
        try:
            cur.execute(ddl)
        finally:
            cur.close()

    out = df.copy()
    out["LOADED_AT"] = datetime.now()
    # DDL kolon sırasına hizala (fazla/eksik kolon erken patlasın).
    ddl_cols = _ddl_columns(name)
    missing = [c for c in ddl_cols if c not in out.columns]
    if missing:
        raise RuntimeError(f"{name}: df'te eksik kolon(lar): {missing}")
    out = out[ddl_cols]

    rows = [tuple(_to_db_value(v) for v in row)
            for row in out.itertuples(index=False, name=None)]
    binds = ", ".join(f":{i + 1}" for i in range(len(ddl_cols)))
    insert_sql = (f"INSERT INTO {qualified} ({', '.join(ddl_cols)}) "
                  f"VALUES ({binds})")

    cur = con.cursor()
    try:
        cur.execute(f"DELETE FROM {qualified}")
        for i in range(0, len(rows), 50_000):
            cur.executemany(insert_sql, rows[i:i + 50_000])
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()
    log.info("   ✓ %s: %d satır yazıldı", qualified, len(rows))


# ════════════════════════════════════════════════════════════════════════════
# 3b. DOKÜMANTASYON — S3 tablo dokümanları (Prisma keşif/filtre katmanı)
# ════════════════════════════════════════════════════════════════════════════

def _col(type_, desc, tag=None, role=None, filterable=None, aggregatable=False):
    d = {"type": type_, "description": desc}
    if tag:
        d.update(filterable=True, filter_role=role or "dimension",
                 suggested_semantic_tag=tag)
    if filterable is not None:
        d["filterable"] = filterable
    if aggregatable:
        d["aggregatable"] = True
    return d


_PAY_PAYDA_NOTU = (
    "Oran kolonları YOKTUR — pay/payda ayrı taşınır: oran = WR_SUM/BALANCE, "
    "ort. vade = WT_SUM/BALANCE. Hangi seviyede gruplarsan grupla bölmeyi "
    "sorguda yap."
)

_DIM_DOC = {
    "DIM_PRODUCT":    _col("VARCHAR2(64)", "Ürün (Vadeli/Kasa/O-N)", tag="product_group"),
    "DIM_SUBPRODUCT": _col("VARCHAR2(64)", "Alt ürün (O/N kırılımı: KGH/BTH/…)", tag="product_group"),
    "DIM_CUSTOMER":   _col("VARCHAR2(16)", "Müşteri tipi: G (gerçek) / T (tüzel)", tag="segment"),
    "DIM_AUM":        _col("VARCHAR2(64)", "AUM bandı (AUM_0_100K … AUM_200M+)", tag="other"),
    "DIM_SEGMENT":    _col("VARCHAR2(32)", "Segment (TC/SB/MI/KR/FB/BR)", tag="segment"),
}


def build_table_docs(schema: str, dfs: dict[str, pd.DataFrame]) -> list[dict]:
    """8 tablonun Prisma TableDoc YAML gövdeleri (S3TableDocStore.save şekli)."""
    docs = []

    def _core(name, desc, partition, cols):
        df = dfs.get(name)
        # TableDoc kuralı: suggested_semantic_tag varsa suggested_variable de
        # zorunlu — kolon adından türet (blok değişken adı önerisi).
        for cname, cdoc in cols.items():
            if cdoc.get("suggested_semantic_tag") and not cdoc.get("suggested_variable"):
                cdoc["suggested_variable"] = cname.lower()[:40]
        docs.append({
            "table": name, "schema": schema,
            "description": desc + "\n\n" + _PAY_PAYDA_NOTU,
            "partition_column": partition,
            "estimated_total_rows": int(len(df)) if df is not None else None,
            "columns": cols,
        })

    snap_cols = lambda datcol, datdesc: {
        datcol: _col("DATE", datdesc, tag="as_of_time", role="time_axis"),
        **_DIM_DOC,
        "BALANCE":    _col("NUMBER", "Toplam bakiye (TL)", aggregatable=True),
        "WR_SUM":     _col("NUMBER", "Σ(bakiye·faiz) — faiz payı; oran=WR_SUM/BALANCE", aggregatable=True),
        "CUST_COUNT": _col("NUMBER", "Müşteri adedi", aggregatable=True),
        "LOADED_AT":  _col("DATE", "Pipeline yazım zamanı (tazelik)", filterable=False),
    }
    _core("PRISMA_DEP_OUT_MONTHLY",
          "Mevduat stok AYLIK snapshot — Outstanding Cost/Balance sayfalarının "
          "waterfall, heatmap, ranked-bar ve AUM kompozisyon grafiklerinin kaynağı. "
          "İki tarih seçilir, fark/karşılaştırma sorguda alınır.",
          "MONTH", snap_cols("MONTH", "Ay sonu snapshot tarihi"))
    _core("PRISMA_DEP_OUT_DAILY",
          "Mevduat stok GÜNLÜK snapshot (haftasonsuz) — Cost/Balance günlük "
          "sekmeleri ve rate/balance drill zaman serileri.",
          "DAT", snap_cols("DAT", "İş günü"))

    tenor_cols = lambda datcol: {
        datcol: _col("DATE", "Snapshot tarihi", tag="as_of_time", role="time_axis"),
        "TENOR_MODE": _col("VARCHAR2(8)", "Vade modu: tenor (orijinal) | dtm (kalan)", tag="other"),
        "DIM_BUCKET": _col("VARCHAR2(32)", "Vade kovası (örn. 32-45)", tag="tenor_bucket"),
        **_DIM_DOC,
        "BALANCE": _col("NUMBER", "Toplam bakiye (TL)", aggregatable=True),
        "WR_SUM":  _col("NUMBER", "Σ(bakiye·faiz)", aggregatable=True),
        "WT_SUM":  _col("NUMBER", "Σ(bakiye·vade_günü); ort.vade=WT_SUM/BALANCE", aggregatable=True),
        "LOADED_AT": _col("DATE", "Pipeline yazım zamanı", filterable=False),
    }
    _core("PRISMA_DEP_TENOR_MONTHLY",
          "Vade merdiveni AYLIK snapshot — Outstanding Tenor sayfası (tenor+dtm modları).",
          "MONTH", tenor_cols("MONTH"))
    _core("PRISMA_DEP_TENOR_DAILY",
          "Vade merdiveni GÜNLÜK snapshot — Tenor günlük sekmesi + stacked-area evrim.",
          "DAT", tenor_cols("DAT"))

    _core("PRISMA_DEP_ROLL_AGG",
          "Vadesi dolan mevduat dönüşleri (ileriye dönük pencere) — Future Deposit "
          "Rollings pivot tabloları. Pencere: WINDOW_START→WINDOW_END; satır greni "
          "dönüş günü × para birimi × müşteri tipi × AUM bandı.",
          "ROLL_DATE", {
              "WINDOW_START": _col("DATE", "Sorgu penceresi başı", filterable=False),
              "WINDOW_END":   _col("DATE", "Sorgu penceresi sonu", filterable=False),
              "ROLL_DATE": _col("DATE", "Vade dönüş günü", tag="as_of_time", role="time_axis"),
              "CURRENCY":  _col("VARCHAR2(8)", "TRY/FX grubu", tag="currency"),
              "CCY_CODE":  _col("VARCHAR2(8)", "Para birimi", tag="currency"),
              "CUST_TP":   _col("VARCHAR2(4)", "G/T müşteri tipi", tag="segment"),
              "AUM_LOWER": _col("NUMBER", "AUM alt sınırı (ham)", filterable=False),
              "AUM_BAND":  _col("VARCHAR2(16)", "AUM bandı (6'lı)", tag="other"),
              "ISLEM_SAYISI":     _col("NUMBER", "İşlem adedi", aggregatable=True),
              "MUSTERI_SAYISI":   _col("NUMBER", "Müşteri adedi", aggregatable=True),
              "TRY_BAKIYE_TOPLAM": _col("NUMBER", "Dönen TRY bakiye", aggregatable=True),
              "ORIG_BAKIYE_TOPLAM": _col("NUMBER", "Orijinal para bakiyesi", aggregatable=True),
              "TRY_X_INTRST": _col("NUMBER", "Σ(bakiye·faiz) payı", aggregatable=True),
              "TRY_X_DTM":    _col("NUMBER", "Σ(bakiye·DTM) payı", aggregatable=True),
              "LOADED_AT": _col("DATE", "Pipeline yazım zamanı", filterable=False),
          })
    _core("PRISMA_DEP_ROLL_DETAIL",
          "Dönüş penceresi hesap-seviyesi detay — Rollings segment donut/stack, "
          "müşteri tablosu ve DTM histogramı. FULL_NM maskelidir (PII).",
          "ROLL_DATE", {
              "WINDOW_START": _col("DATE", "Pencere başı", filterable=False),
              "WINDOW_END":   _col("DATE", "Pencere sonu", filterable=False),
              "CUST_ID":  _col("NUMBER", "Müşteri no", tag="deal_id"),
              "ACCT_ID":  _col("NUMBER", "Hesap no", filterable=False),
              "FULL_NM":  _col("VARCHAR2(128)", "Müşteri adı (MASKELİ)", filterable=False),
              "ROLL_DATE": _col("DATE", "Vade dönüş günü", tag="as_of_time", role="time_axis"),
              "VAL_DT":   _col("DATE", "Hesap açılış valörü", filterable=False),
              "CURRENCY": _col("VARCHAR2(8)", "TRY/FX grubu", tag="currency"),
              "CCY_CODE": _col("VARCHAR2(8)", "Para birimi", tag="currency"),
              "CUST_TP":  _col("VARCHAR2(4)", "G/T", tag="segment"),
              "AUM_LOWER": _col("NUMBER", "AUM alt sınırı", filterable=False),
              "TOTAL_AUM": _col("NUMBER", "Müşteri toplam AUM", aggregatable=True),
              "AUM_BAND": _col("VARCHAR2(16)", "AUM bandı (6'lı)", tag="other"),
              "TRY_BALANCE":  _col("NUMBER", "TRY bakiye", aggregatable=True),
              "ORIG_BALANCE": _col("NUMBER", "Orijinal bakiye", aggregatable=True),
              "INTRST_RT":  _col("NUMBER", "Faiz oranı (satır)", filterable=False),
              "EFF_INTRST": _col("NUMBER", "Efektif faiz", filterable=False),
              "DTM":        _col("NUMBER", "Vadeye kalan gün", filterable=False),
              "KAMPANYA_ADI": _col("VARCHAR2(256)", "Kampanya adı", tag="other"),
              "HAS_KAMPANYA": _col("NUMBER(1)", "Kampanyalı mı (0/1)", tag="other"),
              "SEGMENT": _col("VARCHAR2(16)", "Türetilmiş segment (NPO/Tüzel/Private/Affluent/Maaşlı/Diğer)", tag="segment"),
              "ISPRIVATE":  _col("NUMBER(1)", "Private bayrağı", filterable=False),
              "ISAFFLUENT": _col("NUMBER(1)", "Affluent bayrağı", filterable=False),
              "ISMAASLI":   _col("NUMBER(1)", "Maaşlı bayrağı", filterable=False),
              "ISNPO":      _col("NUMBER(1)", "NPO bayrağı", filterable=False),
              "LOADED_AT":  _col("DATE", "Pipeline yazım zamanı", filterable=False),
          })
    _core("PRISMA_NP_FLOW_DAILY",
          "Yeni üretim (bağlanan mevduat) GÜNLÜK akışı — New Business sayfasının "
          "tüm grafikleri (AUM-rate, bubble'lar, rate-volume heatmap, cell "
          "timeseries, volume-pricing). Bileşik faiz payı WC_SUM motorla aynı "
          "yüzde-vektörize çevrimle üretilir.",
          "DAT", {
              "DAT":       _col("DATE", "Valör günü", tag="as_of_time", role="time_axis"),
              "CCY_CODE":  _col("VARCHAR2(8)", "Para birimi", tag="currency"),
              "CUST_TP":   _col("VARCHAR2(4)", "G/T", tag="segment"),
              "RELATED_PC": _col("VARCHAR2(64)", "İlişkili kâr merkezi", tag="product_group"),
              "AUM_BAND":  _col("VARCHAR2(16)", "AUM bandı (11'li ince)", tag="other"),
              "TENOR_GRP": _col("VARCHAR2(16)", "Vade grubu (01_1-3 … 11_540+)", tag="tenor_bucket"),
              "SUB_SEGMENT": _col("VARCHAR2(32)", "Alt segment", tag="segment"),
              "NP_HACIM":  _col("NUMBER", "Bağlanan toplam bakiye (₺M)", aggregatable=True),
              "YENI_PARA": _col("NUMBER", "Yeni para (₺M)", aggregatable=True),
              "OS_BAKIYE": _col("NUMBER", "Bağlanan bakiye (₺M, NP_HACIM eşi)", aggregatable=True),
              "WC_SUM":    _col("NUMBER", "Σ(bileşik_faiz%·hacim) payı", aggregatable=True),
              "WT_SUM":    _col("NUMBER", "Σ(vade_günü·hacim) payı", aggregatable=True),
              "LOADED_AT": _col("DATE", "Pipeline yazım zamanı", filterable=False),
          })
    _core("PRISMA_NP_OUT_DAILY",
          "Mevduat stoğu ortak şemada (8'li AUM/tenor bandı) GÜNLÜK AS-OF — New "
          "Business heatmap/bubble'ın outstanding (payda) tarafı. Kasa & O/N "
          "tenor '1-3' kovasına yazılır (dashboard kuralı).",
          "DAT", {
              "DAT":     _col("DATE", "Snapshot günü", tag="as_of_time", role="time_axis"),
              "CHANNEL": _col("VARCHAR2(8)", "Kanal/segment (TC/SB/MI/KR/FB/BR)", tag="segment"),
              "CUST_TP": _col("VARCHAR2(4)", "G/T", tag="segment"),
              "AUM_COMMON":   _col("VARCHAR2(16)", "Ortak AUM bandı (8'li)", tag="other"),
              "TENOR_COMMON": _col("VARCHAR2(16)", "Ortak vade kovası (8'li; ''=bilinmiyor)", tag="tenor_bucket"),
              "BAL_SUM": _col("NUMBER", "Stok bakiye (₺M)", aggregatable=True),
              "WR_SUM":  _col("NUMBER", "Σ(bakiye·faiz%) payı", aggregatable=True),
              "LOADED_AT": _col("DATE", "Pipeline yazım zamanı", filterable=False),
          })
    return docs


def publish_docs(dc, docs: list[dict]) -> None:
    from presentations.table_docs.schema import TableDoc
    from presentations.table_docs.store import S3TableDocStore

    store = S3TableDocStore(dc)
    for raw in docs:
        doc = TableDoc.model_validate(raw)
        store.save(doc)
        log.info("   ✓ doküman S3'e yazıldı: %s.%s", doc.schema_name, doc.table)


# ════════════════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    today = date.today()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--rollings-start", default=today.strftime("%d/%m/%Y"))
    p.add_argument("--rollings-end",
                   default=(today + timedelta(days=28)).strftime("%d/%m/%Y"))
    p.add_argument("--only", default=None, help="Virgülle ayrılmış tablo alt kümesi")
    p.add_argument("--schema", default=None,
                   help="Hedef şema (varsayılan: bağlantı kullanıcısı)")
    p.add_argument("--grant-to", default="A63837",
                   help="Tablolara SELECT verilecek kullanıcı(lar), virgülle "
                        "ayrılmış (varsayılan: A63837; boş ver → grant yok)")
    p.add_argument("--skip-db", action="store_true")
    p.add_argument("--skip-docs", action="store_true")
    args = p.parse_args(argv)

    from DataClient import DataClient
    dc = DataClient()
    con = dc.get_connection()
    try:
        schema = (args.schema or con.username).upper()
        log.info("Bağlantı OK — hedef şema: %s", schema)

        raws = fetch_all(dc, con, args.rollings_start, args.rollings_end)
        dfs = build_final_dfs(raws, args.rollings_start, args.rollings_end)

        only = ({t.strip().upper() for t in args.only.split(",")}
                if args.only else None)
        if only:
            unknown = only - set(dfs)
            if unknown:
                raise SystemExit(f"--only bilinmeyen tablo(lar): {sorted(unknown)}")
            dfs = {k: v for k, v in dfs.items() if k in only}

        for name, df in dfs.items():
            log.info("── %s: %d satır", name, len(df))
            if df.empty and not name.startswith("PRISMA_DEP_ROLL"):
                raise RuntimeError(f"{name} boş — kaynak sorguyu kontrol et")
            if not args.skip_db:
                write_table(con, schema, name, df)

        grantees = [g.strip().upper() for g in (args.grant_to or "").split(",") if g.strip()]
        if not args.skip_db and grantees:
            log.info("── Grant'ler veriliyor (%s)…", ", ".join(grantees))
            grant_tables(con, schema, list(dfs), grantees)

        if not args.skip_docs:
            log.info("── Tablo dokümanları S3'e yazılıyor…")
            publish_docs(dc, build_table_docs(schema, dfs))
    finally:
        dc.drop_connection(con)

    log.info("Bitti.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

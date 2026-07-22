"""
engine/outstanding_daily.py — Gerçek outstanding (stok) günlük bakiye kaynağı.

New Business heatmap'inde ORAN ve BAĞLANAN HACİM new-production akımından
(MEVDUAT_DONUSLER_FULLDATA) gelirken, OUTSTANDING bakiye gerçek stok defterinden
gelir: `daily_deposit` (← A16438.DEPOSITUSAGE_NEW), günlük granülerlik.

İki kaynağın boyutları farklı binlendiğinden, heatmap ortak bir şemada hizalanır:
  • Kanal: new-prod RELATED_PC (TC/SB/MI/KR/FB/BR) == daily_deposit SEGMENT.
  • AUM:   ortak 8-band (her iki kaynağın AUM bandları bu 8'e temiz map'lenir).
  • Tenor: ortak 8-bucket (new-prod DTM bandları ↔ outstanding MATURITY_INF-SUP).

Pencere semantiği (stok!) — POINT-IN-TIME AS-OF:
  • Outstanding stok, `end` tarihine AS-OF nokta-değeridir (o tarihe ≤ en yakın
    mevcut iş gününün hücre-toplamı). DAILY ve WEEKLY modlar arasında BAKİYE
    FARKI YOKTUR — ikisi de `end` snapshot'ıdır. Yeni-üretim hacmi/oranı
    penceresel (D/W) kalır; STOK ise noktasaldır.
  • Gerekçe: New Business heatmap/bubble outstanding deltası, Outstanding Balance
    Analysis (DailyBalanceEngine — exact-date point-in-time) ile birebir uzlaşmalı.
    Eski WEEKLY ortalama-günlük-bakiye yaklaşımı bu tutarlılığı bozuyordu.

Kırmızı çizgi (CLAUDE.md #9): AUM_TYPE / VADE_BUCKET eşleşmezse SESSİZ NaN drop
YASAK — _require_mapped() bilinmeyen değerde ValueError fırlatır.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

_OD_CACHE: Optional[pd.DataFrame] = None

# Heatmap ortak AUM 8-band (kolon sırası). "Bilinmiyor" = new-prod AUM_LOWER
# haritada değilse düşen sentinel (np_agg.py fillna); ortak şemada en sona,
# explicit bir kolon olarak durur (sessiz drop yerine görünür — bkz. #9).
COMMON_AUM_ORDER = [
    "0-1M", "1M-5M", "5M-10M", "10M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+",
    "Bilinmiyor",
]

# Heatmap ortak tenor 8-bucket (selection sırası).
COMMON_TENOR_ORDER = [
    "1-3", "4-31", "32-45", "46-91", "92-181", "182-273", "274-365", "366+",
]

# ── Ortak band eşlemeleri ────────────────────────────────────────────────────

# new-production AUM_BAND (AUM_LOWER etiketi) → ortak 8-band
NP_AUM_TO_COMMON = {
    "0-1M": "0-1M",
    "1M-2M": "1M-5M", "2M-5M": "1M-5M",
    "5M-10M": "5M-10M",
    "10M-25M": "10M-25M",
    "25M-50M": "25M-50M",
    "50M-100M": "50M-100M",
    "100M-200M": "100M-200M",
    "200M-500M": "200M+", "500M-1B": "200M+", "1B+": "200M+",
    "Bilinmiyor": "Bilinmiyor",   # new-prod sınıflanamayan AUM sentinel'i (explicit)
}

# outstanding AUM_TYPE → ortak 8-band
OS_AUM_TO_COMMON = {
    "AUM_0_100K": "0-1M", "AUM_100K_500K": "0-1M", "AUM_500K_1M": "0-1M",
    "AUM_1M_5M": "1M-5M",
    "AUM_5M_10M": "5M-10M",
    "AUM_10M_20M": "10M-25M", "AUM_20M_25M": "10M-25M",
    "AUM_25M_30M": "25M-50M", "AUM_30M_50M": "25M-50M",
    "AUM_50M_75M": "50M-100M", "AUM_75M_100M": "50M-100M",
    "AUM_100M_200M": "100M-200M",
    "AUM_200M+": "200M+",
    # LEGACY: daily_deposit 2025-09'a uzatılınca eski taksonomi geldi — o dönemde
    # 100M üstü TEK bant ('AUM_100M+', 100M-200M/200M+ ayrımı yok). Ortak şemada
    # dönemin en üst bandı → en üst banda ('200M+') yaklaştırılır (bant o
    # tarihlerde bölünemez; yalnız legacy satırları etkiler).
    "AUM_100M+": "200M+",
}

# new-production TENOR_GRP → ortak tenor bucket
NP_TENOR_TO_COMMON = {
    "01_1-3": "1-3",
    "02_4-31": "4-31",
    "03_32-35": "32-45", "04_36-45": "32-45",
    "05_46-60": "46-91", "06_61-91": "46-91",
    "07_92-181": "92-181",
    "08_182-273": "182-273",
    "09_274-365": "274-365",
    "10_366-540": "366+", "11_540+": "366+",
}

# outstanding VADE_BUCKET (MATURITY_INFIMUM-MATURITY_SUPREMUM) → ortak tenor bucket
OS_TENOR_TO_COMMON = {
    "1-3": "1-3",
    "4-31": "4-31",
    "32-45": "32-45",
    "46-60": "46-91", "61-91": "46-91",
    "92-149": "92-181", "150-181": "92-181",
    "182-273": "182-273",
    "274-365": "274-365",
    "366-725": "366+", "726-10000": "366+",
}

# Ortak bucket → new-prod TENOR_GRP listesi (new-prod filtresi için)
COMMON_TENOR_TO_NP_GRP = {}
for _grp, _common in NP_TENOR_TO_COMMON.items():
    COMMON_TENOR_TO_NP_GRP.setdefault(_common, []).append(_grp)

# Ortak bucket → outstanding VADE_BUCKET listesi (outstanding filtresi için)
COMMON_TENOR_TO_OS_VADE = {}
for _vade, _common in OS_TENOR_TO_COMMON.items():
    COMMON_TENOR_TO_OS_VADE.setdefault(_common, []).append(_vade)

# new-prod AUM_BAND → ortak band; ortak band → new-prod fine band listesi
COMMON_AUM_TO_NP_BANDS = {}
for _band, _common in NP_AUM_TO_COMMON.items():
    COMMON_AUM_TO_NP_BANDS.setdefault(_common, []).append(_band)


def _require_mapped(series: pd.Series, mapping: dict, label: str) -> pd.Series:
    """Map + sessiz NaN drop'u engelle (CLAUDE.md #9). Bilinmeyen değerde raise."""
    mapped = series.map(mapping)
    unknown = sorted(set(series[mapped.isna() & series.notna()].astype(str).unique()))
    if unknown:
        raise ValueError(
            "outstanding_daily: unmapped value(s) in '{}': {} "
            "— update the mapping table (silent drop is forbidden).".format(label, unknown)
        )
    return mapped


# ── Yükle & cache ────────────────────────────────────────────────────────────

def load_outstanding_daily() -> pd.DataFrame:
    """daily_deposit'i ortak şemaya normalize et + cache'le.

    Çıktı kolonları:
      DAT          datetime64
      CHANNEL      str   (SEGMENT: TC/SB/MI/KR/FB/BR)
      AUM_COMMON   str   (ortak 8-band)
      TENOR_COMMON str   (ortak tenor bucket; VADE_BUCKET yoksa None — dev)
      OS_BAKIYE    float (TL-milyon)
      OS_FAIZ      float (percent)
    """
    global _OD_CACHE
    if _OD_CACHE is not None:
        return _OD_CACHE

    from ..data_source import load_dataframe  # port: db_source yerine
    raw = load_dataframe("daily_deposit")

    # Vadeli (time) + Kasa (vault) + O/N (overnight). Kasa & O/N efektif ≤1-3 gün
    # (gecelik / kasa) → ortak "1-3" tenor kovasına yazılır; böylece Tenor Y ekseninde
    # "1-3" satırı TÜM gecelik + kasa stoğunu da içerir (kullanıcı isteği). Vadeli kendi
    # VADE_BUCKET'inden map'lenir. Diğer türler (vadesiz/demand) sorguda zaten yok.
    if "TYPE2" in raw.columns:
        raw = raw[raw["TYPE2"].astype(str).isin(["Vadeli", "Kasa", "O/N"])].copy()

    out = pd.DataFrame()
    out["DAT"]        = pd.to_datetime(raw["DAT"])
    out["CHANNEL"]    = raw["SEGMENT"].astype(str)
    out["CUST_TP"]    = raw["CUST_TP"].astype(str) if "CUST_TP" in raw.columns else "?"
    out["AUM_COMMON"] = _require_mapped(raw["AUM_TYPE"].astype(str), OS_AUM_TO_COMMON, "AUM_TYPE")

    # TENOR_COMMON: Kasa/O/N → "1-3" (zorla, en kısa vade); Vadeli → VADE_BUCKET map'i
    # (kolon varsa; dev'de yok → Vadeli için None).
    tc = pd.Series([None] * len(raw), index=raw.index, dtype=object)
    if "TYPE2" in raw.columns:
        _t2 = raw["TYPE2"].astype(str)
        tc[_t2.isin(["Kasa", "O/N"])] = "1-3"
        if "VADE_BUCKET" in raw.columns:
            _vm = (_t2 == "Vadeli")
            if _vm.any():
                tc.loc[_vm] = _require_mapped(
                    raw.loc[_vm, "VADE_BUCKET"].astype(str), OS_TENOR_TO_COMMON, "VADE_BUCKET")
    out["TENOR_COMMON"] = tc

    out["OS_BAKIYE"] = pd.to_numeric(raw["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0) / 1e6
    out["OS_FAIZ"]   = pd.to_numeric(raw["AGIRLIKLI_ORT_FAIZ"], errors="coerce")

    _OD_CACHE = out
    return _OD_CACHE


def reset_caches() -> None:
    """Process-ömrü cache'ini boşaltır (data-refresh endpoint'i çağırır)."""
    global _OD_CACHE
    _OD_CACHE = None


# ── Aggregation ──────────────────────────────────────────────────────────────

def aggregate_outstanding(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    freq: str = "D",
    channels: Optional[List[str]] = None,
    cust_tp: Optional[List[str]] = None,
    aum_commons: Optional[List[str]] = None,
    tenor_commons: Optional[List[str]] = None,
    aum_remap: Optional[dict] = None,
    row_dim: str = "CHANNEL",
    col_dim: str = "AUM_COMMON",
    row_remap: Optional[dict] = None,
    col_remap: Optional[dict] = None,
) -> dict:
    """(row_dim × col_dim[display]) → outstanding stok agregasyonu.

    row_dim / col_dim: eksen boyutu kolonları — "CHANNEL", "CUST_TP",
    "TENOR_COMMON" veya "AUM_COMMON" (AUM eksende aum_remap ile display banda
    çevrilir). Kaynakta yoksa / tümü None ise {} döner (o boyutta stok kırılımı
    yok → heatmap new-prod-only gösterir). Anahtar = "row_val|col_val".

    POINT-IN-TIME AS-OF (freq D/W FARK ETMEZ): outstanding = `end`e AS-OF nokta-
    değeri (≤ end en yakın mevcut iş gününün hücre-toplamı). start yok sayılır.
    → Outstanding Balance Analysis (exact-date point-in-time) ile birebir uzlaşır.

    aum_remap: AUM_COMMON → display band (heatmap AUM-merge için). None = kimlik.
    Dönen her hücre:
      os_bakiye (snapshot bakiye, TL-mn), os_faiz (balance-wavg %, ya da None),
      bal_sum + wr_sum (ham toplamlar — total'larda yeniden ağırlıklandırma için),
      n_days (=1; snapshot).
    """
    # Eksen boyutu kaynakta yok ya da tümü None ise (ör. dev'de TENOR_COMMON) →
    # stok kırılımı yapılamaz → boş dön (heatmap new-prod-only).
    for _dim in (row_dim, col_dim):
        if _dim not in df.columns or not df[_dim].notna().any():
            return {}

    # STOK snapshot semantiği (POINT-IN-TIME AS-OF): outstanding = `end` tarihine
    # AS-OF nokta-değeri — o tarihe ≤ en yakın mevcut iş gününün hücre-toplamı.
    # Böylece New Business outstanding deltası Balance Analysis (DailyBalanceEngine,
    # exact-date point-in-time) ile BİREBİR uzlaşır (kullanıcı isteği: NB heatmap/
    # bubble ↔ Outstanding Balance Analysis tutarlı). `end` mevcut bir tarihse
    # snapshot == o tarih; değilse son bilinen stok taşınır (weekend/gap'te sahte-
    # sıfır snapshot önlenir). freq (D/W) artık BAKİYEYİ ETKİLEMEZ — her iki mod da
    # `end` snapshot'ıdır (yeni-üretim hacmi/oranı penceresel kalır, stok noktasal).
    # snap_date içerik filtrelerinden ÖNCE (tüm tarih uzayından) seçilir → tüm
    # hücreler AYNI güne düşer, total'ların additivity'si korunur.
    avail = df["DAT"] <= end
    if not avail.any():
        return {}
    snap_date = df.loc[avail, "DAT"].max()
    date_mask = (df["DAT"] == snap_date)
    n_days = 1

    mask = date_mask.copy()
    if channels:
        mask &= df["CHANNEL"].isin(channels)
    if cust_tp:
        mask &= df["CUST_TP"].isin(cust_tp)
    if aum_commons:
        mask &= df["AUM_COMMON"].isin(aum_commons)
    if tenor_commons:
        # Bilinen tenor'u olan satırlar filtreye tabi; tenor'u bilinmeyen (None,
        # ör. dev Vadeli) satırlar dışlanmaz (tenor filtresi onlar için no-op).
        mask &= (df["TENOR_COMMON"].isna() | df["TENOR_COMMON"].isin(tenor_commons))
    f = df.loc[mask]
    if f.empty:
        return {}

    f = f.copy()
    f["_aum_disp"] = (f["AUM_COMMON"].map(lambda c: aum_remap.get(c, c))
                      if aum_remap else f["AUM_COMMON"])
    # AUM eksende display banda (merge grubu) çevrilmiş halini kullan.
    row_key = "_aum_disp" if row_dim == "AUM_COMMON" else row_dim
    col_key = "_aum_disp" if col_dim == "AUM_COMMON" else col_dim
    f = f[f[row_key].notna() & f[col_key].notna()]
    if f.empty:
        return {}
    # Generic satır/kolon merge (RELATED_PC=CHANNEL / CUST_TP) — new-prod tarafıyla
    # aynı grup adlarına relabel et ki hücreler "grup|col" anahtarında hizalansın.
    if row_remap:
        f[row_key] = f[row_key].astype(str).map(lambda v: row_remap.get(v, v))
    if col_remap:
        f[col_key] = f[col_key].astype(str).map(lambda v: col_remap.get(v, v))
    f["_wr"] = f["OS_FAIZ"] * f["OS_BAKIYE"]
    g = (
        f.groupby([row_key, col_key], observed=True)
        .agg(bal_sum=("OS_BAKIYE", "sum"), wr_sum=("_wr", "sum"))
        .reset_index()
    )
    out = {}
    for _, r in g.iterrows():
        k = "{}|{}".format(r[row_key], r[col_key])
        bal_sum = float(r["bal_sum"])
        wr_sum  = float(r["wr_sum"])
        bal = bal_sum / n_days                        # n_days=1 → snapshot bakiye
        faiz = (wr_sum / bal_sum) if bal_sum else None
        out[k] = {
            "os_bakiye": round(bal, 2),
            "os_faiz":   round(faiz, 4) if faiz is not None else None,
            "bal_sum":   bal_sum,
            "wr_sum":    wr_sum,
            "n_days":    n_days,
        }
    return out

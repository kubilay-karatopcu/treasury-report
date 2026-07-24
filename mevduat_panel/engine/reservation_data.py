"""Rezervasyon/fiyatlama veri katmanı — legacy ``DataOperations`` portu.

Kaynak: PRISMA öncesi üretim ``app.py`` (``DataOperations.process_data`` +
``load_competitor_data``). Legacy uygulama boot'ta iki paylaşılan bellek
DataFrame'i kurardı (``current_df`` + ``competitor_df``); tüm legacy sayfalar
(oranlar/miktarlar/historic/competitor) bunlardan okurdu — per-request Oracle
YOK, tazeleme batch (``/internal/refresh-data``).

Bu port o modeli mevduat_panel'in **process-lifetime cache + reset_caches()**
disiplinine taşır (kaynak repo davranışı: veri güncellemesi = restart ya da
``/mevduat-panel/admin/refresh``). Sorgular ``data_source.load_dataframe`` ile
koşar (PROD DataClient / DEV dev.db). Hesap mantığı legacy ile **birebir**:
IS_MAX_REVIZE bayrağı, iki filtre (RESERVATION_AMT ≥ 50k, OFFERED_RATE ≤
MARKET_MAX_RT×1.02), %99 percentile outlier kırpma, JSON-hazır tarih kolonları.

Bkz. docs/STATIK_DASHBOARD_ADAPTASYON.md §3.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..data_source import load_dataframe

log = logging.getLogger("mevduat_panel")

# ── Process-lifetime cache (kaynak davranış: tazeleme = reset + yeniden yükle) ─
_RESERVATION_CACHE: Optional[pd.DataFrame] = None
_COMPETITOR_CACHE: Optional[pd.DataFrame] = None


# ── Yardımcılar ──────────────────────────────────────────────────────────────

def _load_with_fallback(name: str, alt: str, params: dict | None = None) -> pd.DataFrame:
    """``name`` sorgusunu koşar; hata olursa ``alt`` (``_T_CUST``) aynasına düşer.

    Legacy try/except deseni: bazı ortamlarda birincil sorgu (T_CUST'suz)
    çalışmaz, müşteri-tipli aynası kullanılır. Her ikisi de aynı şema üretir.
    """
    try:
        return load_dataframe(name, params)
    except Exception:
        log.warning("reservation_data: %s başarısız, %s aynasına düşülüyor", name, alt)
        return load_dataframe(alt, params)


def _dedup_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Yinelenen kolon adlarını at (legacy: join'ler çift kolon üretebilir)."""
    return df.loc[:, ~df.columns.duplicated()].reset_index(drop=True)


def _custom_part_id(date_obj: datetime) -> object:
    """Legacy ``get_custom_part_id``: ay-sonu ise ``%m%Y``, değilse ``gün×10``.

    core_comparison partisyon kimliği; birincil bind bugün ``:val_dt`` olsa da
    fazla named bind güvenlidir (python-oracledb kullanılmayan bind'i yok sayar).
    """
    _, last_day = calendar.monthrange(date_obj.year, date_obj.month)
    if date_obj.day == last_day:
        return date_obj.strftime("%m%Y")
    return date_obj.day * 10


def _clear_outliers(df: pd.DataFrame, source_col: str, target_col: str,
                    q: float = 0.99) -> None:
    """Legacy ``clear_outliers``: q-percentile üstünü NaN'a çevirip ``target_col``e yaz.

    ``source_col`` yoksa sessiz geç (legacy'de kolon her zaman vardı; port
    dayanıklılığı için guard)."""
    if source_col not in df.columns:
        df[target_col] = np.nan
        return
    df[source_col] = pd.to_numeric(df[source_col], errors="coerce")
    p_limit = df[source_col].quantile(q)
    df[target_col] = np.where(df[source_col] <= p_limit, df[source_col], np.nan)


# ── current_df portu ─────────────────────────────────────────────────────────

def build_reservation_df() -> pd.DataFrame:
    """Legacy ``DataOperations.process_data`` birebir portu (cache'siz üretim).

    MYU + core_comparison + TREASURY birleşimi → temizlik → JSON-hazır kolonlar.
    """
    # ── MYU ──
    myu_df = _dedup_cols(_load_with_fallback("myu", "myu_T_CUST"))

    today = datetime.now()
    val_dt = today.strftime("%d/%m/%Y")
    part_id = _custom_part_id(today)
    part_id_t_1 = _custom_part_id(today - timedelta(days=1))

    core_df = _dedup_cols(_load_with_fallback(
        "core_comparison", "core_comparison_T_CUST",
        {"part_id": part_id, "part_id_t_1": part_id_t_1,
         "val_dt": val_dt, "mtrty_dt": val_dt},
    ))

    myu_df = pd.concat([myu_df, core_df], ignore_index=True)
    myu_df["DATE_TIME"] = pd.to_datetime(
        myu_df["CREATE_DT"].astype(str) + " "
        + myu_df["CREATE_TM"].astype(str).str.zfill(6),
        errors="coerce",
    )
    myu_df["DATA_SRC"] = "MYU"

    # ── TREASURY ──
    treasury_df = _load_with_fallback("treasury", "treasury_T_CUST")
    treasury_df.rename(columns={
        "RQSTD_INTRST_RT": "DEMANDED_RATE",
        "RECMMND_INTRST_RT": "SUGGESTED_PRICE",
        "APPRVD_INTRST_RT": "OFFERED_RATE",
        "CURRENCY_CD": "CCY_CODE",
        "MTRTY_STRT": "VADE_BASLANGIC",
        "MTRTY_END": "VADE_BITIS",
        "RSRVTN_DT": "CREATE_DT",
        "PRCNG_CNT": "TALEP_REVIZE_NO",
    }, inplace=True)
    treasury_df["DATA_SRC"] = "TREASURY"
    treasury_df["DATE_TIME"] = pd.to_datetime(
        treasury_df["CREATE_TM"].astype(str).str[:14],
        format="%Y%m%d%H%M%S", errors="coerce",
    )
    treasury_df = _dedup_cols(treasury_df)

    # ── Birleştir + sırala ──
    final_df = pd.concat([myu_df, treasury_df], ignore_index=True)
    final_df.sort_values(by=["DATE_TIME"], inplace=True)
    final_df.reset_index(drop=True, inplace=True)

    # ── IS_MAX_REVIZE ──
    final_df["IS_MAX_REVIZE"] = False
    final_df["TALEP_REVIZE_NO"] = final_df["TALEP_REVIZE_NO"].fillna(1)
    if not final_df.empty:
        group_cols = ["DATA_SRC", "CUST_ID", "CREATE_DT", "VADE_BASLANGIC"]
        max_idx = final_df.groupby(group_cols)["TALEP_REVIZE_NO"].idxmax()
        final_df.loc[max_idx, "IS_MAX_REVIZE"] = True

    # ── Temizlik (legacy sıra) ──
    final_df["OFFERED_RATE"] = pd.to_numeric(final_df["OFFERED_RATE"], errors="coerce")
    final_df = final_df[final_df["RESERVATION_AMT"] >= 50000].copy()
    final_df = final_df[final_df["OFFERED_RATE"] <= (final_df["MARKET_MAX_RT"] * 1.02)].copy()
    _clear_outliers(final_df, "COMPETITOR_BANK_RTS", "PERCENTILE_COMPETITOR_RTS")
    _clear_outliers(final_df, "DEMANDED_RATE", "PERCENTILE_DEMANDED_RTS")
    final_df.dropna(subset=["DATE_TIME"], inplace=True)

    # ── JSON-hazır kolonlar ──
    final_df["CREATE_DT"] = pd.to_datetime(final_df["CREATE_DT"])
    final_df["DATE_STR_CLEAN"] = final_df["CREATE_DT"].dt.strftime("%Y-%m-%d")
    final_df["DATE_TIME_STR"] = final_df["DATE_TIME"].dt.strftime("%Y-%m-%d %H:%M:%S")

    log.info("reservation_data: current_df kuruldu (%d satır)", len(final_df))
    return final_df


# ── competitor_df portu ──────────────────────────────────────────────────────

_COMPETITOR_EMPTY_COLS = [
    "TARIH", "VADE", "TUTAR", "FAIZ", "DOVIZ_CINSI", "KAYNAK", "URUN", "BANKA_ADI",
]


def _parse_range(val) -> tuple[int, int]:
    """Legacy ``parse_range``: "32-45 Gün" → (32,45); "500 Bin" → (500,500)."""
    import re
    if pd.isna(val):
        return 0, 0
    s = str(val)
    nums = re.findall(r"[\d]+(?:[.,]\d+)?", s.replace(".", "").replace(",", "."))
    nums = [float(n) for n in nums]
    if len(nums) >= 2:
        return int(min(nums)), int(max(nums))
    if len(nums) == 1:
        return int(nums[0]), int(nums[0])
    return 0, 0


def build_competitor_df() -> pd.DataFrame:
    """Legacy ``load_competitor_data`` birebir portu (cache'siz üretim)."""
    try:
        comp_df = load_dataframe("competitor_analysis")
    except Exception:
        log.exception("reservation_data: competitor_analysis yüklenemedi")
        comp_df = pd.DataFrame(columns=_COMPETITOR_EMPTY_COLS)

    if comp_df.empty:
        return comp_df

    comp_df[["VADE_MIN", "VADE_MAX"]] = comp_df["VADE"].apply(
        lambda x: pd.Series(_parse_range(x)))
    comp_df[["TUTAR_MIN", "TUTAR_MAX"]] = comp_df["TUTAR"].apply(
        lambda x: pd.Series(_parse_range(x)))

    comp_df["TARIH"] = pd.to_datetime(comp_df["TARIH"], errors="coerce")
    comp_df["DATE_STR"] = comp_df["TARIH"].dt.strftime("%Y-%m-%d")
    comp_df["FAIZ"] = pd.to_numeric(comp_df["FAIZ"], errors="coerce")
    comp_df.dropna(subset=["TARIH", "FAIZ"], inplace=True)

    log.info("reservation_data: competitor_df kuruldu (%d satır)", len(comp_df))
    return comp_df


# ── Public cache API (mevduat_panel engine sözleşmesi) ───────────────────────

def load_reservation_df() -> pd.DataFrame:
    """current_df eşdeğeri — cache'li. İlk çağrıda kurar, sonra cache döner."""
    global _RESERVATION_CACHE
    if _RESERVATION_CACHE is None:
        _RESERVATION_CACHE = build_reservation_df()
    return _RESERVATION_CACHE


def load_competitor_df() -> pd.DataFrame:
    """competitor_df eşdeğeri — cache'li."""
    global _COMPETITOR_CACHE
    if _COMPETITOR_CACHE is None:
        _COMPETITOR_CACHE = build_competitor_df()
    return _COMPETITOR_CACHE


def reset_caches() -> None:
    """prewarm.refresh_all tarafından çağrılır: sonraki yükleme SQL'i tazeler."""
    global _RESERVATION_CACHE, _COMPETITOR_CACHE
    _RESERVATION_CACHE = None
    _COMPETITOR_CACHE = None

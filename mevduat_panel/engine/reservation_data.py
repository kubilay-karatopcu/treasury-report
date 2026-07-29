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

# R10 Yenilik 2 — Ekstrem kuralının AUM eşiği (PARAMETRİK, kullanıcı kararı
# 2026-07-29): müşteri portföyü (PORTFOLIO_AMT) bu eşiğin ALTINDAysa
# EKSTREM_LIMIT_ALTI(_TUZEL), üstünde/eşitse ya da AUM bilinmiyorsa
# EKSTREM(_TUZEL) kullanılır.
AUM_LIMIT_ALTI_ESIK = 100_000_000

# Tüzel kolon setine yönlendiren müşteri tipleri ('G' = gerçek → normal set).
_TUZEL_CUST_TPS = ("F", "T")


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


# ── R10 Yenilik 2: MEVDUAT_YETKILER kural bazlı Ekstrem türetimi ─────────────

def derive_ekstrem_columns(df: pd.DataFrame, yetki: pd.DataFrame,
                           esik: float = AUM_LIMIT_ALTI_ESIK) -> pd.DataFrame:
    """Rezervasyon satırlarına kural bazlı EKSTREM / EKSTREM_YETKI yazar.

    Kural (kullanıcı kararı 2026-07-29):
    - Eşleşme: satırın günü (CREATE_DT) = yetki DAT'ı, para birimi (CCY_CODE)
      = DOVIZ, satır vadesi yetki bandının [VADE_BASLANGIC, VADE_BITIS]
      aralığında.
    - CUST_TP 'F'/'T' (tüzel) → *_TUZEL kolonları; 'G' (ve diğerleri) → normal.
    - AUM (PORTFOLIO_AMT) < ``esik`` → EKSTREM_LIMIT_ALTI; değilse (AUM
      bilinmiyorsa dahil) → EKSTREM.
    - EKSTREM_YETKI = seçilen ekstrem + ZARAR_YETKISI/100 (tüzelde _TUZEL).

    Eşleşmeyen satırların mevcut değerlerine DOKUNULMAZ (SQL'den gelen eski
    join değeri ya da NaN kalır) — kural tablosu eksik günlerde sayfa kırılmaz.
    Saf fonksiyon: girdileri değiştirmez, güncellenmiş kopya döner.
    """
    df = df.copy()
    for col in ("EKSTREM", "EKSTREM_YETKI"):
        if col not in df.columns:
            df[col] = np.nan
    if yetki is None or yetki.empty:
        return df

    y = yetki.copy()
    y.columns = [str(c).upper() for c in y.columns]
    need = ["DAT", "DOVIZ", "VADE_BASLANGIC", "VADE_BITIS", "EKSTREM",
            "EKSTREM_LIMIT_ALTI", "ZARAR_YETKISI", "EKSTREM_TUZEL",
            "EKSTREM_LIMIT_ALTI_TUZEL", "ZARAR_YETKISI_TUZEL"]
    missing = [c for c in need if c not in y.columns]
    if missing:
        log.warning("mevduat_yetkiler: eksik kolon %s — kural atlandı", missing)
        return df
    y = y[need].rename(columns={c: "Y_" + c for c in need})
    y["Y_DAT"] = pd.to_datetime(y["Y_DAT"], errors="coerce").dt.normalize()
    y["Y_DOVIZ"] = y["Y_DOVIZ"].astype(str).str.strip().str.upper()
    for c in y.columns:
        if c not in ("Y_DAT", "Y_DOVIZ"):
            y[c] = pd.to_numeric(y[c], errors="coerce")

    def _col(name: str, dtype=object) -> pd.Series:
        if name in df.columns:
            return df[name]
        return pd.Series(index=df.index, dtype=dtype)

    # Vade anahtarı: satır değeri sayı (32) da olabilir bant etiketi ("32-35")
    # de — İLK sayı alt sınırdır ve yetki bandının aralığına oturtulur.
    vade = pd.to_numeric(
        _col("VADE_BASLANGIC").astype(str).str.extract(r"(\d+)", expand=False),
        errors="coerce")

    left = pd.DataFrame({
        "_ROW": df.index,
        "_DAT": pd.to_datetime(_col("CREATE_DT"), errors="coerce").dt.normalize(),
        "_DOVIZ": _col("CCY_CODE").astype(str).str.strip().str.upper(),
        "_VADE": vade,
        "_TUZEL": _col("CUST_TP").astype(str).str.strip().str.upper()
                    .isin(_TUZEL_CUST_TPS),
        "_AUM": pd.to_numeric(_col("PORTFOLIO_AMT", float), errors="coerce"),
    })

    m = left.merge(y, left_on=["_DAT", "_DOVIZ"], right_on=["Y_DAT", "Y_DOVIZ"],
                   how="inner")
    m = m[(m["_VADE"] >= m["Y_VADE_BASLANGIC"]) & (m["_VADE"] <= m["Y_VADE_BITIS"])]
    if m.empty:
        return df
    # Bantlar çakışırsa en dar/alçak alt sınırlı bant kazanır (deterministik).
    m = m.sort_values(["_ROW", "Y_VADE_BASLANGIC"]).drop_duplicates("_ROW")

    ek_ust = np.where(m["_TUZEL"], m["Y_EKSTREM_TUZEL"], m["Y_EKSTREM"])
    ek_alt = np.where(m["_TUZEL"], m["Y_EKSTREM_LIMIT_ALTI_TUZEL"],
                      m["Y_EKSTREM_LIMIT_ALTI"])
    zarar = np.where(m["_TUZEL"], m["Y_ZARAR_YETKISI_TUZEL"], m["Y_ZARAR_YETKISI"])
    # AUM < eşik → limit-altı seti; AUM NaN karşılaştırması False → üst set.
    alt_mi = m["_AUM"].to_numpy() < esik
    ekstrem = pd.Series(np.where(alt_mi, ek_alt, ek_ust), index=m["_ROW"])
    yetki_v = ekstrem + pd.Series(zarar, index=m["_ROW"]) / 100.0

    upd_ek = ekstrem.dropna()
    df.loc[upd_ek.index, "EKSTREM"] = upd_ek
    upd_ey = yetki_v.dropna()
    df.loc[upd_ey.index, "EKSTREM_YETKI"] = upd_ey
    return df


def _apply_mevduat_yetkileri(df: pd.DataFrame) -> pd.DataFrame:
    """MEVDUAT_YETKILER'i yükleyip kuralı uygular; yüklenemezse df aynen döner."""
    try:
        yetki = load_dataframe("mevduat_yetkiler")
    except Exception:
        log.warning("reservation_data: mevduat_yetkiler yüklenemedi — "
                    "EKSTREM kolonları SQL değerleriyle kalıyor", exc_info=True)
        return df
    try:
        return derive_ekstrem_columns(df, yetki)
    except Exception:
        log.exception("reservation_data: ekstrem kural türetimi başarısız — "
                      "SQL değerleriyle devam")
        return df


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
    # DTYPE TUZAĞI (2026-07-29 veri-kaybı kökü): legacy bu veriyi DataClient'ın
    # S3 parquet önbelleğinden okurdu (CREATE_TM int/str olarak korunur); port
    # taze oracledb çeker → NUMBER kolonlar float64 gelir. float'ta
    # str(93015.0)='93015.0' → zfill(6) pad'lemez ve esnek parser 5 haneli saati
    # çözemez → 10:00 ÖNCESİ TÜM MYU SATIRLARI DÜŞERDİ (6 haneli '103015.0'
    # kesirli saniye gibi parse olup hayatta kalıyordu — o yüzden gün 10:00'da
    # "başlıyor" görünüyordu). Saat daima rakama indirgenir + zfill(6).
    _dt_str = pd.to_datetime(myu_df["CREATE_DT"], errors="coerce").dt.strftime("%Y-%m-%d")
    _dt_str = _dt_str.fillna(myu_df["CREATE_DT"].astype(str))
    _tm_str = (myu_df["CREATE_TM"].astype(str)
               .str.replace(r"\.0+$", "", regex=True)
               .str.replace(r"\D", "", regex=True)
               .str.zfill(6))
    myu_df["DATE_TIME"] = pd.to_datetime(
        _dt_str + " " + _tm_str, format="%Y-%m-%d %H%M%S", errors="coerce")
    _miss = myu_df["DATE_TIME"].isna()
    if _miss.any():
        # CREATE_DT beklenmedik biçimdeyse legacy'nin esnek parser'ına düş.
        myu_df.loc[_miss, "DATE_TIME"] = pd.to_datetime(
            _dt_str[_miss] + " " + _tm_str[_miss], errors="coerce")
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
    # Aynı dtype tuzağı (yukarıdaki MYU notu): CREATE_TM float64 gelirse
    # str()[:14] '.0' kuyruğunu/karışık biçimi keserek 14 haneyi bozar →
    # TÜM TREASURY (Hazine) SATIRLARI NaT olup düşerdi. Rakama indirgeyip
    # 14 hane al; olmazsa Oracle DATE dönmüş olabilir → doğrudan parse;
    # o da olmazsa satırı öldürme, CREATE_DT (gün 00:00) ile yaşat.
    _tm_raw = treasury_df["CREATE_TM"]
    _tm14 = (_tm_raw.astype(str)
             .str.replace(r"\.0+$", "", regex=True)
             .str.replace(r"\D", "", regex=True)
             .str[:14])
    treasury_df["DATE_TIME"] = pd.to_datetime(
        _tm14, format="%Y%m%d%H%M%S", errors="coerce")
    _miss = treasury_df["DATE_TIME"].isna()
    if _miss.any():
        treasury_df.loc[_miss, "DATE_TIME"] = pd.to_datetime(
            _tm_raw[_miss], errors="coerce")
    _miss = treasury_df["DATE_TIME"].isna()
    if _miss.any():
        treasury_df.loc[_miss, "DATE_TIME"] = pd.to_datetime(
            treasury_df.loc[_miss, "CREATE_DT"], errors="coerce")
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

    # ── R10 Yenilik 2: EKSTREM / EKSTREM_YETKI kural bazlı türetim ──
    final_df = _apply_mevduat_yetkileri(final_df)

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

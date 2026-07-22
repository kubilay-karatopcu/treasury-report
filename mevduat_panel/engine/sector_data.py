"""engine/sector_data.py — BDDK & TCMB sektör verisi yükleyici + cache.

BDDK'nın (müşteri tipi × AUM, vade, maliyet) ve TCMB'nin (ağırlıklı ortalama
mevduat faizleri) herkese açık sektör yayınlarının yedeği olan tablolar. Bu modül
onları HAM (transform'suz) yükler, dtype'ları normalize eder ve process-ömrü
boyunca cache'ler. Ek olarak BIST TLREF (O/N repo referans faizi + bileşik
endeks) piyasa verisi de burada yaşar. Site açılışında (app.py startup warm)
çağrılır → 5 DataFrame RAM'de hazır bekler. Tüketim/analiz mantığı SONRA eklenecek (şimdilik yalnız
ingestion + cache).

Kaynak seçimi env'e göre (config.ENV): DEVELOPMENT → data/dev.db (sentetik),
PRODUCTION_DB → Oracle (queries/prod/*.sql). Kolon sözleşmesi iki tarafta AYNI.

Cache stratejisi (CLAUDE.md #5): process restart'ta sıfırlanır; kaynak değişirse
Flask restart şart. Dönen df'ler cache'in KENDİSİDİR — çağıran taraf mutasyon
yapacaksa .copy() almalı (CLAUDE.md #6).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..data_source import load_dataframe  # port: db_source yerine

_AMT_CACHE:     Optional[pd.DataFrame] = None
_VADE_CACHE:    Optional[pd.DataFrame] = None
_MALIYET_CACHE: Optional[pd.DataFrame] = None
_TCMB_CACHE:    Optional[pd.DataFrame] = None
_TLREF_CACHE:   Optional[pd.DataFrame] = None


def load_bddk_amt_kirilim() -> pd.DataFrame:
    """BDDK müşteri-tipi × AUM kırılımı (BDDK_AMT_KIRILIM). Ham + cache'li."""
    global _AMT_CACHE
    if _AMT_CACHE is None:
        df = load_dataframe("BDDK_AMT_KIRILIM")
        df["TARIH"] = pd.to_datetime(df["TARIH"], errors="coerce")
        df["BAKIYE_TL"] = pd.to_numeric(df["BAKIYE_TL"], errors="coerce")
        _AMT_CACHE = df
    return _AMT_CACHE


def load_bddk_vade() -> pd.DataFrame:
    """BDDK vade kırılımı (BDDK_VADE). Ham + cache'li."""
    global _VADE_CACHE
    if _VADE_CACHE is None:
        df = load_dataframe("BDDK_VADE")
        df["TARIH"] = pd.to_datetime(df["TARIH"], errors="coerce")
        df["BAKIYE_TL"] = pd.to_numeric(df["BAKIYE_TL"], errors="coerce")
        _VADE_CACHE = df
    return _VADE_CACHE


def load_bddk_maliyet() -> pd.DataFrame:
    """BDDK mevduat maliyeti (BDDK_MALIYET). Ham + cache'li."""
    global _MALIYET_CACHE
    if _MALIYET_CACHE is None:
        df = load_dataframe("BDDK_MALIYET")
        df["TARIH"] = pd.to_datetime(df["TARIH"], errors="coerce")
        df["BAKIYE_TL"] = pd.to_numeric(df["BAKIYE_TL"], errors="coerce")
        _MALIYET_CACHE = df
    return _MALIYET_CACHE


def load_tcmb_deposit_rates() -> pd.DataFrame:
    """TCMB ağırlıklı ortalama mevduat faizleri (tcmb_deposit_rates). Ham + cache'li."""
    global _TCMB_CACHE
    if _TCMB_CACHE is None:
        df = load_dataframe("tcmb_deposit_rates")
        df["TCMB_DATE"] = pd.to_datetime(df["TCMB_DATE"], errors="coerce")
        df["ORT_FAIZ"] = pd.to_numeric(df["ORT_FAIZ"], errors="coerce")
        _TCMB_CACHE = df
    return _TCMB_CACHE


def load_bist_tlref() -> pd.DataFrame:
    """BIST TLREF O/N repo faizi + bileşik endeks (bist_tlref). Ham + cache'li.

    Kolonlar: ASOFDATE (datetime), INDEX_VALUE, RATE. RATE yüzde puan olarak
    GELDİĞİ GİBİ tutulur (39.50 = %39.50 — /100 YAPILMAZ; tüketen taraf kendi
    ölçeğine çevirir). INDEX_VALUE compound endeks: iki tarih arası işleyen
    faiz = INDEX(t1)/INDEX(t0) - 1. ASOFDATE artan sıralı döner.
    """
    global _TLREF_CACHE
    if _TLREF_CACHE is None:
        df = load_dataframe("bist_tlref")
        df["ASOFDATE"] = pd.to_datetime(df["ASOFDATE"], errors="coerce")
        df["INDEX_VALUE"] = pd.to_numeric(df["INDEX_VALUE"], errors="coerce")
        df["RATE"] = pd.to_numeric(df["RATE"], errors="coerce")
        df = df.dropna(subset=["ASOFDATE"]).sort_values("ASOFDATE").reset_index(drop=True)
        _TLREF_CACHE = df
    return _TLREF_CACHE


# ── Türetilmiş seri: sektör mevduat faiz oranı (aylık, yıllıklandırılmış) ─────

# Segment tanımları — hangi BANKA_TIPI'ler dahil.
_RATE_SEGMENTS = {
    "Private Sector": {"Yerli Özel", "Yabancı"},          # Kamu HARİÇ
    "Total Sector":   {"Yerli Özel", "Yabancı", "Kamu"},  # Kamu DAHİL (tüm sektör)
}

_RATE_SERIES_CACHE: Optional[pd.DataFrame] = None


def sector_deposit_rate_series() -> pd.DataFrame:
    """Aylık sektör mevduat faiz oranı zaman serisi (BDDK_MALIYET'ten türetilir).

    Segment × para birimi (2 × 2 = 4 seri): {Özel Sektör, Toplam Sektör} ×
    {TP, YP}. Her (segment, ccy, ay) için:

      # Faiz gideri KÜMÜLATİF gelir (yıl içinde birikir, her Ocak sıfırlanır) →
      # o ayın faiz giderini bulmak için önceki aydan farkını al; Ocak'ta çıkarma
      # YOK (yıl başı reset → Ocak kümülatifi = Ocak aylık gideri).
      fg_month   = Σ FaizGideri(ay-sonu) − Σ FaizGideri(önceki ay-sonu)   # DATA_TIPI='Faiz Gideri'
                 = Σ FaizGideri(ay-sonu)                                   # ay Ocak ise
      ort_bakiye = ( Σ Bakiye(ay-sonu) + Σ Bakiye(önceki ay-sonu) ) / 2   # DATA_TIPI='Bakiye'
      # Yıllıklandırma ACT/ACT: ×12 DEĞİL, ×(yıl gün sayısı / ay gün sayısı).
      # yıl gün sayısı = 366 (artık yıl) ya da 365; ay gün sayısı = o ayın günü.
      RATE_PCT   = (gün_yıl / gün_ay) · fg_month / ort_bakiye · 100       # yıllıklandırılmış %

    Σ = segmentteki BANKA_TIPI'ler üzerinden toplam. Önceki ay = sıralı
    ay-sonlarında bir önceki gözlem (shift(1)). Bir gözlem SADECE ort_bakiye
    hesaplanabiliyorsa (önceki ay-sonu bakiyesi varsa) üretilir; ayrıca Ocak
    DIŞINDAKİ aylarda önceki-ay kümülatif faiz gideri de gerekir. Bu yüzden
    serinin ilk ay-sonu (ör. 2025-12-31) düşer, seri 2026-01-31'den başlar
    (Ocak → çıkarma yok, kümülatif doğrudan aylık gider).

    İKİNCİ ORAN (RATE_REES_PCT): aynı pay/annualizasyon, ama payda ortalama bakiye
    (Bakiye + Reeskont) üzerinden: ort_bakiye_ree = ((Bakiye+Reeskont)_ay-sonu +
    (Bakiye+Reeskont)_önceki-ay) / 2. Reeskont kolonu yoksa 0 (⇒ RATE_PCT ile aynı).

    Döner (long df, ay-sonu artan): kolonlar
      MONTH | SEGMENT | CCY_CODE | RATE_PCT | RATE_REES_PCT |
      FG_MONTH (aylık faiz gideri = pay) | FG_CUM (bu ay kümülatif) |
      FG_PREV_CUM (önceki ay kümülatif; Ocak'ta None) |
      ORT_BAKIYE | ORT_BAKIYE_REES | BAKIYE_END | BAKIYE_PREV
    """
    global _RATE_SERIES_CACHE
    if _RATE_SERIES_CACHE is not None:
        return _RATE_SERIES_CACHE

    src = load_bddk_maliyet()
    rows = []
    for seg, bankas in _RATE_SEGMENTS.items():
        d = src[src["BANKA_TIPI"].isin(bankas)]
        if d.empty:
            continue
        # (TARIH, CCY, DATA_TIPI) → segment toplamı; sonra Bakiye/Faiz Gideri sütunlaştır.
        g = (d.groupby(["TARIH", "CCY_CODE", "DATA_TIPI"], observed=True)["BAKIYE_TL"]
             .sum().reset_index())
        piv = g.pivot_table(index=["TARIH", "CCY_CODE"], columns="DATA_TIPI",
                            values="BAKIYE_TL", aggfunc="sum")
        for ccy in sorted(piv.index.get_level_values("CCY_CODE").unique()):
            sub = piv.xs(ccy, level="CCY_CODE").sort_index()   # index = TARIH (ay-sonu)
            if "Bakiye" not in sub.columns or "Faiz Gideri" not in sub.columns:
                continue
            bakiye_end  = sub["Bakiye"].astype(float)
            bakiye_prev = bakiye_end.shift(1)                  # önceki ay-sonu bakiye
            ort_bakiye  = (bakiye_end + bakiye_prev) / 2.0
            # İkinci oran için payda: (Bakiye + Reeskont) ortalaması. Reeskont yoksa 0.
            reeskont_end = (sub["Reeskont"].astype(float) if "Reeskont" in sub.columns
                            else pd.Series(0.0, index=sub.index))
            bre_end      = bakiye_end + reeskont_end           # bakiye + reeskont
            ort_bakiye_ree = (bre_end + bre_end.shift(1)) / 2.0
            fg_cum      = sub["Faiz Gideri"].astype(float)     # KÜMÜLATİF (yıl içi)
            fg_prev_cum = fg_cum.shift(1)                       # önceki ay-sonu kümülatif
            for tarih in sub.index:
                ob = ort_bakiye.loc[tarih]
                if pd.isna(ob) or ob == 0:                     # önceki-ay bakiyesi yok / bölen sıfır
                    continue
                _ts = pd.Timestamp(tarih)
                is_ocak = _ts.month == 1
                if is_ocak:
                    fg_month = float(fg_cum.loc[tarih])        # yıl başı reset → çıkarma yok
                    prev_cum = None
                else:
                    pv = fg_prev_cum.loc[tarih]
                    if pd.isna(pv):                            # önceki ay kümülatif yok → farkı alamayız
                        continue
                    fg_month = float(fg_cum.loc[tarih]) - float(pv)
                    prev_cum = round(float(pv), 3)
                # Yıllıklandırma: ACT/ACT convention → ×12 DEĞİL, ×(yıl gün sayısı /
                # ay gün sayısı). Ör. Mayıs (31g) → ×365/31; Şubat (28g) → ×365/28;
                # artık yılda yıl gün sayısı 366. Aylık gider o ayın gün sayısına
                # ait; act/act ile gerçek gün oranıyla yıla çevrilir.
                _days_in_year = 366 if _ts.is_leap_year else 365
                _annualize = _days_in_year / _ts.days_in_month
                # İkinci oran: payda (Bakiye+Reeskont) ortalaması. Payda geçersizse None.
                ob_ree = ort_bakiye_ree.loc[tarih]
                rate_ree = (round(_annualize * fg_month / ob_ree * 100.0, 4)
                            if not pd.isna(ob_ree) and ob_ree != 0 else None)
                rows.append({
                    "MONTH":            _ts,
                    "SEGMENT":          seg,
                    "CCY_CODE":         ccy,
                    "RATE_PCT":         round(_annualize * fg_month / ob * 100.0, 4),
                    "RATE_REES_PCT":    rate_ree,
                    "FG_MONTH":         round(fg_month, 3),
                    "FG_CUM":           round(float(fg_cum.loc[tarih]), 3),
                    "FG_PREV_CUM":      prev_cum,
                    "ORT_BAKIYE":       round(float(ob), 3),
                    "ORT_BAKIYE_REES":  (round(float(ob_ree), 3) if not pd.isna(ob_ree) else None),
                    "BAKIYE_END":       round(float(bakiye_end.loc[tarih]), 3),
                    "BAKIYE_PREV":      round(float(bakiye_prev.loc[tarih]), 3),
                })
    out = (pd.DataFrame(rows, columns=["MONTH", "SEGMENT", "CCY_CODE",
                                       "RATE_PCT", "RATE_REES_PCT",
                                       "FG_MONTH", "FG_CUM", "FG_PREV_CUM",
                                       "ORT_BAKIYE", "ORT_BAKIYE_REES",
                                       "BAKIYE_END", "BAKIYE_PREV"])
           .sort_values(["SEGMENT", "CCY_CODE", "MONTH"])
           .reset_index(drop=True))
    _RATE_SERIES_CACHE = out
    return _RATE_SERIES_CACHE


# ── TCMB faiz tablosu: vade kovaları sütun, (tarih × döviz) satır ─────────────
# (TIP önekindeki vade dilimi) → (json key, görünen etiket). Sıra = sütun sırası.
_TCMB_BUCKETS = [
    ("1 Aya Kadar Vadeli",        "v0_1",   "0-1 M"),
    ("3 Aya Kadar Vadeli",        "v1_3",   "1-3 M"),
    ("6 Aya Kadar Vadeli",        "v3_6",   "3-6 M"),
    ("1 Yıla Kadar Vadeli",       "v6_12",  "6M-1Y"),
    ("1 Yıl ve Daha Uzun Vadeli", "v1y",    "1Y+"),
    ("Toplam",                    "toplam", "Total"),
]
# Frontend'in sütunları kurabilmesi için (key, label) meta'sı.
TCMB_BUCKET_META = [{"key": k, "label": lbl} for _, k, lbl in _TCMB_BUCKETS]


def tcmb_rate_table() -> pd.DataFrame:
    """TCMB ağırlıklı ortalama mevduat faizlerini vade-sütunlu tabloya çevirir.

    Ham tablo long: (CUR, TCMB_DATE, TIP='<vade dilimi>_<CUR>', ORT_FAIZ). Burada
    SADECE reshape yapılır (hesap YOK): TIP'in '_<CUR>' son eki atılıp vade dilimi
    çıkarılır, (TCMB_DATE × CUR) satır, vade kovaları sütun olacak şekilde pivotlanır.

    Döner: kolonlar [TCMB_DATE, CUR, v0_1, v1_3, v3_6, v6_12, v1y, toplam]
    (tarih azalan → en güncel üstte, sonra CUR artan). Eksik kova = NaN.
    """
    d = load_tcmb_deposit_rates()[["CUR", "TCMB_DATE", "TIP", "ORT_FAIZ"]].copy()
    d["CUR"] = d["CUR"].astype(str)
    d["TIP"] = d["TIP"].astype(str)
    pfx_to_key = {p: k for p, k, _ in _TCMB_BUCKETS}

    def _key(tip: str, cur: str):
        suf = "_" + cur
        pfx = tip[:-len(suf)] if tip.endswith(suf) else tip
        return pfx_to_key.get(pfx)

    d["_KEY"] = [_key(t, c) for t, c in zip(d["TIP"], d["CUR"])]
    d = d[d["_KEY"].notna()]
    piv = d.pivot_table(index=["TCMB_DATE", "CUR"], columns="_KEY",
                        values="ORT_FAIZ", aggfunc="first")
    piv = piv.reindex(columns=[k for _, k, _ in _TCMB_BUCKETS])   # sabit sütun sırası
    piv = (piv.reset_index()
              .sort_values(["TCMB_DATE", "CUR"], ascending=[False, True])
              .reset_index(drop=True))
    return piv


# ── Sektör Blotter: banka verisi TCMB vade kovalarına gruplanır + TCMB eşleşir ─
#
# Amaç: bankanın new-production (ve 1-3 gün stok) verisini TCMB vade kovalarına
# (0-1 Ay, 1-3 Ay, 3-6 Ay, 6 Ay-1 Yıl, 1 Yıl+) gruplayıp her satırın yanına o
# tarihe denk gelen TCMB sektör faizini koymak. Kullanıcı bunu blotter gibi
# kullanıp sektörün outstanding maliyetini gün gün tahmin edecek (sonraki adım).
#
# Kaynak birleşimi:
#   • daily_deposit (stok), YALNIZ VADE_BUCKET='1-3' → gecelik/kasa benzeri; DAT
#     val_dt rolü oynar (1-3 gün her gün döner → stok ≈ günlük yeni bağlanan).
#     Compound: AGIRLIKLI_ORT_FAIZ (simple %) + AGIRLIKLI_ORT_TENOR (gün).
#   • new_production (akım), TENOR_GRP != '01_1-3' (1-3 zaten stoktan geliyor;
#     çift sayım olmasın), yalnız TRY. Compound: NP_FAIZ + TENOR_DAYS (WAVG_DTM).
#
# Gruplama: her iki kaynak fine-kova → TCMB kovası map'lenir; (VAL_DT × TCMB
# kovası) hücresinde compound faiz ve DTM bakiye-ağırlıklı ortalanır (1-3 ile
# 4-31'in 0-1 Ay'da birleşmesi dahil). Bakiye birimi: TL-mn (daily /1e6).
#
# TCMB eşleşmesi: TCMB haftalık, TCMB_DATE = haftanın SON günü → gözlem o
# haftanın başından o tarihe kadar geçerli. val_dt, İLERİYE dönük en yakın
# TCMB_DATE ile eşleşir (merge_asof forward, tolerans 6 gün); satırın kovasına
# denk gelen TCMB kolonu TCMB_RATE olarak yazılır.

# Fine kova → TCMB kova key'i (TCMB_BUCKET_META key'leri). NP TENOR_GRP evreni
# NP_TENOR_TO_COMMON (outstanding_daily) ile aynı; bilinmeyen değer ValueError
# (sessiz drop yasak — CLAUDE.md #9). '99_DIGER' = np_agg'ın vade-bucket'sız
# satır sentineli → bucketlanamaz, AÇIK biçimde dışlanır ve hacmi raporlanır.
_NP_GRP_TO_TCMB = {
    "01_1-3":    "v0_1",   # normalde NP'den dışlanır (stoktan gelir) — güvenlik için map'te
    "02_4-31":   "v0_1",
    "03_32-35":  "v1_3", "04_36-45": "v1_3", "05_46-60": "v1_3", "06_61-91": "v1_3",
    "07_92-181": "v3_6",
    "08_182-273": "v6_12", "09_274-365": "v6_12",
    "10_366-540": "v1y",  "11_540+": "v1y",
}
_TCMB_KEY_ORDER = ["v0_1", "v1_3", "v3_6", "v6_12", "v1y"]
_TCMB_KEY_LABEL = {b["key"]: b["label"] for b in TCMB_BUCKET_META}

_BLOTTER_CACHE: Optional[dict] = None


def sector_blotter() -> dict:
    """Banka new-prod + 1-3 gün stok verisini TCMB kovalarına gruplar, TCMB eşler.

    Döner: {"df": DataFrame, "dq_note": str|None}
      df kolonları: VAL_DT | BUCKET_KEY | VADE_BUCKET (TCMB kova etiketi) |
        BAKIYE (TL-mn) | WAVG_DTM (gün) | WAVG_COMP_PCT (compound, yıllık %) |
        TCMB_DATE (eşleşen haftalık gözlem) | TCMB_RATE_PCT
      Sıralama: VAL_DT azalan, kova sırası (0-1 Ay → 1 Yıl+).
    """
    global _BLOTTER_CACHE
    if _BLOTTER_CACHE is not None:
        return _BLOTTER_CACHE

    from ..data_source import load_dataframe as _ld  # port: db_source yerine
    from .np_agg import load_np_data, simple_to_compound_pct_series

    parts = []

    # ── 1) daily_deposit stok — yalnız VADE_BUCKET='1-3' (gecelik/kasa) ────────
    dd = _ld("daily_deposit")
    dd = dd[dd["VADE_BUCKET"].astype(str) == "1-3"].copy()
    if not dd.empty:
        dd["DAT"] = pd.to_datetime(dd["DAT"], errors="coerce")
        dd = dd[dd["DAT"].notna() & (dd["DAT"].dt.dayofweek < 5)]   # hafta sonu hariç
        bal = pd.to_numeric(dd["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0.0) / 1e6
        faiz = pd.to_numeric(dd["AGIRLIKLI_ORT_FAIZ"], errors="coerce")      # percent
        tenor = pd.to_numeric(dd.get("AGIRLIKLI_ORT_TENOR"), errors="coerce")
        ok = faiz.notna() & tenor.notna() & (tenor > 0) & (bal > 0)
        comp = simple_to_compound_pct_series(faiz[ok], tenor[ok])
        parts.append(pd.DataFrame({
            "VAL_DT":     dd.loc[ok, "DAT"].values,
            "BUCKET_KEY": "v0_1",
            "BAL":        bal[ok].values,
            "DTM":        tenor[ok].values,
            "COMP":       comp.values,
        }))

    # ── 2) new production — TRY, TENOR_GRP != 01_1-3 (çift sayım yok) ──────────
    npd = load_np_data()
    npd = npd[(npd["CCY_CODE"].astype(str) == "TRY")
              & (npd["TENOR_GRP"].astype(str) != "01_1-3")].copy()
    dq_note = None
    if not npd.empty:
        grp = npd["TENOR_GRP"].astype(str)
        # '99_DIGER' (vade-bucket'sız NP sentineli) bucketlanamaz → açık dışlama + rapor.
        diger = grp == "99_DIGER"
        if diger.any():
            _dropped = float(npd.loc[diger, "NP_HACIM"].sum())
            dq_note = ("NP volume without a maturity bucket (99_DIGER) excluded "
                       "from the blotter: {:,.0f} ₺M.".format(_dropped))
            npd = npd[~diger]
            grp = grp[~diger]
        unknown = sorted(set(grp[~grp.isin(_NP_GRP_TO_TCMB)].unique()))
        if unknown:
            raise ValueError(
                "sector_blotter: TENOR_GRP value(s) missing from the TCMB bucket "
                "map: {} — update _NP_GRP_TO_TCMB (silent drop is forbidden).".format(unknown))
        bal = pd.to_numeric(npd["NP_HACIM"], errors="coerce").fillna(0.0)     # TL-mn
        faiz = pd.to_numeric(npd["NP_FAIZ"], errors="coerce")                 # percent
        tenor = pd.to_numeric(npd["TENOR_DAYS"], errors="coerce")             # WAVG_DTM
        ok = faiz.notna() & tenor.notna() & (tenor > 0) & (bal > 0)
        comp = simple_to_compound_pct_series(faiz[ok], tenor[ok])
        parts.append(pd.DataFrame({
            "VAL_DT":     pd.to_datetime(npd.loc[ok, "DAT"]).values,
            "BUCKET_KEY": grp[ok].map(_NP_GRP_TO_TCMB).values,
            "BAL":        bal[ok].values,
            "DTM":        tenor[ok].values,
            "COMP":       comp.values,
        }))

    if not parts:
        empty = pd.DataFrame(columns=["VAL_DT", "BUCKET_KEY", "VADE_BUCKET", "BAKIYE",
                                      "WAVG_DTM", "WAVG_COMP_PCT", "TCMB_DATE", "TCMB_RATE_PCT"])
        _BLOTTER_CACHE = {"df": empty, "dq_note": dq_note}
        return _BLOTTER_CACHE

    allp = pd.concat(parts, ignore_index=True)
    allp["_wd"] = allp["DTM"] * allp["BAL"]
    allp["_wc"] = allp["COMP"] * allp["BAL"]
    g = (allp.groupby(["VAL_DT", "BUCKET_KEY"], observed=True)
         .agg(BAKIYE=("BAL", "sum"), _wd=("_wd", "sum"), _wc=("_wc", "sum"))
         .reset_index())
    g = g[g["BAKIYE"] > 0]
    g["WAVG_DTM"]      = g["_wd"] / g["BAKIYE"]
    g["WAVG_COMP_PCT"] = g["_wc"] / g["BAKIYE"]

    # ── 3) TCMB eşleşmesi (haftalık, TCMB_DATE = hafta sonu → forward asof) ────
    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"].copy()
    tc["TCMB_DATE"] = pd.to_datetime(tc["TCMB_DATE"])
    tc = tc.sort_values("TCMB_DATE")
    g = g.sort_values("VAL_DT")
    merged = pd.merge_asof(
        g, tc[["TCMB_DATE"] + _TCMB_KEY_ORDER],
        left_on="VAL_DT", right_on="TCMB_DATE",
        direction="forward", tolerance=pd.Timedelta(days=6),
    )
    # Satırın kovasına denk gelen TCMB kolonunu seç (eşleşme yoksa None).
    def _pick_tcmb(row):
        if pd.isna(row["TCMB_DATE"]):
            return None
        v = row[row["BUCKET_KEY"]]
        return None if pd.isna(v) else float(v)
    merged["TCMB_RATE_PCT"] = merged.apply(_pick_tcmb, axis=1)
    merged["VADE_BUCKET"] = merged["BUCKET_KEY"].map(_TCMB_KEY_LABEL)
    merged["_bo"] = merged["BUCKET_KEY"].map({k: i for i, k in enumerate(_TCMB_KEY_ORDER)})
    out = (merged.sort_values(["VAL_DT", "_bo"], ascending=[False, True])
           [["VAL_DT", "BUCKET_KEY", "VADE_BUCKET", "BAKIYE", "WAVG_DTM",
             "WAVG_COMP_PCT", "TCMB_DATE", "TCMB_RATE_PCT"]]
           .reset_index(drop=True))
    out["BAKIYE"]        = out["BAKIYE"].astype(float).round(2)
    out["WAVG_DTM"]      = out["WAVG_DTM"].astype(float).round(1)
    out["WAVG_COMP_PCT"] = out["WAVG_COMP_PCT"].astype(float).round(4)
    _BLOTTER_CACHE = {"df": out, "dq_note": dq_note}
    return _BLOTTER_CACHE


# ── Detay blotter (TCMB kovasız; VAL_DT × VADE) + gün-gün outstanding ─────────
#
# sector_blotter'ın GRUPLANMAMIŞ hali: new-production TRY akımları (VAL_DT ×
# MATURITY_DT) çiftinde tutulur (MATURITY_DT = VAL_DT + WAVG_DTM). TCMB kovasına
# GRUPLANMAZ — amaç, her akımın yaşam penceresi (valör → vade) üzerinden gün gün
# outstanding üretmek. Her detay satırının yanına gene TCMB oranı iliştirilir
# (valör haftası + akımın gün-vadesine denk gelen TCMB kovası).
#
# NOT (kapsam): detay blotter yalnız NP VADELİ akımları taşır (01_1-3 HARİÇ —
# ilk blotter ile aynı tasarım). Kısa (1-3 gün) kitap, outstanding hesabına
# daily_deposit STOĞU olarak girer: her gün o günün 1-3 stok bakiyesi "o gün
# yaşayan mevduatların bir parçası" gibi kendi ağırlığıyla katılır (akım-yaşam
# penceresi YOK; stok zaten günlük gözlem). TCMB tarafında stok, o günün
# haftasına denk 0-1 Ay TCMB oranını taşır. NP 01_1-3 akımları bu yüzden
# DIŞLANIR (stokla çift sayım olmasın). 99_DIGER (vade-bucket'sız) vade
# türetilemediği için açık dışlanır + hacmi dq_note'ta raporlanır.

def _days_to_tcmb_key(days: int) -> str:
    """Gün cinsinden vade → TCMB kova key'i (TENOR_GRP sınırlarıyla tutarlı)."""
    if days <= 31:
        return "v0_1"
    if days <= 91:
        return "v1_3"
    if days <= 181:
        return "v3_6"
    if days <= 365:
        return "v6_12"
    return "v1y"


_DETAIL_CACHE: Optional[dict] = None
_OUTSTANDING_CACHE: Optional[dict] = None

# Gün-gün (ve aylık) outstanding serisinin görünür başlangıcı. NP akım geçmişi
# 2025-01'e uzanır (queries new_production_analysis); ilk ~9 ay kitap-oluşum
# dönemi olduğundan seri 2025-10 öncesini göstermez (kullanıcı kararı).
OUTSTANDING_START = pd.Timestamp("2025-10-01")

# BDDK_VADE VADE_KIRILIM → TCMB kova key'i. 'Vadesiz' faizli kitap dışı →
# ağırlıklardan DIŞLANIR (kova payları faizli kitap üzerinden normalize edilir).
_BDDK_VADE_TO_TCMB = {
    "0-1_Ay":  "v0_1",
    "1_3_Ay":  "v1_3",
    "3_6_Ay":  "v3_6",
    "6_12_Ay": "v6_12",
    "1_Yil+":  "v1y",
}


def _bddk_vade_weights() -> dict:
    """Sektör vade kova ağırlıkları: {ay_sonu_Timestamp: {kova_key: pay}}.

    BDDK_VADE'den: TP + TÜM banka tipleri (Toplam Sektör — TCMB fiyatı tüm
    sektör olduğundan tutarlı ağırlık) + Y_I∪Y_D. 'Vadesiz' hariç tutulur, kova
    payları FAİZLİ kitap toplamına normalize edilir. Bilinmeyen VADE_KIRILIM
    (Vadesiz dışı) ValueError (sessiz drop yasak).
    """
    v = load_bddk_vade()
    if v.empty:
        return {}
    v = v.copy()
    # Oracle CHAR kolonlarında trailing space yaygın → strip ŞART (dev'de no-op).
    v["CCY_CODE"] = v["CCY_CODE"].astype(str).str.strip()
    v["VADE_KIRILIM"] = v["VADE_KIRILIM"].astype(str).str.strip()
    v = v[v["CCY_CODE"] == "TP"]
    if v.empty:
        return {}
    lbl = v["VADE_KIRILIM"]
    known = lbl.isin(_BDDK_VADE_TO_TCMB) | (lbl == "Vadesiz")
    unknown = sorted(lbl[~known].unique())
    if unknown:
        raise ValueError(
            "VADE_KIRILIM value(s) missing from the BDDK_VADE map: {} — "
            "update _BDDK_VADE_TO_TCMB.".format(unknown))
    v = v[lbl.isin(_BDDK_VADE_TO_TCMB)]
    v["_key"] = v["VADE_KIRILIM"].map(_BDDK_VADE_TO_TCMB)
    g = v.groupby([v["TARIH"].dt.normalize(), "_key"])["BAKIYE_TL"].sum()
    out: dict = {}
    for (tarih, key), bal in g.items():
        out.setdefault(pd.Timestamp(tarih), {})[key] = float(bal)
    for tarih, d in out.items():
        tot = sum(d.values())
        out[tarih] = {k: (b / tot if tot > 0 else 0.0) for k, b in d.items()}
    return out


def sector_blotter_detail() -> dict:
    """Gruplanmamış (VAL_DT × MATURITY_DT) NP blotter'ı + TCMB eşleşmesi.

    Döner: {"df": DataFrame, "dq_note": str|None}
      df kolonları: VAL_DT | MATURITY_DT | TENOR_GUN ((vade−valör) gün) |
        BAKIYE (TL-mn) | COMP_PCT (bakiye-wavg compound %) |
        TCMB_DATE | TCMB_RATE_PCT (valör haftası × gün-vade kovası)
    """
    global _DETAIL_CACHE
    if _DETAIL_CACHE is not None:
        return _DETAIL_CACHE

    from .np_agg import load_np_data, simple_to_compound_pct_series

    npd = load_np_data()
    # Yalnız VADELİ akımlar: 01_1-3 HARİÇ — kısa kitap outstanding'e günlük 1-3
    # STOK olarak girer (bkz. modül notu; akım+stok birlikte çift sayım olurdu).
    npd = npd[(npd["CCY_CODE"].astype(str) == "TRY")
              & (npd["TENOR_GRP"].astype(str) != "01_1-3")].copy()
    dq_note = None
    grp = npd["TENOR_GRP"].astype(str)
    diger = grp == "99_DIGER"
    if diger.any():
        _dropped = float(npd.loc[diger, "NP_HACIM"].sum())
        dq_note = ("NP volume without a maturity bucket (99_DIGER) excluded from the "
                   "detailed blotter: {:,.0f} ₺M (tenor cannot be derived).".format(_dropped))
        npd = npd[~diger]

    bal   = pd.to_numeric(npd["NP_HACIM"], errors="coerce").fillna(0.0)      # TL-mn
    faiz  = pd.to_numeric(npd["NP_FAIZ"], errors="coerce")                   # percent
    tenor = pd.to_numeric(npd["TENOR_DAYS"], errors="coerce")                # WAVG_DTM (gün)
    ok = faiz.notna() & tenor.notna() & (tenor > 0) & (bal > 0)
    npd = npd[ok]
    comp = simple_to_compound_pct_series(faiz[ok], tenor[ok])

    det = pd.DataFrame({
        "VAL_DT": pd.to_datetime(npd["DAT"]).values,
        "BAL":    bal[ok].values,
        "COMP":   comp.values,
        "_ten":   tenor[ok].round().astype(int).clip(lower=1).values,
    })
    det["MATURITY_DT"] = det["VAL_DT"] + pd.to_timedelta(det["_ten"], unit="D")
    det["_wc"] = det["COMP"] * det["BAL"]
    g = (det.groupby(["VAL_DT", "MATURITY_DT"], observed=True)
         .agg(BAKIYE=("BAL", "sum"), _wc=("_wc", "sum"))
         .reset_index())
    g = g[g["BAKIYE"] > 0]
    g["COMP_PCT"]  = g["_wc"] / g["BAKIYE"]
    g["TENOR_GUN"] = (g["MATURITY_DT"] - g["VAL_DT"]).dt.days
    g["_bkey"]     = g["TENOR_GUN"].map(_days_to_tcmb_key)

    # TCMB eşleşmesi: valör haftası (forward asof ≤6 gün) × satırın gün-vade kovası.
    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"].copy()
    tc["TCMB_DATE"] = pd.to_datetime(tc["TCMB_DATE"])
    tc = tc.sort_values("TCMB_DATE")
    g = g.sort_values("VAL_DT")
    merged = pd.merge_asof(
        g, tc[["TCMB_DATE"] + _TCMB_KEY_ORDER],
        left_on="VAL_DT", right_on="TCMB_DATE",
        direction="forward", tolerance=pd.Timedelta(days=6),
    )

    def _pick(row):
        if pd.isna(row["TCMB_DATE"]):
            return None
        v = row[row["_bkey"]]
        return None if pd.isna(v) else float(v)
    merged["TCMB_RATE_PCT"] = merged.apply(_pick, axis=1)

    out = (merged[["VAL_DT", "MATURITY_DT", "TENOR_GUN", "BAKIYE", "COMP_PCT",
                   "TCMB_DATE", "TCMB_RATE_PCT"]]
           .sort_values(["VAL_DT", "MATURITY_DT"])
           .reset_index(drop=True))
    _DETAIL_CACHE = {"df": out, "dq_note": dq_note}
    return _DETAIL_CACHE


def _stock_13_daily() -> pd.DataFrame:
    """1-3 gün STOK — günlük agregat + o günün haftasının TCMB 0-1 Ay oranı.

    daily_deposit yalnız VADE_BUCKET='1-3' (gecelik/kasa). Her DAT için:
    BAL (TL-mn, Σ), COMP_PCT (AGIRLIKLI_ORT_FAIZ+TENOR'dan compound, bakiye-wavg),
    TCMB_RATE_PCT (DAT ileriye dönük en yakın TCMB gözleminin v0_1 oranı ≤6 gün).
    Outstanding hesabında stok, o gün YAŞAYAN mevduatların bir parçası olarak
    kendi ağırlığıyla katılır (akım-yaşam penceresi yok; stok günlük gözlem).
    """
    from ..data_source import load_dataframe as _ld  # port: db_source yerine
    from .np_agg import simple_to_compound_pct_series

    dd = _ld("daily_deposit")
    dd = dd[dd["VADE_BUCKET"].astype(str) == "1-3"].copy()
    if dd.empty:
        return pd.DataFrame(columns=["DAT", "BAL", "COMP_PCT", "TENOR_GUN", "TCMB_RATE_PCT"])
    dd["DAT"] = pd.to_datetime(dd["DAT"], errors="coerce")
    dd = dd[dd["DAT"].notna() & (dd["DAT"].dt.dayofweek < 5)]
    bal = pd.to_numeric(dd["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0.0) / 1e6
    faiz = pd.to_numeric(dd["AGIRLIKLI_ORT_FAIZ"], errors="coerce")
    tenor = pd.to_numeric(dd.get("AGIRLIKLI_ORT_TENOR"), errors="coerce")
    ok = faiz.notna() & tenor.notna() & (tenor > 0) & (bal > 0)
    comp = simple_to_compound_pct_series(faiz[ok], tenor[ok])
    f = pd.DataFrame({"DAT": dd.loc[ok, "DAT"].values,
                      "BAL": bal[ok].values,
                      "_wc": (comp * bal[ok].values).values,
                      "_wt": (tenor[ok] * bal[ok]).values})
    g = (f.groupby("DAT", observed=True)
         .agg(BAL=("BAL", "sum"), _wc=("_wc", "sum"), _wt=("_wt", "sum"))
         .reset_index())
    g = g[g["BAL"] > 0]
    g["COMP_PCT"]  = g["_wc"] / g["BAL"]
    g["TENOR_GUN"] = g["_wt"] / g["BAL"]

    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"].copy()
    tc["TCMB_DATE"] = pd.to_datetime(tc["TCMB_DATE"])
    tc = tc.sort_values("TCMB_DATE")
    g = g.sort_values("DAT")
    merged = pd.merge_asof(
        g, tc[["TCMB_DATE", "v0_1"]],
        left_on="DAT", right_on="TCMB_DATE",
        direction="forward", tolerance=pd.Timedelta(days=6),
    )
    merged["TCMB_RATE_PCT"] = merged["v0_1"]
    return merged[["DAT", "BAL", "COMP_PCT", "TENOR_GUN", "TCMB_RATE_PCT"]].reset_index(drop=True)


# ── Vadesiz (Demand) etkisi — KGH/BTH O/N stoğuna sıfır-faizli vadesiz varsayımı ─
# Slide 2 bubble + Slide 4 grafik/tablolarında kullanılır. KGH ve BTH O/N
# ürünlerinde, normal bakiyeye ek olarak bakiyenin girilen yüzdesi kadar %0 faizli
# vadesiz mevduat varmış gibi varsayılır → efektif simple oran r/(1+p), bakiye
# B·(1+p). Faiz TUTARI (B·r) sabit; toplam wavg payı değişmez, paydası büyür.
_DEMAND_SUBPRODUCTS = ("KGH", "BTH")


def _stock_13_daily_split(demand_pct: float = 0.0) -> pd.DataFrame:
    """1-3 gün STOK — _stock_13_daily ile aynı, ama SUB_PRODUCT bazında bölünür
    ve opsiyonel demand (vadesiz) etkisi uygulanır.

    demand_pct > 0: KGH/BTH satırlarında simple faiz ÷(1+p), bakiye ×(1+p).
    Oran/harman ağırlığı BÜYÜMÜŞ bakiye (BAL), tenor ağırlığı ORİJİNAL bakiye
    (BAL_TEN) — karar (a): tenor demand'dan etkilenmez. demand_pct=0 iken
    BAL == BAL_TEN ve sonuç _stock_13_daily ile birebir aynıdır.

    Döner: DataFrame[DAT, BAL, BAL_TEN, COMP_PCT, TENOR_GUN, TCMB_RATE_PCT].
    """
    from ..data_source import load_dataframe as _ld  # port: db_source yerine
    from .np_agg import simple_to_compound_pct_series

    p = max(0.0, float(demand_pct or 0.0)) / 100.0
    dd = _ld("daily_deposit")
    dd = dd[dd["VADE_BUCKET"].astype(str) == "1-3"].copy()
    if dd.empty:
        return pd.DataFrame(columns=["DAT", "BAL", "BAL_TEN", "COMP_PCT",
                                     "TENOR_GUN", "TCMB_RATE_PCT"])
    dd["DAT"] = pd.to_datetime(dd["DAT"], errors="coerce")
    dd = dd[dd["DAT"].notna() & (dd["DAT"].dt.dayofweek < 5)]
    bal   = pd.to_numeric(dd["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0.0) / 1e6
    faiz  = pd.to_numeric(dd["AGIRLIKLI_ORT_FAIZ"], errors="coerce")
    tenor = pd.to_numeric(dd.get("AGIRLIKLI_ORT_TENOR"), errors="coerce")
    sub   = (dd["SUB_PRODUCT"].astype(str) if "SUB_PRODUCT" in dd.columns
             else pd.Series("", index=dd.index))
    ok = faiz.notna() & tenor.notna() & (tenor > 0) & (bal > 0)

    dat_ok    = dd.loc[ok, "DAT"].values
    bal_ok    = bal[ok].to_numpy(dtype=float)
    faiz_ok   = faiz[ok].to_numpy(dtype=float)
    tenor_ok  = tenor[ok].to_numpy(dtype=float)
    is_demand = sub[ok].isin(_DEMAND_SUBPRODUCTS).to_numpy(dtype=float)

    scale     = 1.0 + p * is_demand          # KGH/BTH → (1+p), diğerleri → 1
    faiz_eff  = faiz_ok / scale              # seyreltilmiş simple oran
    bal_rate  = bal_ok * scale               # rate/harman ağırlığı (büyümüş)
    comp = simple_to_compound_pct_series(pd.Series(faiz_eff),
                                         pd.Series(tenor_ok)).to_numpy(dtype=float)

    f = pd.DataFrame({
        "DAT":     dat_ok,
        "BAL":     bal_rate,                 # oran/harman ağırlığı
        "BAL_TEN": bal_ok,                   # tenor ağırlığı (orijinal)
        "_wc":     comp * bal_rate,          # compound × büyümüş bakiye
        "_wt":     tenor_ok * bal_ok,        # tenor × orijinal bakiye
    })
    g = (f.groupby("DAT", observed=True)
         .agg(BAL=("BAL", "sum"), BAL_TEN=("BAL_TEN", "sum"),
              _wc=("_wc", "sum"), _wt=("_wt", "sum"))
         .reset_index())
    g = g[g["BAL"] > 0]
    g["COMP_PCT"]  = g["_wc"] / g["BAL"]
    g["TENOR_GUN"] = g["_wt"] / g["BAL_TEN"]

    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"].copy()
    tc["TCMB_DATE"] = pd.to_datetime(tc["TCMB_DATE"])
    tc = tc.sort_values("TCMB_DATE")
    g = g.sort_values("DAT")
    merged = pd.merge_asof(
        g, tc[["TCMB_DATE", "v0_1"]],
        left_on="DAT", right_on="TCMB_DATE",
        direction="forward", tolerance=pd.Timedelta(days=6),
    )
    merged["TCMB_RATE_PCT"] = merged["v0_1"]
    return merged[["DAT", "BAL", "BAL_TEN", "COMP_PCT", "TENOR_GUN",
                   "TCMB_RATE_PCT"]].reset_index(drop=True)


_HARM_BUCKET_CACHE: dict = {}


def harmonized_bucket_0_1_monthly(demand_pct: float = 0.0) -> dict:
    """Harmanlı 0-1 M bank rate — AYLIK (Slide 4 'Monthly New Business Rate' 0-1 M).

    0-1 M kova = O/N/kasa STOK (daily_deposit VADE_BUCKET='1-3') + 4-31 gün NP
    akımı (blotter, TENOR_GUN ≤ 31; 01_1-3 blotter'da zaten dışlı). İkisi her iş
    günü YAŞAYAN outstanding olarak bakiye-ağırlıklı harmanlanır; günlük compound
    oran → simple (o günün wavg tenoruyla), sonra ay içinde günlük bakiyeyle
    ağırlıklanır (sector_outstanding_monthly konvansiyonuyla birebir). Seri
    TCMB'nin son gözleminde kesilir (kısmi-ay tutarlılığı).

    demand_pct > 0: O/N stoğun KGH/BTH kısmına vadesiz etkisi (bkz.
    _stock_13_daily_split). NP akımı ETKİLENMEZ (ürün kırılımı yok).

    Döner: {Period(ay): (bank_simple_pct, bank_tenor_gun, bank_upto_str)}.
    bank_upto = o ay banka verisine giren SON iş günü ('%Y-%m-%d').
    """
    key = round(float(demand_pct or 0.0), 4)
    if key in _HARM_BUCKET_CACHE:
        return _HARM_BUCKET_CACHE[key]

    import numpy as np
    from .np_agg import compound_to_simple_pct

    det = sector_blotter_detail()["df"]
    if not det.empty:
        det = det[det["TENOR_GUN"] <= 31]          # yalnız v0_1 kovası (4-31 gün)
    stock = _stock_13_daily_split(demand_pct)
    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"]
    if (det.empty and stock.empty) or tc.empty:
        _HARM_BUCKET_CACHE[key] = {}
        return {}

    tcmb_last = pd.to_datetime(tc["TCMB_DATE"]).max()   # kesim: TCMB son gözlemi
    starts = []
    if not det.empty:
        starts.append(det["VAL_DT"].min())
    if not stock.empty:
        starts.append(stock["DAT"].min())
    if not starts:
        _HARM_BUCKET_CACHE[key] = {}
        return {}
    series_start = max(min(starts), OUTSTANDING_START)
    days = pd.bdate_range(series_start, tcmb_last)

    empty_d = np.array([], dtype=float)
    val  = (det["VAL_DT"].values.astype("datetime64[D]") if not det.empty
            else np.array([], dtype="datetime64[D]"))
    mat  = (det["MATURITY_DT"].values.astype("datetime64[D]") if not det.empty
            else np.array([], dtype="datetime64[D]"))
    bal  = det["BAKIYE"].values.astype(float) if not det.empty else empty_d
    comp = det["COMP_PCT"].values.astype(float) if not det.empty else empty_d
    tng  = det["TENOR_GUN"].values.astype(float) if not det.empty else empty_d

    stock_by_day = {
        pd.Timestamp(r["DAT"]).normalize(): (float(r["BAL"]), float(r["BAL_TEN"]),
                                             float(r["COMP_PCT"]), float(r["TENOR_GUN"]))
        for _, r in stock.iterrows()
    }

    # Aylık birikim: [Σ(simple·bal_rate), Σ(bal_rate), Σ(tenor·bal_ten),
    #                 Σ(bal_ten), son_iş_günü]
    by_month: dict = {}
    for day in days:
        dd_np = np.datetime64(day.date())
        wc = tot_r = wt = tot_t = 0.0
        if bal.size:
            alive = (val <= dd_np) & (dd_np < mat)
            b = bal[alive]
            if b.size:
                bs = float(b.sum())
                wc  += float((comp[alive] * b).sum())
                tot_r += bs
                wt  += float((tng[alive] * b).sum())
                tot_t += bs
        s = stock_by_day.get(pd.Timestamp(day).normalize())
        if s is not None:
            s_bal, s_ten_bal, s_comp, s_ten = s
            wc    += s_comp * s_bal
            tot_r += s_bal
            wt    += s_ten * s_ten_bal
            tot_t += s_ten_bal
        if tot_r <= 0 or tot_t <= 0:
            continue
        bank_comp_d  = wc / tot_r
        wavg_tenor_d = wt / tot_t
        if wavg_tenor_d <= 0:
            continue
        simple_d = compound_to_simple_pct(bank_comp_d, wavg_tenor_d)
        if simple_d is None:
            continue
        per = day.to_period("M")
        acc = by_month.setdefault(per, [0.0, 0.0, 0.0, 0.0, None])
        acc[0] += simple_d * tot_r
        acc[1] += tot_r
        acc[2] += wavg_tenor_d * tot_t
        acc[3] += tot_t
        acc[4] = day                          # days artan → son atama = ayın son iş günü

    out = {}
    for per, (ws, tr, wt2, tt, last_day) in by_month.items():
        if tr <= 0 or tt <= 0:
            continue
        out[per] = (round(float(ws / tr), 4), round(float(wt2 / tt), 1),
                    pd.Timestamp(last_day).strftime("%Y-%m-%d"))
    _HARM_BUCKET_CACHE[key] = out
    return out


def sector_outstanding_series() -> dict:
    """Gün-gün outstanding: toplam bakiye + banka compound + TCMB sentetik oran.

    İki bileşen:
      • VADELİ akımlar (detay blotter): her akım [VAL_DT, MATURITY_DT)
        penceresinde yaşar (vade günü geri ödenir → o gün dahil değil); TCMB
        oranı GİRİŞ haftasından taşınır.
      • 1-3 gün STOK (daily_deposit): her gün o günün stok bakiyesi, o günün
        compound oranı ve o günün haftasının TCMB 0-1 Ay oranıyla, o gün yaşayan
        mevduatların bir parçası olarak kendi ağırlığıyla katılır.
    Her iş günü d için (akım+stok birlikte):
      BAKIYE(d)        = Σ bal
      BANK_COMP_PCT(d) = Σ(comp·bal) / Σbal            (bakiye-wavg compound)
      TCMB_PCT(d)      = Σ(tcmb·bal) / Σ(bal|tcmb var)
      WAVG_TENOR(d)    = Σ(tenor·bal) / Σbal           (akım TENOR_GUN + stok tenor)
      SPREAD_COMP_PCT  = BANK_COMP − TCMB
      BANK_SIMPLE_PCT  = compound→simple(BANK_COMP, WAVG_TENOR)
      TCMB_SIMPLE_PCT  = compound→simple(TCMB,      WAVG_TENOR)   (AYNI tenor)
      SPREAD_SIMPLE_PCT = BANK_SIMPLE − TCMB_SIMPLE

    İKİNCİ VARYANT — TCMB (BDDK Mix): TCMB_PCT banka vade MIX'ini kullanır
    ("sektör bankayla aynı yapıda bağlasaydı"). TCMB_BDDK_PCT ise kova bazlı
    vintage oranlarını SEKTÖRÜN vade kompozisyonuyla ağırlıklar:
      R_b(d)        = kova b'de yaşayan bakiyelerin giriş-TCMB wavg'ı
                      (stok → v0_1 kovasına katılır)
      TCMB_BDDK(d)  = Σ_b W_b·R_b / Σ_b W_b   (R_b'si olan kovalar üzerinden
                      YENİDEN normalize; kapsam BDDK_W_KAPSAM'da raporlanır)
      W_b           = son mevcut BDDK ay-sonu ≤ d ağırlığı (_bddk_vade_weights;
                      Vadesiz hariç, faizli kitaba normalize). Lag'li aylar için
                      SON GÖZLEM TAŞINIR (nowcast) — kullanılan ay BDDK_W_AY'da.
      TCMB_BDDK_SIMPLE = compound→simple(TCMB_BDDK, WAVG_TENOR)  (tablo
                      konvansiyonuyla tutarlı: aynı günlük tenor)
    Seri, TCMB verisinin SON tarihinde KESİLİR (banka verisi daha güncel olsa da).

    Döner: {"df": DataFrame[TARIH, BAKIYE, WAVG_TENOR, BANK_COMP_PCT, TCMB_PCT,
    SPREAD_COMP_PCT, BANK_SIMPLE_PCT, TCMB_SIMPLE_PCT, SPREAD_SIMPLE_PCT,
    TCMB_BDDK_PCT, TCMB_BDDK_SIMPLE_PCT, BDDK_W_AY, BDDK_W_KAPSAM], "dq_note"}.
    """
    global _OUTSTANDING_CACHE
    if _OUTSTANDING_CACHE is not None:
        return _OUTSTANDING_CACHE

    import numpy as np

    d = sector_blotter_detail()
    det = d["df"]
    stock = _stock_13_daily()
    tc = tcmb_rate_table()
    tc = tc[tc["CUR"].astype(str) == "TRY"]
    _COLS = ["TARIH", "BAKIYE", "WAVG_TENOR", "BANK_COMP_PCT", "TCMB_PCT",
             "SPREAD_COMP_PCT", "BANK_SIMPLE_PCT", "TCMB_SIMPLE_PCT", "SPREAD_SIMPLE_PCT",
             "TCMB_BDDK_PCT", "TCMB_BDDK_SIMPLE_PCT", "BDDK_W_AY", "BDDK_W_KAPSAM",
             "SPREAD_BANK_BDDK_PCT", "MIX_SIMPLE_PCT", "BDDK_TENOR",
             "BANK_ON_PCT", "TCMB_ON_PCT", "TCMB_BDDK_ON_PCT",
             "SPREAD_ON_PCT", "SPREAD_BANK_BDDK_ON_PCT"]
    if (det.empty and stock.empty) or tc.empty:
        _OUTSTANDING_CACHE = {"df": pd.DataFrame(columns=_COLS), "dq_note": d["dq_note"]}
        return _OUTSTANDING_CACHE

    tcmb_last = pd.to_datetime(tc["TCMB_DATE"]).max()   # kesim: TCMB'nin son gözlemi
    starts = []
    if not det.empty:
        starts.append(det["VAL_DT"].min())
    if not stock.empty:
        starts.append(stock["DAT"].min())
    # Görünür başlangıç: 2025-10 (kullanıcı kararı). NP akım geçmişi 2025-01'e
    # uzanır ama serinin ilk ~9 ayı kitap-oluşum (build-up) dönemi → gösterilmez;
    # 2025-10 itibarıyla o gün yaşayan eski akımlar veride mevcuttur (tam kitap).
    series_start = max(min(starts), OUTSTANDING_START)
    days = pd.bdate_range(series_start, tcmb_last)      # iş günleri

    from .np_agg import compound_to_simple_pct, compound_to_on_pct

    val  = det["VAL_DT"].values.astype("datetime64[D]")
    mat  = det["MATURITY_DT"].values.astype("datetime64[D]")
    bal  = det["BAKIYE"].values.astype(float)
    comp = det["COMP_PCT"].values.astype(float)
    tng  = det["TENOR_GUN"].values.astype(float)        # akımın orijinal vadesi (gün)
    tcr  = det["TCMB_RATE_PCT"].astype(float).values    # NaN = eşleşme yok
    has_tc = ~np.isnan(tcr)
    bkey = det["TENOR_GUN"].astype(int).map(_days_to_tcmb_key).values  # akımın TCMB kovası

    # BDDK sektör vade ağırlıkları (ay-sonu → {kova: pay}); gün için son ay-sonu
    # ≤ d taşınır (nowcast). Yükleme hatası/boşluk ana seriyi ÖLDÜRMEZ — BDDK
    # kolonları None kalır ve NEDENİ dq_note badge'inde raporlanır (prod teşhisi).
    w_by_month = {}
    w_err = None
    try:
        w_by_month = _bddk_vade_weights()
        if not w_by_month:
            try:
                _v = load_bddk_vade()
                _tp = int((_v["CCY_CODE"].astype(str).str.strip() == "TP").sum()) if not _v.empty else 0
                w_err = ("BDDK-mix weights could not be built: BDDK_VADE returned {} "
                         "rows, {} TP rows.".format(len(_v), _tp))
            except Exception as ie:
                w_err = "BDDK-mix weights could not be built: BDDK_VADE unreadable ({}).".format(ie)
    except Exception as we:
        w_err = "BDDK-mix weights could not be loaded: {}".format(we)
    w_dates = sorted(w_by_month.keys())

    # Stok: gün → (bal, comp, tenor, tcmb) hızlı erişim.
    stock_by_day = {
        pd.Timestamp(r["DAT"]).normalize(): (
            float(r["BAL"]), float(r["COMP_PCT"]), float(r["TENOR_GUN"]),
            (None if pd.isna(r["TCMB_RATE_PCT"]) else float(r["TCMB_RATE_PCT"])))
        for _, r in stock.iterrows()
    }

    rows = []
    attr_rows = []   # mix etkisi kova ayrıştırması (günlük; aylıkta toplanır)
    for day in days:
        dd_np = np.datetime64(day.date())
        alive = (val <= dd_np) & (dd_np < mat)
        b = bal[alive]
        tot = float(b.sum())
        wc  = float((comp[alive] * b).sum())
        wt  = float((tng[alive] * b).sum())
        at = alive & has_tc
        bt = bal[at]
        tc_bal = float(bt.sum())
        tc_w   = float((tcr[at] * bt).sum())
        # Kova bazında giriş-TCMB birikimleri (BDDK-mix varyantı için).
        bkt_bal = {k: 0.0 for k in _TCMB_KEY_ORDER}
        bkt_w   = {k: 0.0 for k in _TCMB_KEY_ORDER}
        bkt_tw  = {k: 0.0 for k in _TCMB_KEY_ORDER}   # kova tenor birikimi (Σ tenor·bal)
        for k in _TCMB_KEY_ORDER:
            mk = at & (bkey == k)
            if mk.any():
                bk = bal[mk]
                bkt_bal[k] = float(bk.sum())
                bkt_w[k]   = float((tcr[mk] * bk).sum())
                bkt_tw[k]  = float((tng[mk] * bk).sum())
        s = stock_by_day.get(pd.Timestamp(day).normalize())
        if s is not None:
            s_bal, s_comp, s_ten, s_tcmb = s
            tot += s_bal
            wc  += s_comp * s_bal
            wt  += s_ten * s_bal
            if s_tcmb is not None:
                tc_bal += s_bal
                tc_w   += s_tcmb * s_bal
                bkt_bal["v0_1"] += s_bal          # stok = 0-1 Ay kovası
                bkt_w["v0_1"]   += s_tcmb * s_bal
                bkt_tw["v0_1"]  += s_ten * s_bal
        if tot <= 0:
            continue
        bank_comp  = wc / tot
        wavg_tenor = wt / tot
        tcmb       = (tc_w / tc_bal) if tc_bal > 0 else None
        # ── BDDK-mix varyantı: R_b × W_b (son ay-sonu ≤ d; kapsam üzerinden
        # yeniden normalize) ────────────────────────────────────────────────
        tcmb_bddk = None
        w_ay = None
        w_kapsam = None
        bddk_tenor = None
        if w_dates:
            eligible = [x for x in w_dates if x <= pd.Timestamp(day)]
            if eligible:
                w_month = eligible[-1]
                weights = w_by_month[w_month]
                num = den = 0.0
                tnum = 0.0   # sektör wavg tenoru: Σ W_b × (bankanın kova tenoru)
                for k, wgt in weights.items():
                    if bkt_bal.get(k, 0.0) > 0:
                        num += wgt * (bkt_w[k] / bkt_bal[k])   # R_b
                        tnum += wgt * (bkt_tw[k] / bkt_bal[k])  # kova tenoru (banka)
                        den += wgt
                if den > 0:
                    tcmb_bddk = num / den
                    bddk_tenor = tnum / den
                    w_ay = w_month.strftime("%Y-%m")
                    w_kapsam = round(den * 100.0, 1)
        # Compound → simple: banka/TCMB günlük wavg tenor ile; TCMB_BDDK ise
        # SEKTÖRÜN wavg tenoruyla (BDDK kova ağırlığı × bankanın kova tenoru —
        # kullanıcı konvansiyonu; eskiden yanlış olarak banka portföy tenoru
        # kullanılıyordu → gecelik-ağırlıklı kitapta BDDK simple'ı fazla
        # compound'a yakın kalıyordu).
        bank_simple = compound_to_simple_pct(bank_comp, wavg_tenor)
        tcmb_simple = (compound_to_simple_pct(tcmb, wavg_tenor)
                       if tcmb is not None else None)
        tcmb_bddk_simple = (compound_to_simple_pct(tcmb_bddk, bddk_tenor)
                            if (tcmb_bddk is not None and bddk_tenor is not None) else None)
        # O/N eşlenikleri: yıllık bileşikten vadeden bağımsız (365 gün) türetilir.
        bank_on = compound_to_on_pct(bank_comp)
        tcmb_on = compound_to_on_pct(tcmb) if tcmb is not None else None
        tcmb_bddk_on = compound_to_on_pct(tcmb_bddk) if tcmb_bddk is not None else None
        # ── Mix etkisi kova ayrıştırması (compound uzayında KESİN kimlik):
        #   TCMB(banka mix) − TCMB(BDDK mix) = Σ_b (wB_b − wS_b)·(R_b − R̄)
        # wB_b = bankanın TCMB'li bakiye payı; wS_b = sektör payı (kapsam üzerinden
        # renormalize); R̄ = TCMB_BDDK (referans — katkı işaretleri yorumlanabilir:
        # banka pahalı kovada sektörden AĞIRSA katkı +). Günlük katkılar aylıkta
        # aynı bakiye ağırlıklarıyla toplanır → aylık kimlik de kesin tutar.
        if tcmb is not None and tcmb_bddk is not None and tc_bal > 0:
            _ws_den = den
            # SIMPLE uzayı: her kovanın oranı KOVANIN KENDİ ortalama vadesiyle
            # simple'a çevrilir (bkt_tw/bkt_bal). Günlük PORTFÖY tenoru KULLANILMAZ:
            # gecelik-ağırlıklı kitapta portföy tenoru ~birkaç gün olur ve o kadar
            # kısa vadede simple≈compound → dönüşüm no-op görünürdü (prod'da
            # gözlenen "Simple = Compound" belirtisi). Kova-tenorlu dönüşümde uzun
            # kovaların simple'ı belirgin düşer. Referans = sektör-ağırlıklı kova
            # simple ortalaması → Σ katkı_s = Σ(wB−wS)·rB_s (kova-bazlı simple mix
            # farkı; kesin). Compound tarafında kimlik değişmedi (R̄ = TCMB_BDDK).
            _binfo = []
            for k in _TCMB_KEY_ORDER:
                if bkt_bal.get(k, 0.0) <= 0:
                    continue
                wB = bkt_bal[k] / tc_bal
                wS = (weights.get(k, 0.0) / _ws_den) if _ws_den > 0 else 0.0
                rB = bkt_w[k] / bkt_bal[k]
                ten_b = bkt_tw[k] / bkt_bal[k]            # kovanın kendi wavg vadesi
                rB_s = compound_to_simple_pct(rB, ten_b)
                _binfo.append((k, wB, wS, rB, rB_s))
            _rs_ref = (sum(x[2] * x[4] for x in _binfo)
                       if _binfo and all(x[4] is not None for x in _binfo) else None)
            for k, wB, wS, rB, rB_s in _binfo:
                katki_s = ((wB - wS) * (rB_s - _rs_ref)
                           if (rB_s is not None and _rs_ref is not None) else None)
                attr_rows.append({
                    "TARIH":  pd.Timestamp(day),
                    "KOVA":   k,
                    "W_BANK": wB,
                    "W_SEKTOR": wS,
                    "R_B":    rB,
                    "R_B_S":  rB_s,
                    "KATKI":  (wB - wS) * (rB - tcmb_bddk),   # % puan (compound)
                    "KATKI_S": katki_s,                        # % puan (simple, kova-tenorlu)
                    "BAKIYE": tot,                            # aylık ağırlık tabanı
                })
        rows.append({
            "TARIH":             pd.Timestamp(day),
            "BAKIYE":            round(tot, 2),
            "WAVG_TENOR":        round(wavg_tenor, 1),
            "BANK_COMP_PCT":     round(bank_comp, 4),
            "TCMB_PCT":          (round(tcmb, 4) if tcmb is not None else None),
            "SPREAD_COMP_PCT":   (round(bank_comp - tcmb, 4) if tcmb is not None else None),
            "BANK_SIMPLE_PCT":   (round(bank_simple, 4) if bank_simple is not None else None),
            "TCMB_SIMPLE_PCT":   (round(tcmb_simple, 4) if tcmb_simple is not None else None),
            "SPREAD_SIMPLE_PCT": (round(bank_simple - tcmb_simple, 4)
                                  if (bank_simple is not None and tcmb_simple is not None) else None),
            "TCMB_BDDK_PCT":        (round(tcmb_bddk, 4) if tcmb_bddk is not None else None),
            "TCMB_BDDK_SIMPLE_PCT": (round(tcmb_bddk_simple, 4)
                                     if tcmb_bddk_simple is not None else None),
            "BDDK_W_AY":            w_ay,
            "BDDK_W_KAPSAM":        w_kapsam,
            # Yeni spread'ler (simple uzayı): banka vs sektör-mix maliyeti; ve saf
            # MIX ETKİSİ (aynı TCMB fiyatları, banka vs sektör ağırlığı).
            "SPREAD_BANK_BDDK_PCT": (round(bank_simple - tcmb_bddk_simple, 4)
                                     if (bank_simple is not None and tcmb_bddk_simple is not None) else None),
            "MIX_SIMPLE_PCT":       (round(tcmb_simple - tcmb_bddk_simple, 4)
                                     if (tcmb_simple is not None and tcmb_bddk_simple is not None) else None),
            # Sektör wavg tenoru + O/N eşlenikleri ve O/N spread'leri.
            "BDDK_TENOR":           (round(bddk_tenor, 1) if bddk_tenor is not None else None),
            "BANK_ON_PCT":          (round(bank_on, 4) if bank_on is not None else None),
            "TCMB_ON_PCT":          (round(tcmb_on, 4) if tcmb_on is not None else None),
            "TCMB_BDDK_ON_PCT":     (round(tcmb_bddk_on, 4) if tcmb_bddk_on is not None else None),
            "SPREAD_ON_PCT":        (round(bank_on - tcmb_on, 4)
                                     if (bank_on is not None and tcmb_on is not None) else None),
            "SPREAD_BANK_BDDK_ON_PCT": (round(bank_on - tcmb_bddk_on, 4)
                                        if (bank_on is not None and tcmb_bddk_on is not None) else None),
        })
    out = pd.DataFrame(rows, columns=_COLS)
    out = out.sort_values("TARIH", ascending=False).reset_index(drop=True)
    attr = pd.DataFrame(attr_rows, columns=["TARIH", "KOVA", "W_BANK", "W_SEKTOR",
                                            "R_B", "R_B_S", "KATKI", "KATKI_S", "BAKIYE"])
    _notes = [n for n in (d["dq_note"], w_err) if n]
    _OUTSTANDING_CACHE = {"df": out, "attr": attr,
                          "dq_note": (" | ".join(_notes) if _notes else None)}
    return _OUTSTANDING_CACHE


_OUTSTANDING_MONTHLY_CACHE: Optional[dict] = None


def sector_outstanding_monthly() -> dict:
    """Gün-gün outstanding serisinin AYLIK özeti (bakiye-ağırlıklı ortalamalar).

    Günlük serideki her oran (banka compound/simple, TCMB, TCMB simple) ve wavg
    tenor, ilgili ayın GÜNLÜK BAKİYELERİYLE ağırlıklandırılarak tek gözleme iner:
      RATE(ay) = Σ_gün(rate_d · bakiye_d) / Σ_gün(bakiye_d)
    (TCMB'li ortalamalar yalnız TCMB'si olan günler üzerinden ağırlıklanır.)
    BAKIYE(ay) = ayın ortalama günlük bakiyesi. Spread'ler aylık ortalamaların
    farkı olarak hesaplanır. Kolonlar günlük tabloyla aynı, satır = ay.

    Döner: {"df": DataFrame[AY, BAKIYE, WAVG_TENOR, BANK_COMP_PCT, TCMB_PCT,
    SPREAD_COMP_PCT, BANK_SIMPLE_PCT, TCMB_SIMPLE_PCT, SPREAD_SIMPLE_PCT],
    "dq_note"} — ay azalan (en güncel üstte).
    """
    global _OUTSTANDING_MONTHLY_CACHE
    if _OUTSTANDING_MONTHLY_CACHE is not None:
        return _OUTSTANDING_MONTHLY_CACHE

    o = sector_outstanding_series()
    daily = o["df"]
    cols = ["AY", "BAKIYE", "WAVG_TENOR", "BANK_COMP_PCT", "TCMB_PCT",
            "SPREAD_COMP_PCT", "BANK_SIMPLE_PCT", "TCMB_SIMPLE_PCT", "SPREAD_SIMPLE_PCT",
            "TCMB_BDDK_PCT", "TCMB_BDDK_SIMPLE_PCT", "BDDK_W_AY", "BDDK_W_KAPSAM",
            "SPREAD_BANK_BDDK_PCT", "MIX_SIMPLE_PCT", "BDDK_TENOR",
            "BANK_ON_PCT", "TCMB_ON_PCT", "TCMB_BDDK_ON_PCT",
            "SPREAD_ON_PCT", "SPREAD_BANK_BDDK_ON_PCT"]
    if daily.empty:
        _OUTSTANDING_MONTHLY_CACHE = {"df": pd.DataFrame(columns=cols), "dq_note": o["dq_note"]}
        return _OUTSTANDING_MONTHLY_CACHE

    d = daily.copy()
    d["_ay"] = d["TARIH"].dt.to_period("M")

    def _wavg(sub: pd.DataFrame, col: str) -> Optional[float]:
        """col'u günlük BAKIYE ile ağırlıkla; col'u olmayan günler dışarıda."""
        m = sub[col].notna() & sub["BAKIYE"].notna() & (sub["BAKIYE"] > 0)
        if not m.any():
            return None
        w = sub.loc[m, "BAKIYE"].astype(float)
        return float((sub.loc[m, col].astype(float) * w).sum() / w.sum())

    rows = []
    for ay, sub in d.groupby("_ay"):
        bank_c = _wavg(sub, "BANK_COMP_PCT")
        tcmb_c = _wavg(sub, "TCMB_PCT")
        bank_s = _wavg(sub, "BANK_SIMPLE_PCT")
        tcmb_s = _wavg(sub, "TCMB_SIMPLE_PCT")
        bddk_c = _wavg(sub, "TCMB_BDDK_PCT")
        bddk_s = _wavg(sub, "TCMB_BDDK_SIMPLE_PCT")
        bddk_k = _wavg(sub, "BDDK_W_KAPSAM")
        bank_on = _wavg(sub, "BANK_ON_PCT")
        tcmb_on = _wavg(sub, "TCMB_ON_PCT")
        bddk_on = _wavg(sub, "TCMB_BDDK_ON_PCT")
        bddk_tn = _wavg(sub, "BDDK_TENOR")
        # Ay içinde en çok kullanılan BDDK ağırlık ayı (nowcast taşımasında
        # ay-sonu geçişindeki tek günlük fark modal ile sadeleşir).
        _ays = sub["BDDK_W_AY"].dropna()
        bddk_ay = (_ays.mode().iloc[0] if not _ays.empty else None)
        rows.append({
            "AY":                ay.to_timestamp(),                      # ay başı damgası
            "BAKIYE":            round(float(sub["BAKIYE"].mean()), 2),  # ort. günlük bakiye
            "WAVG_TENOR":        (round(_wavg(sub, "WAVG_TENOR"), 1)
                                  if _wavg(sub, "WAVG_TENOR") is not None else None),
            "BANK_COMP_PCT":     (round(bank_c, 4) if bank_c is not None else None),
            "TCMB_PCT":          (round(tcmb_c, 4) if tcmb_c is not None else None),
            "SPREAD_COMP_PCT":   (round(bank_c - tcmb_c, 4)
                                  if (bank_c is not None and tcmb_c is not None) else None),
            "BANK_SIMPLE_PCT":   (round(bank_s, 4) if bank_s is not None else None),
            "TCMB_SIMPLE_PCT":   (round(tcmb_s, 4) if tcmb_s is not None else None),
            "SPREAD_SIMPLE_PCT": (round(bank_s - tcmb_s, 4)
                                  if (bank_s is not None and tcmb_s is not None) else None),
            "TCMB_BDDK_PCT":        (round(bddk_c, 4) if bddk_c is not None else None),
            "TCMB_BDDK_SIMPLE_PCT": (round(bddk_s, 4) if bddk_s is not None else None),
            "BDDK_W_AY":            bddk_ay,
            "BDDK_W_KAPSAM":        (round(bddk_k, 1) if bddk_k is not None else None),
            "SPREAD_BANK_BDDK_PCT": (round(_wavg(sub, "SPREAD_BANK_BDDK_PCT"), 4)
                                     if _wavg(sub, "SPREAD_BANK_BDDK_PCT") is not None else None),
            "MIX_SIMPLE_PCT":       (round(_wavg(sub, "MIX_SIMPLE_PCT"), 4)
                                     if _wavg(sub, "MIX_SIMPLE_PCT") is not None else None),
            # Sektör tenoru + O/N eşlenikleri (bakiye-ağırlıklı aylık ortalama);
            # O/N spread'ler = aylık ortalamaların farkı (comp/simple ile aynı
            # konvansiyon).
            "BDDK_TENOR":           (round(bddk_tn, 1) if bddk_tn is not None else None),
            "BANK_ON_PCT":          (round(bank_on, 4) if bank_on is not None else None),
            "TCMB_ON_PCT":          (round(tcmb_on, 4) if tcmb_on is not None else None),
            "TCMB_BDDK_ON_PCT":     (round(bddk_on, 4) if bddk_on is not None else None),
            "SPREAD_ON_PCT":        (round(bank_on - tcmb_on, 4)
                                     if (bank_on is not None and tcmb_on is not None) else None),
            "SPREAD_BANK_BDDK_ON_PCT": (round(bank_on - bddk_on, 4)
                                        if (bank_on is not None and bddk_on is not None) else None),
        })
    out = (pd.DataFrame(rows, columns=cols)
           .sort_values("AY", ascending=False).reset_index(drop=True))
    _OUTSTANDING_MONTHLY_CACHE = {"df": out, "dq_note": o["dq_note"]}
    return _OUTSTANDING_MONTHLY_CACHE


def sector_mix_attribution_monthly() -> pd.DataFrame:
    """Mix etkisinin (banka mix − sektör mix TCMB, compound) AYLIK kova ayrıştırması.

    Günlük katkılar (bkz. sector_outstanding_series içindeki KESİN kimlik:
    Σ_b katkı_b(d) = TCMB(d) − TCMB_BDDK(d)) ayın günlük bakiyeleriyle
    ağırıklanarak aya iner; ağırlıklar lineer olduğundan aylık kimlik de tutar:
    Σ_b KATKI_b(ay) ≈ aylık MIX etkisi (compound). Kova bilgileri de aynı
    ağırlıkla ortalanır (W_BANK/W_SEKTOR/R_B — yorum kolonları).

    Döner: AY | KOVA (key) | W_BANK | W_SEKTOR | R_B (%) | KATKI_BPS
    (ay artan, kova sırası).
    """
    o = sector_outstanding_series()
    attr = o.get("attr")
    cols = ["AY", "KOVA", "W_BANK", "W_SEKTOR", "R_B", "R_B_S", "KATKI_BPS", "KATKI_S_BPS"]
    if attr is None or attr.empty:
        return pd.DataFrame(columns=cols)
    a = attr.copy()
    a["_ay"] = a["TARIH"].dt.to_period("M")
    rows = []
    for (ay, kova), sub in a.groupby(["_ay", "KOVA"], observed=True):
        w = sub["BAKIYE"].astype(float)
        tw = float(w.sum())
        if tw <= 0:
            continue
        # Simple tarafı: KATKI_S/R_B_S None olabilir (dönüşüm başarısızsa) —
        # yalnız dolu satırlar üzerinden aynı bakiye ağırlığıyla ortala.
        _ms = sub["KATKI_S"].notna()
        _s_tw = float(w[_ms].sum())
        rows.append({
            "AY":        ay.to_timestamp(),
            "KOVA":      kova,
            "W_BANK":    round(float((sub["W_BANK"] * w).sum() / tw), 4),
            "W_SEKTOR":  round(float((sub["W_SEKTOR"] * w).sum() / tw), 4),
            "R_B":       round(float((sub["R_B"] * w).sum() / tw), 4),
            "R_B_S":     (round(float((sub.loc[_ms, "R_B_S"] * w[_ms]).sum() / _s_tw), 4)
                          if _s_tw > 0 else None),
            "KATKI_BPS": round(float((sub["KATKI"] * w).sum() / tw) * 100.0, 2),
            "KATKI_S_BPS": (round(float((sub.loc[_ms, "KATKI_S"] * w[_ms]).sum() / _s_tw) * 100.0, 2)
                            if _s_tw > 0 else None),
        })
    out = pd.DataFrame(rows, columns=cols)
    _ord = {k: i for i, k in enumerate(_TCMB_KEY_ORDER)}
    out["_ko"] = out["KOVA"].map(_ord)
    out = out.sort_values(["AY", "_ko"]).drop(columns="_ko").reset_index(drop=True)
    return out


def bddk_mix_weight_summary() -> str:
    """Açılış teşhisi: BDDK-mix ağırlıkları kuruldu mu, hangi aylar? (tek satır).

    'BDDK Mix kolonları neden boş' sorusunu startup log'unda KESİN cevaplar:
    ağırlık sayısı + aralık + örnek kova payları, ya da üretilememe nedeni.
    """
    try:
        w = _bddk_vade_weights()
    except Exception as e:
        return "HATA — {}".format(e)
    if not w:
        try:
            v = load_bddk_vade()
            tp = int((v["CCY_CODE"].astype(str).str.strip() == "TP").sum()) if not v.empty else 0
            return "BOŞ — BDDK_VADE {} satır, TP satırı {}".format(len(v), tp)
        except Exception as e:
            return "BOŞ — BDDK_VADE okunamadı ({})".format(e)
    last = max(w)
    return "{} ay-sonu ({} → {}); son ay payları: {}".format(
        len(w), min(w).date(), last.date(),
        ", ".join("{}={:.1%}".format(k, v) for k, v in sorted(w[last].items())))


# ── Vade dağılımı karşılaştırması: Banka vs Sektör (BDDK_VADE) ────────────────

_VADE_MIX_ORDER = ["v0_1", "v1_3", "v3_6", "v6_12", "v1y"]
_VADE_MIX_LABELS = {"v0_1": "0-1 M", "v1_3": "1-3 M", "v3_6": "3-6 M",
                    "v6_12": "6-12 M", "v1y": "1Y+"}

_BANK_VADE_CACHE: Optional[tuple] = None


def _bank_vade_daily() -> tuple:
    """daily_deposit → gün × TCMB kovası günlük TRY bakiye toplamları.

    Döner: (df[DAT, _key, BAL], note|None). Banka VADE_BUCKET'ı gün-aralığı
    etiketidir ('4-31', '92-149' …); alt sınır günü _days_to_tcmb_key ile BDDK
    kovalarıyla aynı sınırlara (31/91/181/365) oturur — banka kovaları bu
    sınırları YARMAZ, eşleme kayıpsızdır. Sayısal parse edilemeyen bucket'lar
    (ör. vade-türetilemez satırlar) dışlanır, hacim payı note'ta raporlanır.
    """
    global _BANK_VADE_CACHE
    if _BANK_VADE_CACHE is not None:
        return _BANK_VADE_CACHE
    dd = load_dataframe("daily_deposit")[["DAT", "VADE_BUCKET", "GUNLUK_TRY_BAKIYE"]].copy()
    dd["DAT"] = pd.to_datetime(dd["DAT"], errors="coerce")
    dd["BAL"] = pd.to_numeric(dd["GUNLUK_TRY_BAKIYE"], errors="coerce").fillna(0.0)
    dd = dd[dd["DAT"].notna()]
    low = pd.to_numeric(dd["VADE_BUCKET"].astype(str).str.strip()
                        .str.extract(r"^(\d+)", expand=False), errors="coerce")
    dd["_key"] = low.map(lambda d: _days_to_tcmb_key(int(d)) if pd.notna(d) else None)
    note = None
    bad = dd["_key"].isna()
    if bad.any():
        tot = float(dd["BAL"].abs().sum())
        share = float(dd.loc[bad, "BAL"].abs().sum()) / tot * 100 if tot > 0 else 0.0
        note = ("Bank: VADE_BUCKET values that could not be mapped to a maturity "
                "bucket were excluded ({}; {:.2f}% of volume).".format(
                    ", ".join(sorted(dd.loc[bad, "VADE_BUCKET"].astype(str).unique())[:5]),
                    share))
    g = (dd[~bad].groupby(["DAT", "_key"], observed=True)["BAL"].sum()
         .reset_index())
    _BANK_VADE_CACHE = (g, note)
    return _BANK_VADE_CACHE


def _sector_vade_by_month() -> dict:
    """BDDK_VADE → {ay_sonu: {kova_key: bakiye}} (TP, tüm banka tipleri, Y_I∪Y_D,
    Vadesiz HARİÇ). _bddk_vade_weights ile aynı kapsam, ama PAY değil ham bakiye."""
    v = load_bddk_vade()
    if v.empty:
        return {}
    v = v.copy()
    v["CCY_CODE"] = v["CCY_CODE"].astype(str).str.strip()
    v["VADE_KIRILIM"] = v["VADE_KIRILIM"].astype(str).str.strip()
    v = v[v["CCY_CODE"] == "TP"]
    lbl = v["VADE_KIRILIM"]
    unknown = sorted(lbl[~(lbl.isin(_BDDK_VADE_TO_TCMB) | (lbl == "Vadesiz"))].unique())
    if unknown:
        raise ValueError(
            "VADE_KIRILIM value(s) missing from the BDDK_VADE map: {} — "
            "update _BDDK_VADE_TO_TCMB.".format(unknown))
    v = v[lbl.isin(_BDDK_VADE_TO_TCMB)]
    v["_key"] = v["VADE_KIRILIM"].map(_BDDK_VADE_TO_TCMB)
    g = v.groupby([v["TARIH"].dt.normalize(), "_key"])["BAKIYE_TL"].sum()
    out: dict = {}
    for (tarih, key), bal in g.items():
        out.setdefault(pd.Timestamp(tarih), {})[key] = float(bal)
    return out


# Kova temsilci vadesi (gün) — sektör wavg tenor türetimi için. Orta noktalar;
# 1Yıl+ açık uçlu → ~15 ay varsayımı (TR mevduatında 1Y+ kitap tipik 12-18 ay;
# payı küçük olduğundan duyarlılık düşük).
_VADE_REP_TENOR_GUN = {"v0_1": 16, "v1_3": 61, "v3_6": 136, "v6_12": 274, "v1y": 456}

# Sektör faiz segmentleri (sector_deposit_rate_series ile aynı adlar) → tenor
# ağırlıklarına girecek BANKA_TIPI kümeleri.
_TENOR_SEGMENTS = {
    "Private Sector": {"Yerli Özel", "Yabancı"},
    "Total Sector":   {"Yerli Özel", "Yabancı", "Kamu"},
}


def sector_wavg_tenor_by_month(bucket_tenor_by_month: Optional[dict] = None) -> dict:
    """Sektör aylık ağırlıklı ortalama vade (gün): {(segment, Period(ay)): gün}.

    BDDK_VADE TP kova bakiyeleri (Vadesiz HARİÇ, faizli kitaba normalize) ×
    kova vadeleri. Kova vadesi: bucket_tenor_by_month verilirse
    {(Period(ay), kova_key): gün} — BANKANIN o ay o kovadaki bakiye-ağırlıklı
    vadesi (kullanıcı konvansiyonu: sektör kova içi vadesi ≈ bankanınki;
    BDDK-mix yaklaşımıyla tutarlı). O ay/kova için banka verisi yoksa
    _VADE_REP_TENOR_GUN orta noktasına düşülür. Segment bazlı: Özel Sektör =
    Yerli Özel + Yabancı (Kamu hariç), Toplam Sektör = tümü.
    """
    v = load_bddk_vade()
    if v.empty:
        return {}
    v = v.copy()
    v["CCY_CODE"] = v["CCY_CODE"].astype(str).str.strip()
    v["VADE_KIRILIM"] = v["VADE_KIRILIM"].astype(str).str.strip()
    v["BANKA_TIPI"] = v["BANKA_TIPI"].astype(str).str.strip()
    v = v[(v["CCY_CODE"] == "TP") & v["VADE_KIRILIM"].isin(_BDDK_VADE_TO_TCMB)]
    if v.empty:
        return {}
    v["_key"] = v["VADE_KIRILIM"].map(_BDDK_VADE_TO_TCMB)
    v["_per"] = v["TARIH"].dt.to_period("M")
    bt = bucket_tenor_by_month or {}

    def _tenor(per, key):
        t = bt.get((per, key))
        return float(t) if t is not None and t > 0 else float(_VADE_REP_TENOR_GUN[key])

    out: dict = {}
    for seg, tipler in _TENOR_SEGMENTS.items():
        s = v[v["BANKA_TIPI"].isin(tipler)]
        g = s.groupby(["_per", "_key"])["BAKIYE_TL"].sum()
        for per in g.index.get_level_values(0).unique():
            d = g.loc[per]
            tot = float(d.sum())
            if tot <= 0:
                continue
            out[(seg, per)] = round(sum(
                float(b) / tot * _tenor(per, k) for k, b in d.items()), 1)
    return out


def _bank_vade_snapshot(t: pd.Timestamp, mode: str) -> tuple:
    """Banka kova bakiyeleri tek tarih için: (bal_dict|None, info|None, notes).

    mode="monthly" → t'nin ayının GÜNLÜK ortalaması (ayda veri olan günler);
    mode="daily"   → t'ye EN YAKIN gün (eşitlikte önceki; >31 gün uzaksa None).
    """
    notes: list = []
    bank, bnote = _bank_vade_daily()
    if bnote:
        notes.append(bnote)
    if bank.empty:
        notes.append("Bank: daily_deposit data is empty.")
        return None, None, notes
    if mode == "monthly":
        m = bank[(bank["DAT"].dt.year == t.year) & (bank["DAT"].dt.month == t.month)]
        if m.empty:
            notes.append("Bank: no daily_deposit data in {}-{:02d}.".format(t.year, t.month))
            return None, None, notes
        n_days = m["DAT"].nunique()
        s = m.groupby("_key", observed=True)["BAL"].sum()
        # Kova o gün yoksa 0 sayılır → ortalama = Σ/gün_sayısı.
        bal = {k: float(s.get(k, 0.0)) / n_days for k in _VADE_MIX_ORDER}
        return bal, "{}-{:02d} daily avg. ({} days)".format(t.year, t.month, n_days), notes
    # En yakın gün; eşit uzaklıkta ÖNCEKİ gün tercih (d > t tie-break'i).
    days = list(bank["DAT"].drop_duplicates())
    nearest = min(days, key=lambda d: (abs((d - t).days), d > t))
    if abs((nearest - t).days) > 31:
        notes.append("Bank: no daily_deposit day within 31 days of the selected date "
                     "(nearest: {}).".format(nearest.date()))
        return None, None, notes
    d = bank[bank["DAT"] == nearest]
    s = d.groupby("_key", observed=True)["BAL"].sum()
    bal = {k: float(s.get(k, 0.0)) for k in _VADE_MIX_ORDER}
    if nearest != t:
        notes.append("Bank: no data on the selected date — nearest day {} "
                     "was used.".format(nearest.date()))
    return bal, str(nearest.date()), notes


def _vade_shares(bal: dict) -> list:
    tot = sum(bal.get(k, 0.0) for k in _VADE_MIX_ORDER)
    return [round(bal.get(k, 0.0) / tot * 100.0, 2) if tot > 0 else 0.0
            for k in _VADE_MIX_ORDER]


def _vade_gap_bn(bank_bal: dict, sector_pct: list) -> list:
    """Kova payını sektöre eşitlemek için gereken mevduat (milyar TL, işaretli).

    Kovaya x alınca TOPLAM da değişir → öz-tutarlı çözüm:
      (b_k + x) / (T + x) = s_k  ⇒  x = (s_k·T − b_k) / (1 − s_k)
    Pozitif = banka o kovaya mevduat ALMALI, negatif = ÇIKARMALI. s_k ≥ 1
    (dejenere) için None. bank_bal TL cinsindendir → /1e9.
    """
    tot = sum(bank_bal.get(k, 0.0) for k in _VADE_MIX_ORDER)
    out = []
    for k, sp in zip(_VADE_MIX_ORDER, sector_pct):
        s = sp / 100.0
        if s >= 1.0:
            out.append(None)
            continue
        x = (s * tot - bank_bal.get(k, 0.0)) / (1.0 - s)
        out.append(round(x / 1e9, 3))
    return out


def vade_mix_comparison(date_str: Optional[str], mode: str) -> dict:
    """Banka vs Sektör vade dağılımı (%, faizli kitap) — seçilen BDDK ay-sonu için.

    mode="monthly": banka = seçilen ayın GÜNLÜK bakiye ortalaması (kova bazında,
      ayda veri olan günler üzerinden); sektör = (seçilen ay-sonu + önceki
      ay-sonu) / 2 ortalama bakiye.
    mode="daily":   banka = seçilen tarihe EN YAKIN daily_deposit günü (eşitlikte
      önceki; >31 gün uzaksa banka tarafı boş + not); sektör = seçilen ay-sonu
      bakiyesi olduğu gibi.

    Paylar her iki tarafta da Vadesiz HARİÇ faizli kitap toplamına normalize
    edilir (banka daily_deposit zaten vadeli kitap). Döner:
    {dates, date, mode, buckets, bank_pct, sector_pct, diff_pp,
     bank_date_info, sector_date_info, notes}
    """
    sec = _sector_vade_by_month()
    if not sec:
        return {"dates": [], "date": None, "mode": mode, "buckets": [],
                "bank_pct": [], "sector_pct": [], "diff_pp": [],
                "bank_date_info": None, "sector_date_info": None,
                "notes": ["BDDK_VADE data is empty."]}
    dates = sorted(sec)
    t = pd.to_datetime(date_str) if date_str else dates[-1]
    if t not in sec:
        t = dates[-1]
    notes: list = []

    # ── Sektör bakiyeleri ────────────────────────────────────────────────────
    if mode == "monthly":
        idx = dates.index(t)
        prev = dates[idx - 1] if idx > 0 else None
        if prev is not None:
            sec_bal = {k: (sec[t].get(k, 0.0) + sec[prev].get(k, 0.0)) / 2.0
                       for k in _VADE_MIX_ORDER}
            sector_date_info = "({} + {}) / 2".format(prev.date(), t.date())
        else:
            sec_bal = {k: sec[t].get(k, 0.0) for k in _VADE_MIX_ORDER}
            sector_date_info = str(t.date())
            notes.append("Sector: no previous month-end — the {} balance was used "
                         "instead of the average.".format(t.date()))
    else:
        sec_bal = {k: sec[t].get(k, 0.0) for k in _VADE_MIX_ORDER}
        sector_date_info = str(t.date())

    # ── Banka bakiyeleri ─────────────────────────────────────────────────────
    bank_bal, bank_date_info, bnotes = _bank_vade_snapshot(t, mode)
    notes.extend(bnotes)

    sector_pct = _vade_shares(sec_bal)
    bank_pct = _vade_shares(bank_bal) if bank_bal is not None else None
    diff_pp = ([round(b - s, 2) for b, s in zip(bank_pct, sector_pct)]
               if bank_pct is not None else None)
    return {
        "dates":   [str(d.date()) for d in dates],
        "date":    str(t.date()),
        "mode":    mode,
        "buckets": [_VADE_MIX_LABELS[k] for k in _VADE_MIX_ORDER],
        "bank_pct": bank_pct,
        "sector_pct": sector_pct,
        "diff_pp": diff_pp,
        "bank_date_info": bank_date_info,
        "sector_date_info": sector_date_info,
        "notes": notes,
    }


def vade_mix_presentation(date_end: Optional[str], mode: str) -> dict:
    """BSC Presentation Slide 4 — vade dağılımı: Sektör + Banka×2 tarih.

    Sektör tarihi (t_sec) = prezentasyonun Date(End)'ine en yakın BDDK ay-sonu
    (monthly modda önce AYNI AY tercih edilir — Date(End) ay temsilcisidir).
    Sektör bakiyesi: monthly → (t_sec + önceki ay-sonu)/2, daily → olduğu gibi.
    Banka İKİ seri (_bank_vade_snapshot ile): A = t_sec'e karşılık gelen
    (monthly: o ayın günlük ort.; daily: en yakın gün), B = Date(End)'e karşılık
    gelen. A ile B aynı kaynağa düşerse (BDDK gecikmesi yoksa) B tek başına
    döner, not eklenir. Fark panelleri: A−Sektör ve B−Sektör (puan).
    """
    sec = _sector_vade_by_month()
    if not sec:
        return {"date_end": None, "sector_date": None, "mode": mode, "buckets": [],
                "sector_pct": [], "sector_date_info": None,
                "bank_a_pct": None, "bank_a_info": None, "diff_a_pp": None,
                "bank_b_pct": None, "bank_b_info": None, "diff_b_pp": None,
                "notes": ["BDDK_VADE data is empty."]}
    dates = sorted(sec)
    te = pd.to_datetime(date_end) if date_end else dates[-1]
    notes: list = []

    # Sektör tarihi seçimi.
    t_sec = None
    if mode == "monthly":
        same = [d for d in dates if d.year == te.year and d.month == te.month]
        if same:
            t_sec = same[0]
    if t_sec is None:
        t_sec = min(dates, key=lambda d: (abs((d - te).days), d > te))
        if mode == "monthly":
            notes.append("Sector: no BDDK data in the Date(End) month — nearest "
                         "month-end {} was used.".format(t_sec.date()))

    # Sektör bakiyeleri.
    if mode == "monthly":
        idx = dates.index(t_sec)
        prev = dates[idx - 1] if idx > 0 else None
        if prev is not None:
            sec_bal = {k: (sec[t_sec].get(k, 0.0) + sec[prev].get(k, 0.0)) / 2.0
                       for k in _VADE_MIX_ORDER}
            sector_date_info = "({} + {}) / 2".format(prev.date(), t_sec.date())
        else:
            sec_bal = {k: sec[t_sec].get(k, 0.0) for k in _VADE_MIX_ORDER}
            sector_date_info = str(t_sec.date())
            notes.append("Sector: no previous month-end — the {} balance was used "
                         "instead of the average.".format(t_sec.date()))
    else:
        sec_bal = {k: sec[t_sec].get(k, 0.0) for k in _VADE_MIX_ORDER}
        sector_date_info = str(t_sec.date())

    # Banka: A = sektör tarihi, B = prezentasyon Date(End).
    bal_a, info_a, na = _bank_vade_snapshot(t_sec, mode)
    bal_b, info_b, nb = _bank_vade_snapshot(te, mode)
    # Aynı kaynağa düşen iki seri (ör. BDDK gecikmesizken) tek seride birleşir;
    # notlar da tekrarlanmasın.
    if bal_a is not None and bal_b is not None and info_a == info_b:
        bal_a, info_a = None, None
        notes.append("Bank: the sector date and Date(End) resolve to the same source — "
                     "showing a single bank series.")
        notes.extend(nb)
    else:
        notes.extend(na)
        notes.extend([n for n in nb if n not in notes])

    sector_pct = _vade_shares(sec_bal)
    a_pct = _vade_shares(bal_a) if bal_a is not None else None
    b_pct = _vade_shares(bal_b) if bal_b is not None else None
    # Hover eki: kova payını sektöre eşitlemek için gereken mevduat (milyar TL).
    gap_a = _vade_gap_bn(bal_a, sector_pct) if bal_a is not None else None
    gap_b = _vade_gap_bn(bal_b, sector_pct) if bal_b is not None else None
    return {
        "date_end":    str(te.date()),
        "sector_date": str(t_sec.date()),
        "mode":        mode,
        "buckets":     [_VADE_MIX_LABELS[k] for k in _VADE_MIX_ORDER],
        "sector_pct":  sector_pct,
        "sector_date_info": sector_date_info,
        "bank_a_pct":  a_pct,
        "bank_a_info": info_a,
        "diff_a_pp":   ([round(x - s, 2) for x, s in zip(a_pct, sector_pct)]
                        if a_pct is not None else None),
        "gap_a_bn":    gap_a,
        "bank_b_pct":  b_pct,
        "bank_b_info": info_b,
        "diff_b_pp":   ([round(x - s, 2) for x, s in zip(b_pct, sector_pct)]
                        if b_pct is not None else None),
        "gap_b_bn":    gap_b,
        "notes": notes,
    }


def warm_all() -> dict:
    """Sektör/piyasa df'lerini yükle (açılış cache warm). {ad: satır_sayısı} döner."""
    return {
        "BDDK_AMT_KIRILIM":   len(load_bddk_amt_kirilim()),
        "BDDK_VADE":          len(load_bddk_vade()),
        "BDDK_MALIYET":       len(load_bddk_maliyet()),
        "tcmb_deposit_rates": len(load_tcmb_deposit_rates()),
        "bist_tlref":         len(load_bist_tlref()),
    }


def reset_caches() -> None:
    """Bu modüldeki tüm process-ömrü cache'lerini (baz + türetilmiş) boşaltır;
    data-refresh endpoint'i çağırır. Sonraki load_*'lar SQL'i yeniden koşar."""
    global _AMT_CACHE, _VADE_CACHE, _MALIYET_CACHE, _TCMB_CACHE, _TLREF_CACHE
    global _RATE_SERIES_CACHE, _BLOTTER_CACHE, _DETAIL_CACHE, _OUTSTANDING_CACHE
    global _OUTSTANDING_MONTHLY_CACHE, _BANK_VADE_CACHE
    _AMT_CACHE = _VADE_CACHE = _MALIYET_CACHE = _TCMB_CACHE = _TLREF_CACHE = None
    _RATE_SERIES_CACHE = _BLOTTER_CACHE = _DETAIL_CACHE = _OUTSTANDING_CACHE = None
    _OUTSTANDING_MONTHLY_CACHE = _BANK_VADE_CACHE = None
    _HARM_BUCKET_CACHE.clear()

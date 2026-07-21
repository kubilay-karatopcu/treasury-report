"""Future Deposit Rollings motoru — WeeklyRollingsEngine + KVKK maskesi.

Kaynak: NIM_calculation (bs_evolution5 @ c569ae3) — satır referansları blok
başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları uyarlandı
(bkz. mevduat_panel/tools/extract_a4a5.py).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_source import load_dataframe
from .common import _aum_numeric_key, _wavg

import logging

log = logging.getLogger("mevduat_panel")


# Port notu: kaynak config.ENV — _to_bind yalnız prod yolunu kullanır.
_ENV = "PRODUCTION_DB"

# ── app.py 3214-3663 ──
class WeeklyRollingsEngine:
    """Pivot raw mevduat dönüşleri rows into three weekly-report tables."""

    # CUST_TP codes — verified in prod via SELECT DISTINCT CUST_TP (see doc).
    CUST_TP_GERCEK = "G"
    CUST_TP_TUZEL  = "T"

    SCALE = 1_000_000.0   # ₺ → million-₺

    # 11 AUM_LOWER values → 6 display bands. Order matters: pd.Categorical.
    BAND_ORDER = ["0-5M", "5M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+"]
    AUM_TO_BAND = {
        0:                "0-5M",
        1_000_000:        "0-5M",
        2_000_000:        "0-5M",
        5_000_000:        "5M-25M",
        10_000_000:       "5M-25M",
        25_000_000:       "25M-50M",
        50_000_000:       "50M-100M",
        100_000_000:      "100M-200M",
        200_000_000:      "200M+",
        500_000_000:      "200M+",
        1_000_000_000:    "200M+",
    }

    @classmethod
    def _load(cls, date_start: str, date_end: str) -> pd.DataFrame:
        # Endpoint UI'dan DD/MM/YYYY alır. Oracle SQL TO_DATE(:X, 'DD/MM/YYYY')
        # ile parse ediyor → prod bind DD/MM/YYYY olarak gider. DEV'de SQLite
        # tablosundaki MTRTY_DT TEXT/ISO formatında tutulur ve string-compare
        # yapılır; DD/MM/YYYY ile compare yanlış sıralama verir (01/04 < 16/03).
        # Bu yüzden DEV'e bind ISO YYYY-MM-DD'ye dönüştürülerek gönderilir.
        key = (date_start, date_end)
        cached = _WEEKLY_AGG_DF_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        ds_bind, de_bind = cls._to_bind(date_start), cls._to_bind(date_end)
        df = load_dataframe("weekly_rollings",
                            params={"DATE_START": ds_bind, "DATE_END": de_bind})
        if df.empty:
            _WEEKLY_AGG_DF_CACHE[key] = df
            return df
        # SQLite returns ROLL_DATE as ISO string; Oracle as native datetime.
        df["ROLL_DATE"] = pd.to_datetime(df["ROLL_DATE"], errors="coerce")
        df["AUM_BAND"] = pd.Categorical(
            df["AUM_LOWER"].map(cls.AUM_TO_BAND),
            categories=cls.BAND_ORDER, ordered=True,
        )
        # Surface unmapped AUM_LOWER values loudly — sessiz NaN drop YASAK.
        if df["AUM_BAND"].isna().any():
            unmapped = sorted(df.loc[df["AUM_BAND"].isna(), "AUM_LOWER"].unique().tolist())
            raise ValueError(
                f"AUM_LOWER {unmapped!r} missing from the AUM_TO_BAND map. "
                f"Sync with docs/weekly_rollings_veri_dokumantasyon.md."
            )
        _WEEKLY_AGG_DF_CACHE[key] = df.copy()
        return df

    @classmethod
    def _to_bind(cls, ddmmyyyy: str) -> str:
        """DD/MM/YYYY → SQL bind formatı. PROD: aynen geçirir (TO_DATE format
        string ile uyumlu). DEV (SQLite TEXT compare): YYYY-MM-DD'ye çevirir.
        dev.db eklendiğinde (A1 2. revize) sabit _ENV yerine data_source
        yoluna bakılır — aksi halde DEV'de DD/MM/YYYY string'i ISO kolonla
        karşılaştırılıp weekly HER pencerede boş dönüyordu."""
        from ..data_source import is_dev
        if is_dev():
            return pd.to_datetime(ddmmyyyy, format="%d/%m/%Y").strftime("%Y-%m-%d")
        return ddmmyyyy

    @classmethod
    def _fmt_date(cls, d: pd.Timestamp) -> str:
        """gg/aa/yyyy — report format."""
        return d.strftime("%d/%m/%Y")

    @classmethod
    def _pivot_currency(cls, df: pd.DataFrame) -> Dict:
        """Tablo 1 — Genel (TRY + FX), her gün 2 satır."""
        if df.empty:
            return {"kind": "currency", "rows": [], "footer": [],
                    "columns": cls.BAND_ORDER, "color": "blue"}

        piv = (df.groupby(["ROLL_DATE", "CURRENCY", "AUM_BAND"], dropna=False, observed=False)
                 ["TRY_BAKIYE_TOPLAM"].sum()
                 .unstack("AUM_BAND", fill_value=0.0)
                 .reindex(columns=cls.BAND_ORDER, fill_value=0.0)) / cls.SCALE
        piv = piv.round(0).astype(int)
        piv["TOTAL"] = piv.sum(axis=1).astype(int)

        # % of Total uses each currency-pair (TRY+FX) day total over grand total.
        day_totals = piv.groupby(level="ROLL_DATE")["TOTAL"].sum()
        grand_total = int(piv["TOTAL"].sum())
        pct_lookup = {
            d: round(t / grand_total * 100.0) if grand_total > 0 else 0
            for d, t in day_totals.items()
        }

        # Build rows: for each ROLL_DATE emit TRY first, then FX. Only the TRY
        # row carries pct_of_total (frontend renders as rowspan=2 visually).
        rows: List[Dict] = []
        for roll_date in sorted(piv.index.get_level_values("ROLL_DATE").unique()):
            day_str = cls._fmt_date(roll_date)
            day_pct = pct_lookup.get(roll_date, 0)
            for idx, curr in enumerate(["TRY", "FX"]):
                if (roll_date, curr) not in piv.index:
                    continue
                row = piv.loc[(roll_date, curr)]
                rows.append({
                    "label":         curr,
                    "date":          day_str,
                    "values":        [int(row[b]) for b in cls.BAND_ORDER],
                    "total":         int(row["TOTAL"]),
                    "pct_of_total":  int(day_pct) if idx == 0 else None,
                })

        # Footer: FX sum, TRY sum, grand total — across all dates.
        footer: List[Dict] = []
        for curr in ["FX", "TRY"]:
            mask = piv.index.get_level_values("CURRENCY") == curr
            sub = piv[mask]
            if sub.empty:
                continue
            footer.append({
                "label":  curr,
                "date":   "",
                "values": [int(sub[b].sum()) for b in cls.BAND_ORDER],
                "total":  int(sub["TOTAL"].sum()),
                "pct_of_total": None,
            })
        footer.append({
            "label":  "Total",
            "date":   "",
            "values": [int(piv[b].sum()) for b in cls.BAND_ORDER],
            "total":  int(piv["TOTAL"].sum()),
            "pct_of_total": None,
        })

        return {"kind": "currency", "rows": rows, "footer": footer,
                "columns": cls.BAND_ORDER, "color": "blue"}

    @classmethod
    def _pivot_try_cust(cls, df: pd.DataFrame, cust_tp: str, color: str, kind: str) -> Dict:
        """Tablo 2/3 — TRY × (Gerçek|Tüzel), her gün 1 satır."""
        sub = df[(df["CURRENCY"] == "TRY") & (df["CUST_TP"] == cust_tp)]
        if sub.empty:
            return {"kind": kind, "rows": [], "footer": [],
                    "columns": cls.BAND_ORDER, "color": color}

        piv = (sub.groupby(["ROLL_DATE", "AUM_BAND"], dropna=False, observed=False)
                  ["TRY_BAKIYE_TOPLAM"].sum()
                  .unstack("AUM_BAND", fill_value=0.0)
                  .reindex(columns=cls.BAND_ORDER, fill_value=0.0)) / cls.SCALE
        piv = piv.round(0).astype(int)
        piv["TOTAL"] = piv.sum(axis=1).astype(int)

        grand_total = int(piv["TOTAL"].sum())
        rows: List[Dict] = []
        for roll_date in sorted(piv.index):
            row = piv.loc[roll_date]
            day_total = int(row["TOTAL"])
            pct = round(day_total / grand_total * 100.0) if grand_total > 0 else 0
            rows.append({
                "label":         "",
                "date":          cls._fmt_date(roll_date),
                "values":        [int(row[b]) for b in cls.BAND_ORDER],
                "total":         day_total,
                "pct_of_total":  int(pct),
            })

        footer = [{
            "label":  "Total",
            "date":   "",
            "values": [int(piv[b].sum()) for b in cls.BAND_ORDER],
            "total":  int(piv["TOTAL"].sum()),
            "pct_of_total": None,
        }]

        return {"kind": kind, "rows": rows, "footer": footer,
                "columns": cls.BAND_ORDER, "color": color}

    # DTM bucket sırası — Slide 1 altındaki vade dağılımı grafiğinde de kullanılır.
    DTM_BUCKET_ORDER = ["≤14", "15-32", "33-90", "91-180", "180+"]

    @classmethod
    def _dtm_bucket(cls, dtm: float) -> str:
        if dtm <= 14:  return "≤14"
        if dtm <= 32:  return "15-32"
        if dtm <= 90:  return "33-90"
        if dtm <= 180: return "91-180"
        return "180+"

    @classmethod
    def _dtm_histogram(cls, df_full: pd.DataFrame) -> List[Dict]:
        """Vade bucket × bakiye (mio TRY) — Slide 1 altındaki grafik."""
        if df_full.empty:
            return [{"bucket": b, "volume_m": 0.0, "ticket_count": 0}
                    for b in cls.DTM_BUCKET_ORDER]
        df = df_full.copy()
        df["DTM_BUCKET"] = df["DTM"].apply(cls._dtm_bucket)
        agg = df.groupby("DTM_BUCKET").agg(
            volume=("TRY_BALANCE", "sum"),
            ticket_count=("CUST_ID", "count"),
        ).reindex(cls.DTM_BUCKET_ORDER, fill_value=0).reset_index()
        return [{
            "bucket":       r["DTM_BUCKET"],
            "volume_m":     round(float(r["volume"]) / cls.SCALE, 2),
            "ticket_count": int(r["ticket_count"]),
        } for _, r in agg.iterrows()]

    @classmethod
    def build_payload(cls, date_start: str, date_end: str) -> Dict:
        df = cls._load(date_start, date_end)
        # DTM histogram detay-seviyesinde dataset üzerinden hesaplanır;
        # aggregate weekly_rollings.sql DTM_BUCKET kolonu içermiyor.
        df_full = cls._load_full(date_start, date_end)
        return {
            "date_start":    date_start,
            "date_end":      date_end,
            "row_count":     int(len(df)),
            "table_1":       cls._pivot_currency(df),
            "table_2":       cls._pivot_try_cust(df, cls.CUST_TP_GERCEK, "grey",   "try_gercek"),
            "table_3":       cls._pivot_try_cust(df, cls.CUST_TP_TUZEL,  "orange", "try_tuzel"),
            "dtm_histogram": cls._dtm_histogram(df_full),
        }

    # ════════════════════════════════════════════════════════════════════════
    # SLIDE 3 + DRILLDOWN: detay seviyesinde data üzerinden agregasyonlar.
    # Tek SQL load (weekly_rollings_full) → pandas tarafında slice'lanır.
    # ════════════════════════════════════════════════════════════════════════
    @classmethod
    def _load_full(cls, date_start: str, date_end: str) -> pd.DataFrame:
        key = (date_start, date_end)
        cached = _WEEKLY_FULL_DF_CACHE.get(key)
        if cached is not None:
            return cached.copy()
        ds_bind, de_bind = cls._to_bind(date_start), cls._to_bind(date_end)
        df = load_dataframe("weekly_rollings_full",
                            params={"DATE_START": ds_bind, "DATE_END": de_bind})
        if df.empty:
            _WEEKLY_FULL_DF_CACHE[key] = df
            return df
        df["ROLL_DATE"] = pd.to_datetime(df["ROLL_DATE"], errors="coerce")
        df["AUM_BAND"] = pd.Categorical(
            df["AUM_LOWER"].map(cls.AUM_TO_BAND),
            categories=cls.BAND_ORDER, ordered=True,
        )
        # Segment etiketi: önce kurum, sonra Private > Affluent > Maaşlı > Diğer.
        def _segment(r):
            if r.get("ISNPO") == 1:        return "NPO"
            if r.get("CUST_TP") == "T":    return "Corporate"
            if r.get("ISPRIVATE") == 1:    return "Private"
            if r.get("ISAFFLUENT") == 1:   return "Affluent"
            if r.get("ISMAASLI") == 1:     return "Salaried"
            return "Other"
        df["SEGMENT"] = df.apply(_segment, axis=1)
        df["HAS_KAMPANYA"] = df["KAMPANYA_ADI"].notna() & (df["KAMPANYA_ADI"] != "")
        _WEEKLY_FULL_DF_CACHE[key] = df.copy()
        return df

    # ── SLIDE 2 (eski 3): Müşteri segmenti & tarihe göre müşteri listesi ─────
    @classmethod
    def build_segments_payload(cls, date_start: str, date_end: str) -> Dict:
        df = cls._load_full(date_start, date_end)
        if df.empty:
            return {"segments": [], "by_date": [], "customers_by_date": {},
                    "dates": [], "hhi": None, "row_count": 0}

        # Segment toplamları (donut için)
        seg_totals = (df.groupby("SEGMENT")["TRY_BALANCE"].sum() / cls.SCALE).round(2)
        seg_counts = df.groupby("SEGMENT")["CUST_ID"].nunique()
        segments = [
            {"segment": s, "volume_m": float(seg_totals.get(s, 0)),
             "customer_count": int(seg_counts.get(s, 0))}
            for s in seg_totals.sort_values(ascending=False).index
        ]

        # Tarih × segment stacked bar
        date_seg = (df.groupby(["ROLL_DATE", "SEGMENT"], observed=True)["TRY_BALANCE"]
                      .sum() / cls.SCALE).round(2)
        all_segments = list(seg_totals.sort_values(ascending=False).index)
        dates = [cls._fmt_date(pd.Timestamp(d)) for d in sorted(df["ROLL_DATE"].unique())]
        by_date = []
        for d in sorted(df["ROLL_DATE"].unique()):
            row = {"date": cls._fmt_date(pd.Timestamp(d))}
            for s in all_segments:
                row[s] = float(date_seg.get((pd.Timestamp(d), s), 0.0))
            by_date.append(row)

        # Tarih × (CUST_ID, CCY_CODE) bazında müşteri listesi
        # Sıralama: önce TRY, sonra diğer CCY'ler (alfabetik), her CCY içinde
        # bakiye büyüklüğüne göre azalan.
        df["_wr"] = df["INTRST_RT"] * df["TRY_BALANCE"]
        customers_by_date: Dict = {}
        for d in sorted(df["ROLL_DATE"].unique()):
            d_str = cls._fmt_date(pd.Timestamp(d))
            day_df = df[df["ROLL_DATE"] == d]
            total_day = float(day_df["TRY_BALANCE"].sum())

            agg = (day_df.groupby(["CUST_ID", "FULL_NM", "SEGMENT", "CCY_CODE"])
                         .agg(volume=("TRY_BALANCE", "sum"),
                              wr_sum=("_wr", "sum"),
                              ticket_count=("ACCT_ID", "count"),
                              avg_dtm=("DTM", "mean"))
                         .reset_index())
            agg["avg_rate"] = (agg["wr_sum"] / agg["volume"].clip(lower=1e-9)).round(2)
            # 100 mio TRY altındaki müşteriler listelenmez
            agg = agg[agg["volume"] >= 100_000_000.0]
            # TRY satırlarını diğerlerinin önüne al, sonra CCY alfabetik, sonra hacim azalan
            agg["_ccy_ord"] = (agg["CCY_CODE"] != "TRY").astype(int)
            agg = agg.sort_values(["_ccy_ord", "CCY_CODE", "volume"],
                                  ascending=[True, True, False])

            customers_by_date[d_str] = [{
                "cust_id":      int(r["CUST_ID"]),
                "full_nm":      _mask_full_nm(str(r["FULL_NM"])),
                "ccy_code":     str(r["CCY_CODE"]),
                "segment":      r["SEGMENT"],
                "volume_m":     round(float(r["volume"]) / cls.SCALE, 2),
                "ticket_count": int(r["ticket_count"]),
                "avg_rate":     round(float(r["avg_rate"]), 2),
                "avg_dtm":      round(float(r["avg_dtm"]), 0),
                "share_pct":    round(float(r["volume"]) / total_day * 100.0, 2)
                                if total_day else 0.0,
            } for _, r in agg.iterrows()]

        # HHI — dönem geneli müşteri konsantrasyonu
        total = float(df["TRY_BALANCE"].sum())
        cust_shares = df.groupby("CUST_ID")["TRY_BALANCE"].sum() / total if total else None
        hhi = float((cust_shares ** 2).sum() * 10000.0) if cust_shares is not None else 0.0

        return {
            "segments":          segments,
            "all_segments":      all_segments,
            "dates":             dates,
            "by_date":           by_date,
            "customers_by_date": customers_by_date,
            "hhi":               round(hhi, 1),
            "row_count":         int(len(df)),
        }

    # ── DRILL-DOWN: hücre tıklamasıyla detay ────────────────────────────────
    @classmethod
    def build_drilldown_payload(cls, date_start: str, date_end: str,
                                roll_date: str = "", aum_band: str = "",
                                currency: str = "", cust_tp: str = "") -> Dict:
        """Filtre parametreleri opsiyonel: hücre tipine göre tarih/band/CCY
        boş gelebilir (Total kolonu, Total/TRY/FX footer satırları).
        Boş filtre = o boyutta agregasyon (tüm tarihler / tüm bandlar / her CCY).
        """
        df = cls._load_full(date_start, date_end)
        empty_payload = {"customers": [], "rate_histogram": [], "dtm_histogram": [],
                         "segments": [], "kampanya_split": None, "row_count": 0,
                         "context": {"date": roll_date, "aum_band": aum_band,
                                     "currency": currency, "cust_tp": cust_tp}}
        if df.empty:
            return empty_payload

        mask = pd.Series(True, index=df.index)
        if roll_date:
            target_date = pd.to_datetime(roll_date, format="%d/%m/%Y")
            mask &= (df["ROLL_DATE"] == target_date)
        if aum_band:
            mask &= (df["AUM_BAND"] == aum_band)
        if currency:
            mask &= (df["CURRENCY"] == currency)
        if cust_tp:
            mask &= (df["CUST_TP"] == cust_tp)
        sub = df[mask]

        if sub.empty:
            return empty_payload

        # Müşteri listesi — CCY_CODE breakdown (TRY önce, sonra alfabetik, sonra
        # hacim azalan). Top 100.
        cust_agg = (sub.groupby(["CUST_ID", "FULL_NM", "SEGMENT", "CCY_CODE"])
                       .agg(volume=("TRY_BALANCE", "sum"),
                            avg_rate=("INTRST_RT", "mean"),
                            avg_dtm=("DTM", "mean"),
                            has_kampanya=("HAS_KAMPANYA", "any"))
                       .reset_index())
        cust_agg["_ccy_ord"] = (cust_agg["CCY_CODE"] != "TRY").astype(int)
        cust_agg = (cust_agg.sort_values(["_ccy_ord", "CCY_CODE", "volume"],
                                        ascending=[True, True, False])
                              .head(100))
        total_sub = float(sub["TRY_BALANCE"].sum())
        customers = [{
            "cust_id":  int(r["CUST_ID"]),
            "full_nm":  _mask_full_nm(str(r["FULL_NM"])),
            "ccy_code": str(r["CCY_CODE"]),
            "segment":  r["SEGMENT"],
            "volume_m": round(float(r["volume"]) / cls.SCALE, 2),
            "avg_rate": round(float(r["avg_rate"]), 2),
            "avg_dtm":  round(float(r["avg_dtm"]), 0),
            "kampanya": bool(r["has_kampanya"]),
            "share_pct": round(float(r["volume"]) / total_sub * 100.0, 2) if total_sub else 0,
        } for _, r in cust_agg.iterrows()]

        # Faiz histogramı (bakiye-ağırlıklı, 1pp bucket)
        if sub["INTRST_RT"].notna().any():
            r_min = float(sub["INTRST_RT"].min())
            r_max = float(sub["INTRST_RT"].max())
            edges = list(range(int(r_min), int(r_max) + 2))
            if len(edges) < 2:
                edges = [int(r_min), int(r_min) + 1]
            sub_c = sub.copy()
            sub_c["RATE_BUCKET"] = pd.cut(sub_c["INTRST_RT"], bins=edges,
                                          include_lowest=True, right=False)
            rh = (sub_c.groupby("RATE_BUCKET", observed=True)["TRY_BALANCE"].sum()
                  / cls.SCALE).round(2)
            rate_histogram = [{"bucket": f"{int(idx.left)}-{int(idx.right)}%",
                               "volume_m": float(v)}
                              for idx, v in rh.items()]
        else:
            rate_histogram = []

        # DTM histogram
        bucket_order = ["≤14", "15-32", "33-90", "91-180", "180+"]
        def _bucket(d):
            if d <= 14:  return "≤14"
            if d <= 32:  return "15-32"
            if d <= 90:  return "33-90"
            if d <= 180: return "91-180"
            return "180+"
        sub_d = sub.copy()
        sub_d["DTM_BUCKET"] = sub_d["DTM"].apply(_bucket)
        dh = (sub_d.groupby("DTM_BUCKET")["TRY_BALANCE"].sum()
              .reindex(bucket_order, fill_value=0) / cls.SCALE).round(2)
        dtm_histogram = [{"bucket": k, "volume_m": float(v)} for k, v in dh.items()]

        # Segment donut
        seg_grp = (sub.groupby("SEGMENT")["TRY_BALANCE"].sum() / cls.SCALE).round(2)
        segments = [{"segment": s, "volume_m": float(v)}
                    for s, v in seg_grp.sort_values(ascending=False).items()]

        # Kampanya split
        kmp = sub.groupby("HAS_KAMPANYA")["TRY_BALANCE"].sum() / cls.SCALE
        kampanya_split = {
            "with":    round(float(kmp.get(True,  0.0)), 2),
            "without": round(float(kmp.get(False, 0.0)), 2),
        }

        return {
            "context":         {"date": roll_date, "aum_band": aum_band,
                                "currency": currency, "cust_tp": cust_tp},
            "customers":       customers,
            "rate_histogram":  rate_histogram,
            "dtm_histogram":   dtm_histogram,
            "segments":        segments,
            "kampanya_split":  kampanya_split,
            "row_count":       int(len(sub)),
            "total_volume_m":  round(total_sub / cls.SCALE, 2),
        }


# ── app.py 3665-3682 ──
def _mask_full_nm(name: str) -> str:
    """KVKK koruması için müşteri isim maskesi: ilk harf + soyadın ilk harfi."""
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0][:1]}*** {parts[-1][:1]}***"
    return name[:1] + "***"


# Cache: (date_start, date_end) → payload. Process-lifetime; no eviction.
WEEKLY_CACHE: Dict[Tuple[str, str], Dict] = {}
WEEKLY_SEGMENTS_CACHE: Dict[Tuple[str, str], Dict] = {}
# DF-level cache — _load() ve _load_full() aynı tarih için aynı DataFrame'i
# birden fazla endpoint çağırırken SQL'i tekrarlamaz. df.copy() ile döner ki
# çağıran tarafın mutasyonu cache'i bozmasın.
_WEEKLY_AGG_DF_CACHE:  Dict[Tuple[str, str], pd.DataFrame] = {}
_WEEKLY_FULL_DF_CACHE: Dict[Tuple[str, str], pd.DataFrame] = {}




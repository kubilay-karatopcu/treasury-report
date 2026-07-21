"""Sector Comparison + BSC endpoint'leri.

Kaynak: NIM_calculation (bs_evolution5 @ c569ae3) — satır referansları blok
başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları uyarlandı
(bkz. mevduat_panel/tools/extract_a6.py).
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .engine.common import _convert_rate_series, _wavg
from .engine.outstanding import DailyDepositEngine, DepositDetailEngine
from .engine.np_agg import (
    aggregate_timeseries as np_aggregate_timeseries,
    apply_filters as np_apply_filters,
    compound_to_simple_pct as np_compound_to_simple_pct,
    load_np_data,
    simple_to_compound_pct_series as np_simple_to_compound_pct_series,
)
from .engine.sector_data import (
    TCMB_BUCKET_META,
    _days_to_tcmb_key,
    bddk_mix_weight_summary,
    load_bddk_amt_kirilim,
    load_bddk_maliyet,
    load_bddk_vade,
    load_tcmb_deposit_rates,
    sector_blotter,
    sector_deposit_rate_series,
    sector_mix_attribution_monthly,
    sector_outstanding_monthly,
    sector_outstanding_series,
    sector_wavg_tenor_by_month,
    tcmb_rate_table,
    vade_mix_comparison,
    vade_mix_presentation,
)
from .routes import mevduat_panel_bp

log = logging.getLogger("mevduat_panel")


# Kaynak: app.py 3688-3689
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

# ── app.py 4488-4620 ──
@mevduat_panel_bp.route("/api/sector_deposit_rates", methods=["GET"])
@login_required
def api_sector_deposit_rates():
    """Sektör mevduat faiz oranı zaman serisi (Sector Comparison sayfası — veri
    kontrol tablosu). BDDK_MALIYET'ten türetilir; bkz.
    engine/sector_data.py::sector_deposit_rate_series.

    Döner: {ok, rows:[{month, segment, ccy, bank_rate_pct, rate_pct, fg_month,
    fg_cum, fg_prev_cum, ort_bakiye, bakiye_end, bakiye_prev}]} — long form,
    segment/ccy/ay artan. fg_month = aylık faiz gideri (kümülatif fark; Ocak'ta
    = fg_cum).

    bank_rate_pct: BANKANIN o ayki bakiye-ağırlıklı ortalama SIMPLE mevduat
    faizi (%). Kaynak = aylık TRY_DEPOSIT_DETAIL (DepositDetailEngine) —
    Outstanding Cost Analysis > Monthly Averages ilk waterfall'ının Start/End
    Rate'iyle AYNI hesap: Σ(BALANCE·INTEREST_RATE)/Σ(BALANCE)·100. Banka verisi
    TRY olduğundan yalnız CCY_CODE='TP' satırlarına yazılır (YP → None); aynı
    ayın iki segment satırında aynı değer (banka referansı segmentten bağımsız).
    """
    try:
        from .engine.sector_data import _DEMAND_SUBPRODUCTS  # port
        demand_pct = request.args.get("demand_pct", type=float) or 0.0
        _p = max(0.0, demand_pct) / 100.0   # vadesiz etkisi (KGH/BTH seyreltme)

        df = sector_deposit_rate_series()

        # Banka aylık wavg simple oranı ve wavg tenoru: {Period(ay): değer}.
        # Banka MONTH (ay-başı) ile BDDK TARIH (ay-sonu) damgaları farklı →
        # (yıl, ay) period ile eşle.
        bank_by_month = {}
        bank_tenor_by_month = {}
        bank_bucket_tenor = {}   # {(Period, tcmb_kova_key): gün} — sektör tenoru için
        try:
            bdf, _ = DepositDetailEngine._load()
            g = bdf.copy()
            g["_wr"] = g["BALANCE"] * g["INTEREST_RATE"]   # faiz tutarı — demand'dan ETKİLENMEZ
            # VADESİZ (demand) etkisi: KGH/BTH bakiyesi paydada ×(1+p) büyür →
            # oran r/(1+p)'ye seyrelir; faiz tutarı (pay) sabit. Tenor ise
            # ORİJİNAL bakiyeyle kalır (karar (a)) → _bal_rate yalnız orandadır.
            if _p > 0 and "DIM_SUBPRODUCT" in g.columns:
                _is_dem = g["DIM_SUBPRODUCT"].astype(str).isin(_DEMAND_SUBPRODUCTS)
                g["_bal_rate"] = g["BALANCE"] * (1.0 + _p * _is_dem.astype(float))
            else:
                g["_bal_rate"] = g["BALANCE"]
            _per_g = pd.to_datetime(g["MONTH"]).dt.to_period("M")
            agg = g.groupby(_per_g)[["_bal_rate", "_wr"]].sum()
            for per, r in agg.iterrows():
                if r["_bal_rate"] > 0:
                    bank_by_month[per] = round(float(r["_wr"] / r["_bal_rate"]) * 100.0, 4)
            if "TENOR_RATE" in g.columns:
                tn = pd.to_numeric(g["TENOR_RATE"], errors="coerce")
                ok = tn.notna() & (tn > 0) & (g["BALANCE"] > 0)
                gt = g[ok].copy()
                gt["_wt"] = gt["BALANCE"] * tn[ok]
                tagg = gt.groupby(_per_g[ok])[["BALANCE", "_wt"]].sum()
                for per, r in tagg.iterrows():
                    if r["BALANCE"] > 0:
                        bank_tenor_by_month[per] = round(float(r["_wt"] / r["BALANCE"]), 1)
                # Ay × TCMB kovası banka wavg vadesi — DIM_BUCKET gün-aralığı
                # etiketinin alt sınırı BDDK/TCMB kova sınırlarına eşlenir
                # (_bank_vade_daily ile aynı yöntem).
                if "DIM_BUCKET" in gt.columns:
                    low = pd.to_numeric(gt["DIM_BUCKET"].astype(str).str.strip()
                                        .str.extract(r"^(\d+)", expand=False), errors="coerce")
                    gt2 = gt[low.notna()].copy()
                    gt2["_key"] = low[low.notna()].astype(int).map(_days_to_tcmb_key)
                    bagg = gt2.groupby([_per_g[gt2.index], "_key"])[["BALANCE", "_wt"]].sum()
                    for (per, key), r in bagg.iterrows():
                        if r["BALANCE"] > 0:
                            bank_bucket_tenor[(per, key)] = round(float(r["_wt"] / r["BALANCE"]), 1)
        except Exception as be:
            log.info(f"[sector_deposit_rates] banka aylık oran hesaplanamadı: {be}")

        # Sektör aylık wavg tenoru: BDDK_VADE kova payları × BANKANIN o ay o
        # kovadaki wavg vadesi (kullanıcı konvansiyonu; eksik kova → orta nokta).
        try:
            sector_tenor = sector_wavg_tenor_by_month(bank_bucket_tenor)
        except Exception as te:
            sector_tenor = {}
            log.info(f"[sector_deposit_rates] sektör tenoru hesaplanamadı: {te}")

        def _f(v):
            return None if pd.isna(v) else float(v)

        def _conv(pct, tenor_gun, mode):
            """Simple yıllık % → O/N eşleniği | yıllık bileşik %. Formüller
            _convert_rate_series ile birebir (act/365)."""
            if pct is None or tenor_gun is None or tenor_gun <= 0:
                return None
            s = pct / 100.0
            t = float(tenor_gun)
            base = 1.0 + s * t / 365.0
            if base <= 0:
                return None
            if mode == "compound":
                return round((base ** (365.0 / t) - 1.0) * 100.0, 4)
            return round((base ** (1.0 / t) - 1.0) * 365.0 * 100.0, 4)

        rows = []
        for _, r in df.iterrows():
            _per = pd.Timestamp(r["MONTH"]).to_period("M")
            _is_tp = str(r["CCY_CODE"]) == "TP"
            _bank = bank_by_month.get(_per) if _is_tp else None
            _btn  = bank_tenor_by_month.get(_per) if _is_tp else None
            _stn  = sector_tenor.get((str(r["SEGMENT"]), _per)) if _is_tp else None
            _rate = _f(r["RATE_PCT"])
            rows.append({
                "month":           pd.Timestamp(r["MONTH"]).strftime("%Y-%m-%d"),
                "segment":         r["SEGMENT"],
                "ccy":             r["CCY_CODE"],
                "bank_rate_pct":   _bank,
                "bank_tenor_gun":  _btn,
                "bank_rate_on_pct":   _conv(_bank, _btn, "on"),
                "bank_rate_comp_pct": _conv(_bank, _btn, "compound"),
                "sektor_tenor_gun":   _stn,
                "rate_on_pct":        (_conv(_rate, _stn, "on") if _is_tp else None),
                "rate_comp_pct":      (_conv(_rate, _stn, "compound") if _is_tp else None),
                "rate_pct":        _rate,
                "rate_rees_pct":   _f(r["RATE_REES_PCT"]),
                "fg_month":        _f(r["FG_MONTH"]),
                "fg_cum":          _f(r["FG_CUM"]),
                "fg_prev_cum":     _f(r["FG_PREV_CUM"]),
                "ort_bakiye":      _f(r["ORT_BAKIYE"]),
                "ort_bakiye_rees": _f(r["ORT_BAKIYE_REES"]),
                "bakiye_end":      _f(r["BAKIYE_END"]),
                "bakiye_prev":     _f(r["BAKIYE_PREV"]),
            })
        return _json_response({"ok": True, "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4621-4647 ──
@mevduat_panel_bp.route("/api/tcmb_rate_table", methods=["GET"])
@login_required
def api_tcmb_rate_table():
    """TCMB mevduat faizleri — vade-sütunlu tablo (Sector Comparison ikinci tablo).
    Ham veriden yalnız reshape; bkz. engine/sector_data.py::tcmb_rate_table.

    Döner: {ok, buckets:[{key,label}], rows:[{date, cur, <key>:faiz, ...}]}.
    """
    try:
        df = tcmb_rate_table()
        keys = [b["key"] for b in TCMB_BUCKET_META]
        rows = []
        for _, r in df.iterrows():
            row = {
                "date": pd.Timestamp(r["TCMB_DATE"]).strftime("%Y-%m-%d"),
                "cur":  str(r["CUR"]),
            }
            for k in keys:
                v = r.get(k)
                row[k] = None if pd.isna(v) else float(v)
            rows.append(row)
        return _json_response({"ok": True, "buckets": TCMB_BUCKET_META, "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4648-4678 ──
@mevduat_panel_bp.route("/api/sector_blotter", methods=["GET"])
@login_required
def api_sector_blotter():
    """Sektör blotter — banka verisi TCMB kovalarına gruplanmış + TCMB eşleşmeli.
    Bkz. engine/sector_data.py::sector_blotter (kaynak birleşimi, compound
    dönüşüm, kova haritası ve haftalık TCMB asof eşleşmesi orada).

    Döner: {ok, dq_note, rows:[{val_dt, vade_bucket, bakiye, wavg_dtm,
    wavg_comp_pct, tcmb_date, tcmb_rate_pct}]}.
    """
    try:
        b = sector_blotter()
        df = b["df"]
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "val_dt":        pd.Timestamp(r["VAL_DT"]).strftime("%Y-%m-%d"),
                "vade_bucket":   r["VADE_BUCKET"],
                "bakiye":        None if pd.isna(r["BAKIYE"]) else float(r["BAKIYE"]),
                "wavg_dtm":      None if pd.isna(r["WAVG_DTM"]) else float(r["WAVG_DTM"]),
                "wavg_comp_pct": None if pd.isna(r["WAVG_COMP_PCT"]) else float(r["WAVG_COMP_PCT"]),
                "tcmb_date":     (None if pd.isna(r["TCMB_DATE"])
                                  else pd.Timestamp(r["TCMB_DATE"]).strftime("%Y-%m-%d")),
                "tcmb_rate_pct": None if pd.isna(r["TCMB_RATE_PCT"]) else float(r["TCMB_RATE_PCT"]),
            })
        return _json_response({"ok": True, "dq_note": b["dq_note"], "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4679-4727 ──
@mevduat_panel_bp.route("/api/sector_outstanding", methods=["GET"])
@login_required
def api_sector_outstanding():
    """Gün-gün outstanding: toplam bakiye + banka compound + TCMB sentetik oran.
    Detay (VAL_DT × vade) blotter'dan üretilir; seri TCMB'nin son gözleminde
    kesilir. Bkz. engine/sector_data.py::sector_outstanding_series.

    Döner: {ok, dq_note, rows:[{tarih, bakiye, wavg_tenor, bank_comp_pct,
    tcmb_pct, spread_comp_pct, bank_simple_pct, tcmb_simple_pct,
    spread_simple_pct}]}. Simple dönüşümleri günlük wavg tenor iledir.
    """
    try:
        o = sector_outstanding_series()
        df = o["df"]

        def _f(v):
            return None if pd.isna(v) else float(v)

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "tarih":             pd.Timestamp(r["TARIH"]).strftime("%Y-%m-%d"),
                "bakiye":            _f(r["BAKIYE"]),
                "wavg_tenor":        _f(r["WAVG_TENOR"]),
                "bank_comp_pct":     _f(r["BANK_COMP_PCT"]),
                "tcmb_pct":          _f(r["TCMB_PCT"]),
                "spread_comp_pct":   _f(r["SPREAD_COMP_PCT"]),
                "bank_simple_pct":   _f(r["BANK_SIMPLE_PCT"]),
                "tcmb_simple_pct":   _f(r["TCMB_SIMPLE_PCT"]),
                "spread_simple_pct": _f(r["SPREAD_SIMPLE_PCT"]),
                "tcmb_bddk_pct":        _f(r["TCMB_BDDK_PCT"]),
                "tcmb_bddk_simple_pct": _f(r["TCMB_BDDK_SIMPLE_PCT"]),
                "bddk_w_ay":            (None if pd.isna(r["BDDK_W_AY"]) else r["BDDK_W_AY"]),
                "bddk_w_kapsam":        _f(r["BDDK_W_KAPSAM"]),
                "spread_bank_bddk_pct": _f(r["SPREAD_BANK_BDDK_PCT"]),
                "mix_simple_pct":       _f(r["MIX_SIMPLE_PCT"]),
                "bddk_tenor":              _f(r["BDDK_TENOR"]),
                "bank_on_pct":             _f(r["BANK_ON_PCT"]),
                "tcmb_on_pct":             _f(r["TCMB_ON_PCT"]),
                "tcmb_bddk_on_pct":        _f(r["TCMB_BDDK_ON_PCT"]),
                "spread_on_pct":           _f(r["SPREAD_ON_PCT"]),
                "spread_bank_bddk_on_pct": _f(r["SPREAD_BANK_BDDK_ON_PCT"]),
            })
        return _json_response({"ok": True, "dq_note": o["dq_note"], "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4728-4774 ──
@mevduat_panel_bp.route("/api/sector_outstanding_monthly", methods=["GET"])
@login_required
def api_sector_outstanding_monthly():
    """Gün-gün outstanding serisinin aylık (bakiye-ağırlıklı) özeti.
    Bkz. engine/sector_data.py::sector_outstanding_monthly.

    Döner: {ok, dq_note, rows:[{ay, bakiye, wavg_tenor, bank_comp_pct, tcmb_pct,
    spread_comp_pct, bank_simple_pct, tcmb_simple_pct, spread_simple_pct}]}.
    """
    try:
        o = sector_outstanding_monthly()
        df = o["df"]

        def _f(v):
            return None if pd.isna(v) else float(v)

        rows = []
        for _, r in df.iterrows():
            rows.append({
                "ay":                pd.Timestamp(r["AY"]).strftime("%Y-%m"),
                "bakiye":            _f(r["BAKIYE"]),
                "wavg_tenor":        _f(r["WAVG_TENOR"]),
                "bank_comp_pct":     _f(r["BANK_COMP_PCT"]),
                "tcmb_pct":          _f(r["TCMB_PCT"]),
                "spread_comp_pct":   _f(r["SPREAD_COMP_PCT"]),
                "bank_simple_pct":   _f(r["BANK_SIMPLE_PCT"]),
                "tcmb_simple_pct":   _f(r["TCMB_SIMPLE_PCT"]),
                "spread_simple_pct": _f(r["SPREAD_SIMPLE_PCT"]),
                "tcmb_bddk_pct":        _f(r["TCMB_BDDK_PCT"]),
                "tcmb_bddk_simple_pct": _f(r["TCMB_BDDK_SIMPLE_PCT"]),
                "bddk_w_ay":            (None if pd.isna(r["BDDK_W_AY"]) else r["BDDK_W_AY"]),
                "bddk_w_kapsam":        _f(r["BDDK_W_KAPSAM"]),
                "spread_bank_bddk_pct": _f(r["SPREAD_BANK_BDDK_PCT"]),
                "mix_simple_pct":       _f(r["MIX_SIMPLE_PCT"]),
                "bddk_tenor":              _f(r["BDDK_TENOR"]),
                "bank_on_pct":             _f(r["BANK_ON_PCT"]),
                "tcmb_on_pct":             _f(r["TCMB_ON_PCT"]),
                "tcmb_bddk_on_pct":        _f(r["TCMB_BDDK_ON_PCT"]),
                "spread_on_pct":           _f(r["SPREAD_ON_PCT"]),
                "spread_bank_bddk_on_pct": _f(r["SPREAD_BANK_BDDK_ON_PCT"]),
            })
        return _json_response({"ok": True, "dq_note": o["dq_note"], "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4775-4805 ──
@mevduat_panel_bp.route("/api/sector_mix_attribution", methods=["GET"])
@login_required
def api_sector_mix_attribution():
    """Mix etkisinin aylık kova ayrıştırması (Sector Comparison grafik+tablo).
    Bkz. engine/sector_data.py::sector_mix_attribution_monthly. KATKI compound
    bps; Σ_kova katkı(ay) = aylık [TCMB(banka mix) − TCMB(BDDK mix)] × 100.
    """
    try:
        df = sector_mix_attribution_monthly()
        _lbl = {b["key"]: b["label"] for b in TCMB_BUCKET_META}
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "ay":        pd.Timestamp(r["AY"]).strftime("%Y-%m"),
                "kova_key":  r["KOVA"],
                "kova":      _lbl.get(r["KOVA"], r["KOVA"]),
                "w_bank":    None if pd.isna(r["W_BANK"]) else float(r["W_BANK"]),
                "w_sektor":  None if pd.isna(r["W_SEKTOR"]) else float(r["W_SEKTOR"]),
                "dw_pp":     (None if (pd.isna(r["W_BANK"]) or pd.isna(r["W_SEKTOR"]))
                              else round((float(r["W_BANK"]) - float(r["W_SEKTOR"])) * 100.0, 2)),
                "r_b":       None if pd.isna(r["R_B"]) else float(r["R_B"]),
                "r_b_s":     None if pd.isna(r["R_B_S"]) else float(r["R_B_S"]),
                "katki_bps": None if pd.isna(r["KATKI_BPS"]) else float(r["KATKI_BPS"]),
                "katki_s_bps": None if pd.isna(r["KATKI_S_BPS"]) else float(r["KATKI_S_BPS"]),
            })
        return _json_response({"ok": True, "rows": rows})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4806-4827 ──
@mevduat_panel_bp.route("/api/sector_vade_mix", methods=["GET"])
@login_required
def api_sector_vade_mix():
    """Banka vs Sektör vade dağılımı (Sector Comparison alt grafik).

    Parametreler: date (BDDK ay-sonu, boşsa en son) + mode (monthly|daily).
    Bkz. engine/sector_data.py::vade_mix_comparison — mode banka bakiyesinin
    (günlük ortalama vs en yakın gün) ve sektör bakiyesinin (iki ay-sonu
    ortalaması vs olduğu gibi) nasıl hesaplanacağını belirler; TARİH her iki
    modda da BDDK ay-sonlarından seçilir. dates listesi dropdown'ı doldurur.
    """
    date_arg = request.args.get("date", "").strip() or None
    mode = request.args.get("mode", "monthly").strip().lower()
    if mode not in ("monthly", "daily"):
        mode = "monthly"
    try:
        return _json_response({"ok": True, **vade_mix_comparison(date_arg, mode)})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 4828-4848 ──
@mevduat_panel_bp.route("/api/sector_vade_mix_pres", methods=["GET"])
@login_required
def api_sector_vade_mix_pres():
    """BSC Presentation Slide 4 — vade dağılımı (Sektör + Banka×2 tarih).

    Parametreler: date_end (prezentasyonun Date(End)'i) + mode (monthly|daily).
    Bkz. engine/sector_data.py::vade_mix_presentation — sektör tarihi Date(End)'e
    en yakın BDDK ay-sonu; banka hem o tarihe hem Date(End)'e karşılık gelen
    iki ayrı seriyle döner (aynı kaynağa düşerse tek seri + not).
    """
    date_end = request.args.get("date_end", "").strip() or None
    mode = request.args.get("mode", "monthly").strip().lower()
    if mode not in ("monthly", "daily"):
        mode = "monthly"
    try:
        return _json_response({"ok": True, **vade_mix_presentation(date_end, mode)})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 6751-6886 ──
@mevduat_panel_bp.route("/api/bsc_np_rate_series", methods=["GET"])
@login_required
def api_bsc_np_rate_series():
    """BSC Presentation Slide 4 alt grafiği — NP faiz serileri + TCMB 1-3M + TLREF.

    Sabitler: TRY, freq=W (son NP gününde biten rolling haftalar), VADE(DTM) =
    32-45 + 46-91 (TENOR_GRP: 03_32-35, 04_36-45, 05_46-60, 06_61-91). `decomp`
    boyutuna göre banka NP wavg SIMPLE faiz çizgileri döner. Ek olarak:
      tenor[]     : filtrelenmiş kitabın haftalık wavg vadesi (gün) — Rate Type
                    dönüşümlerinin TEK vade kaynağı (client-side çevrim).
      tcmb_comp[] : TCMB '3 Aya Kadar Vadeli' (1-3 M) TRY oranı, COMPOUND —
                    hafta sonuna asof (son gözlem ≤ tarih, ≤13 gün).
      tlref_on[]  : BIST TLREF O/N oranı — hafta penceresi (t-6..t] ortalaması,
                    pencerede gün yoksa asof (son gözlem ≤ tarih, ≤7 gün).
    """
    try:
        from .engine.sector_data import load_bist_tlref  # port (tcmb yukarida)

        df = load_np_data()
        decomp = request.args.get("decomp", "SUB_SEGMENT")
        if decomp not in {"AUM_BAND", "TENOR_GRP", "SUB_SEGMENT", "CUST_TP", "RELATED_PC"}:
            decomp = "SUB_SEGMENT"
        TENOR_FIX = ["03_32-35", "04_36-45", "05_46-60", "06_61-91"]
        # AUM filtresi + gruplama (Slide 4 chip'i): filter_AUM_BAND=v1|v2 ve
        # merges={"AUM_BAND":[{"name","members"}]} — gruplar decomp=AUM_BAND
        # iken seri adına relabel edilir (wavg backend'de doğru toplanır).
        _aum_raw = request.args.get("filter_AUM_BAND")
        aum_filter = [x for x in _aum_raw.split("|") if x] if _aum_raw else None
        aum_merges = []
        _mraw = request.args.get("merges", "").strip()
        if _mraw:
            try:
                aum_merges = (json.loads(_mraw) or {}).get("AUM_BAND", []) or []
            except Exception:
                aum_merges = []
        # AUM evreni (chip seçenekleri) — AUM filtresi UYGULANMADAN önce.
        df_u = np_apply_filters(df, ccy=["TRY"], tenor_grp=TENOR_FIX)
        _AUM_ORDER = ["0-1M", "1M-2M", "2M-5M", "5M-10M", "10M-25M", "25M-50M",
                      "50M-100M", "100M-200M", "200M-500M", "500M-1B", "1B+"]
        _present = set(df_u["AUM_BAND"].astype(str).unique().tolist())
        aum_values = [b for b in _AUM_ORDER if b in _present]                    + sorted(_present - set(_AUM_ORDER))
        df_f = np_apply_filters(df, ccy=["TRY"], tenor_grp=TENOR_FIX,
                                aum_band=aum_filter)
        if df_f.empty:
            return _json_response({"ok": True, "decomp": decomp, "dates": [], "bands": [],
                                   "rates": {}, "tenor": [], "tcmb_comp": [], "tlref_on": [],
                                   "aum_values": aum_values})
        if decomp == "AUM_BAND" and aum_merges:
            _m2g = {}
            for g in aum_merges:
                for m in (g.get("members") or []):
                    _m2g[str(m)] = str(g.get("name"))
            df_f = df_f.copy()
            df_f["AUM_BAND"] = df_f["AUM_BAND"].astype(str).map(
                lambda v: _m2g.get(v, v))
        anchor = pd.Timestamp(df_f["DAT"].max()).normalize()

        tot = np_aggregate_timeseries(df_f, group_by=[], freq="W", week_anchor=anchor)
        dates = [pd.Timestamp(d) for d in tot["DATE"].tolist()]

        # Haftalık wavg vade: aynı binleme, NP_FAIZ kolonuna TENOR_DAYS yazarak
        # (aggregate_timeseries wavg'ı NP_HACIM ağırlığıyla NP_FAIZ üzerinden alır).
        df_t = df_f.copy()
        df_t["NP_FAIZ"] = df_t["TENOR_DAYS"]
        tot_t = np_aggregate_timeseries(df_t, group_by=[], freq="W", week_anchor=anchor)
        ten_by = {pd.Timestamp(r["DATE"]): float(r["NP_FAIZ"]) for _, r in tot_t.iterrows()
                  if pd.notna(r["NP_FAIZ"])}
        tenor = [round(ten_by.get(d), 1) if ten_by.get(d) is not None else None for d in dates]

        by = np_aggregate_timeseries(df_f, group_by=[decomp], freq="W", week_anchor=anchor)
        by[decomp] = by[decomp].astype(str)
        bands = sorted(set(by[decomp].unique().tolist()))
        if decomp == "AUM_BAND":
            def _aum_key(b):
                if b in _AUM_ORDER:
                    return (_AUM_ORDER.index(b), b)
                # merge grubu → üyelerinin en küçük sırası
                for g in aum_merges:
                    if str(g.get("name")) == b:
                        idxs = [_AUM_ORDER.index(str(m)) for m in (g.get("members") or [])
                                if str(m) in _AUM_ORDER]
                        if idxs:
                            return (min(idxs), b)
                return (len(_AUM_ORDER), b)
            bands = sorted(bands, key=_aum_key)
        rates = {}
        for b in bands:
            sub = by[by[decomp] == b].set_index("DATE")
            rates[b] = [round(float(sub.loc[d, "NP_FAIZ"]), 4) if d in sub.index else None
                        for d in dates]

        # TCMB 1-3 M (compound): CUR=TRY, TIP '3 Aya Kadar Vadeli' prefix'i.
        tc = load_tcmb_deposit_rates()
        tc = tc[(tc["CUR"].astype(str) == "TRY")
                & tc["TIP"].astype(str).str.startswith("3 Aya Kadar Vadeli")]
        tc = tc.sort_values("TCMB_DATE")
        tc_d = pd.to_datetime(tc["TCMB_DATE"]).tolist()
        tc_v = pd.to_numeric(tc["ORT_FAIZ"], errors="coerce").tolist()
        def _asof(d, ds, vs, tol_days):
            best = None
            for x, v in zip(ds, vs):
                if x <= d and (d - x).days <= tol_days and v is not None and not pd.isna(v):
                    best = v
                elif x > d:
                    break
            return best
        tcmb_comp = [(round(v, 4) if (v := _asof(d, tc_d, tc_v, 13)) is not None else None)
                     for d in dates]

        # TLREF (O/N): hafta penceresi ortalaması; boşsa asof ≤7 gün.
        tl = load_bist_tlref()
        tl_d = pd.to_datetime(tl["ASOFDATE"]).tolist()
        tl_v = pd.to_numeric(tl["RATE"], errors="coerce").tolist()
        tlref_on = []
        for d in dates:
            lo = d - pd.Timedelta(days=6)
            win = [v for x, v in zip(tl_d, tl_v)
                   if lo <= x <= d and v is not None and not pd.isna(v)]
            if win:
                tlref_on.append(round(sum(win) / len(win), 4))
            else:
                v = _asof(d, tl_d, tl_v, 7)
                tlref_on.append(round(v, 4) if v is not None else None)

        return _json_response({
            "ok": True, "decomp": decomp,
            "dates": [str(d.date()) for d in dates],
            "bands": bands, "rates": rates, "tenor": tenor,
            "tcmb_comp": tcmb_comp, "tlref_on": tlref_on,
            "aum_values": aum_values,
        })
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)



# ── app.py 6887-6981 ──
@mevduat_panel_bp.route("/api/bsc_np_monthly_table", methods=["GET"])
@login_required
def api_bsc_np_monthly_table():
    """Slide 4 üst tabloları — aylık Banka vs TCMB sektör oranları (2 kova).

    Kovalar:
      • 0-1 M — HARMANLI outstanding: O/N/kasa stok (daily_deposit VADE_BUCKET
        '1-3') + 4-31 gün NP akımı (outstanding, living-window). Banka oranı
        engine.sector_data.harmonized_bucket_0_1_monthly'den gelir; KGH/BTH'ye
        opsiyonel VADESİZ (demand) etkisi uygulanır (demand_pct paramı).
      • 1-3 M — saf NP flow (TENOR_GRP 03_32-35..06_61-91): ayın NP hacim-
        ağırlıklı SIMPLE oranı + wavg vadesi; kovanın SON TCMB gözleminde
        kesilir (kısmi-ay tutarlılığı). Demand etkisi YOK (KGH/BTH bu kovada yok).
    Her iki kovada Sektör = TCMB haftalık COMPOUND gözlemlerinin ay ortalaması.
    Çevrim ve spread client-side (NP grafiğiyle aynı yardımcılar/uzay).

    Query: demand_pct (float, %; 0/boş = etkisiz) — yalnız 0-1 M O/N KGH/BTH'yi
    seyreltir.
    Döner: {ok, m0_1: {rows, tcmb_last}, m1_3: {rows, tcmb_last}} — rows
    [{month, bank_simple, bank_tenor, bank_upto, tcmb_comp}] ay azalan;
    bank_upto = o ay banka verisine giren son iş/akış günü (cap denetimi).
    """
    try:
        from .engine.sector_data import (  # port
                                         harmonized_bucket_0_1_monthly)

        demand_pct = request.args.get("demand_pct", type=float) or 0.0
        df = load_np_data()
        tc = load_tcmb_deposit_rates()
        tc = tc[tc["CUR"].astype(str) == "TRY"].copy()
        tc["_per"] = pd.to_datetime(tc["TCMB_DATE"]).dt.to_period("M")
        SPEC = {
            "m0_1": (["01_1-3", "02_4-31"], "1 Aya Kadar Vadeli"),
            "m1_3": (["03_32-35", "04_36-45", "05_46-60", "06_61-91"], "3 Aya Kadar Vadeli"),
        }
        out = {}
        for key, (grps, tip) in SPEC.items():
            _t_bucket = tc[tc["TIP"].astype(str).str.startswith(tip)]
            _tc_last = (pd.to_datetime(_t_bucket["TCMB_DATE"]).max()
                        if not _t_bucket.empty else None)
            if key == "m0_1":
                # HARMANLI: O/N stok (KGH/BTH demand effect) + NP 4-31 outstanding.
                # Bank dict {Period: (simple, tenor, upto)} — helper zaten TCMB'nin
                # son gözleminde kesiyor (kısmi-ay tutarlılığı).
                bank = harmonized_bucket_0_1_monthly(demand_pct)
            else:
                # Saf NP flow — kovanın SON TCMB gözleminde kesilir.
                d = np_apply_filters(df, ccy=["TRY"], tenor_grp=grps)
                if not d.empty and _tc_last is not None:
                    d = d[pd.to_datetime(d["DAT"]) <= _tc_last]
                bank = {}
                if not d.empty:
                    g = d.copy()
                    g["_dat"] = pd.to_datetime(g["DAT"])
                    g["_per"] = g["_dat"].dt.to_period("M")
                    g["_wr"] = g["NP_HACIM"] * g["NP_FAIZ"]
                    g["_wt"] = g["NP_HACIM"] * g["TENOR_DAYS"]
                    agg = g.groupby("_per")[["NP_HACIM", "_wr", "_wt"]].sum()
                    last_dat = g.groupby("_per")["_dat"].max()
                    for per, r in agg.iterrows():
                        if r["NP_HACIM"] > 0:
                            bank[per] = (round(float(r["_wr"] / r["NP_HACIM"]), 4),
                                         round(float(r["_wt"] / r["NP_HACIM"]), 1),
                                         last_dat.loc[per].strftime("%Y-%m-%d"))
            t = tc[tc["TIP"].astype(str).str.startswith(tip)]
            sec = {}
            if not t.empty:
                sagg = t.groupby("_per")["ORT_FAIZ"].mean()
                for per, v in sagg.items():
                    if not pd.isna(v):
                        sec[per] = round(float(v), 4)
            months = sorted(set(bank) | set(sec), reverse=True)
            rows = []
            for per in months:
                b = bank.get(per)
                rows.append({
                    "month":       str(per),
                    "bank_simple": (b[0] if b else None),
                    "bank_tenor":  (b[1] if b else None),
                    # Şeffaflık: o ay banka ortalamasına giren SON akış günü —
                    # kesme (cap) prod'da denetlenebilsin diye satıra yazılır.
                    "bank_upto":   (b[2] if b else None),
                    "tcmb_comp":   sec.get(per),
                })
            out[key] = {
                "rows": rows,
                "tcmb_last": (_tc_last.strftime("%Y-%m-%d")
                              if _tc_last is not None else None),
            }
        return _json_response({"ok": True, "m0_1": out["m0_1"], "m1_3": out["m1_3"]})
    except Exception as e:
        import traceback
        return _json_response({"ok": False, "error": str(e),
                               "traceback": traceback.format_exc()}, status=500)




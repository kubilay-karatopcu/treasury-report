"""Süreç metrik sağlayıcı — Süreç Düzenlileştirme W4b.

Uzman yorumu / "…'ye sor" için KOMPAKT, önceden-agregelenmiş KPI özeti üretir.
Ham veri LLM'e gitmez; buradan çıkan 4-6 satırlık {k, v, delta, tone} listesi
gider (expert kartı rail'iyle aynı şekil).

Kaynak: ``engine/outstanding_daily.load_outstanding_daily()`` — şeması modülde
belgeli (DAT, CHANNEL, AUM_COMMON, TENOR_COMMON, OS_BAKIYE ₺mio, OS_FAIZ %).
Prewarm bu engine'i açılışta ısıttığı için çağrı normalde RAM'den okur.

Sözleşme: her adım savunmacı — herhangi bir hata boş liste döndürür (yorum
sayısız, dökümantasyon-temelli kalır); asla exception sızdırmaz. Kayıt app.py'de
``app.config["PROCESS_METRICS_PROVIDER"] = metrics_summary`` ile yapılır —
prisma_home mevduat_panel'i import ETMEZ (izolasyon sözleşmesi korunur).
"""
from __future__ import annotations

import logging
from datetime import timedelta

log = logging.getLogger("mevduat_panel")


def _fmt_bal(mio: float) -> str:
    if mio >= 1e6:
        return f"₺{mio / 1e6:.2f}T"
    if mio >= 1e3:
        return f"₺{mio / 1e3:.1f}B"
    return f"₺{mio:,.0f}M"


def metrics_summary() -> list[dict]:
    """[{k, v, delta, tone}, ...] — son gün stok fotoğrafı + ~7 gün delta."""
    try:
        from .engine.outstanding_daily import load_outstanding_daily

        df = load_outstanding_daily()
        if df is None or df.empty:
            return []
        last = df["DAT"].max()
        cur = df[df["DAT"] == last]
        bal = float(cur["OS_BAKIYE"].sum())
        wavg = float((cur["OS_BAKIYE"] * cur["OS_FAIZ"]).sum() / bal) if bal else 0.0

        # ~1 hafta önceki en yakın tarih (yoksa eldeki en eski gün).
        prev_dates = df.loc[df["DAT"] <= last - timedelta(days=6), "DAT"]
        out: list[dict] = [
            {"k": "Toplam Stok", "v": _fmt_bal(bal), "delta": "", "tone": ""},
            {"k": "WAvg Faiz", "v": f"%{wavg:.2f}", "delta": "", "tone": ""},
        ]
        if not prev_dates.empty:
            pd_ = prev_dates.max()
            prev = df[df["DAT"] == pd_]
            pbal = float(prev["OS_BAKIYE"].sum())
            pwavg = float((prev["OS_BAKIYE"] * prev["OS_FAIZ"]).sum() / pbal) if pbal else 0.0
            days = (last - pd_).days
            if pbal:
                dpct = (bal - pbal) / pbal * 100
                out[0]["delta"] = f"{dpct:+.1f}% / {days}g"
                out[0]["tone"] = "pos" if dpct >= 0 else "neg"
            dbps = (wavg - pwavg) * 100
            out[1]["delta"] = f"{dbps:+.0f} bps / {days}g"
            # Maliyet artışı banka için olumsuz tondur.
            out[1]["tone"] = "neg" if dbps > 0 else "pos"

        # En büyük segment (kanal) payı — yoğunlaşma sinyali.
        seg = cur.groupby("CHANNEL")["OS_BAKIYE"].sum().sort_values(ascending=False)
        if len(seg) and bal:
            out.append({
                "k": f"En Büyük Segment ({seg.index[0]})",
                "v": f"%{seg.iloc[0] / bal * 100:.1f}",
                "delta": "", "tone": "",
            })
        out.append({"k": "Veri Tarihi", "v": str(last)[:10], "delta": "", "tone": ""})
        return out
    except Exception:
        log.exception("metrics_summary üretilemedi — yorum sayısız devam eder")
        return []

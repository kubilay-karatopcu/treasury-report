"""Blok digest sağlayıcıları — Süreç Düzenlileştirme W5a (piramit Aşama A).

Her dökümante bileşen bloğu için "bu plottan hangi ~10 sayı anlamlı" kararını
veren elle yazılmış kompakt özet fonksiyonları. Kontrat (plan §3.5 W5):

- ``fn() -> list[{k, v, delta?, tone?}]`` — ≤15 satır, sayılar ÖNCEDEN formatlı
  metin (LLM aynen aktarır, dönüştürmez).
- YALNIZ RAM'deki engine cache'lerinden okur: her fonksiyon önce ilgili modül
  cache'inin sıcak olduğunu kontrol eder, soğuksa [] döner — digest ASLA Oracle
  yüklemesi tetiklemez (prewarm/refresh ısıtır, biz okuruz).
- Her hata boş listeye düşer; asla exception sızdırmaz (metrics.py sözleşmesi).

Kayıt ``app.config["PROCESS_BLOCK_DIGESTS"] = build_digest_registry()`` ile
yapılır — prisma_home yalnız config'i okur, bu modülü import etmez (W4b
izolasyon sözleşmesi).

Formül notu: camon_wf Bennet ayrıştırması ve np_rvhm compound ağırlıklaması
kaynak motorlardaki formüllerin kompakt kopyasıdır (outstanding.py
build_waterfalls ~260-290, routes_np.py _agg_window). Kaynak formül değişirse
buradaki digest de gözden geçirilmeli (spec'teki bilinen bakım riski).
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

import pandas as pd

log = logging.getLogger("mevduat_panel")

Row = Dict[str, str]


def _row(k: str, v: str, delta: str = "", tone: str = "") -> Row:
    return {"k": k, "v": v, "delta": delta, "tone": tone}


def _fmt_m(mio: float) -> str:
    """₺M değeri okunur biçimle (binlik ayraçlı, tam sayı)."""
    return f"₺{mio:,.0f}M"


def _safe(fn: Callable[[], List[Row]]) -> List[Row]:
    try:
        return fn()[:15]
    except Exception:
        log.exception("blok digest'i üretilemedi: %s", getattr(fn, "__name__", "?"))
        return []


# ── Ortak cache okuyucular (soğuksa None — SQL tetiklenmez) ─────────────────

def _dd_snapshot() -> Optional[tuple]:
    """(df, d0, d1) — TRY_DEPOSIT_DETAIL aylık cache'i sıcaksa son iki ay."""
    from .engine import outstanding as eng

    if "df" not in eng._DD_CACHE:
        return None
    dates = eng._DD_CACHE.get("dates") or []
    if len(dates) < 2:
        return None
    return eng._DD_CACHE["df"], dates[-2], dates[-1]


def _weekly_latest(cache: dict) -> Optional[pd.DataFrame]:
    """Weekly cache'lerinden en güncel pencerenin df'i (DD/MM/YYYY anahtarlı)."""
    if not cache:
        return None
    def _end(key):  # (ds, de) → de'yi sıralanabilir yap
        try:
            return pd.to_datetime(key[1], format="%d/%m/%Y")
        except Exception:
            return pd.Timestamp.min
    return cache[max(cache.keys(), key=_end)]


# ── mevduat.maliyet ─────────────────────────────────────────────────────────

def digest_camon_wf() -> List[Row]:
    """Waterfall: başlangıç/bitiş wavg + Bennet mix/fiyat + top sürükleyiciler."""
    from .engine import outstanding as eng
    from .engine.common import _wavg

    snap = _dd_snapshot()
    if snap is None:
        return []
    df, d0, d1 = snap
    E = eng.DepositDetailEngine
    df0 = df[df["MONTH"] == pd.to_datetime(d0)]
    df1 = df[df["MONTH"] == pd.to_datetime(d1)]
    if df0.empty or df1.empty:
        return []
    g0 = E._group_by_dims(df0.copy(), list(E.DIMENSIONS))
    g1 = E._group_by_dims(df1.copy(), list(E.DIMENSIONS))
    r0 = _wavg(g0["INTEREST_RATE"], g0["BALANCE"])
    r1 = _wavg(g1["INTEREST_RATE"], g1["BALANCE"])
    b0t, b1t = float(g0["BALANCE"].sum()), float(g1["BALANCE"].sum())
    m = (g0[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
         .rename(columns={"BALANCE": "b0", "INTEREST_RATE": "r0"})
         .merge(g1[["PRODUCT", "BALANCE", "INTEREST_RATE"]]
                .rename(columns={"BALANCE": "b1", "INTEREST_RATE": "r1"}),
                on="PRODUCT", how="outer").fillna(0.0))
    m["w0"] = m["b0"] / b0t if b0t > 0 else 0.0
    m["w1"] = m["b1"] / b1t if b1t > 0 else 0.0
    m["dw"] = m["w1"] - m["w0"]
    m["r_avg"] = (m["r0"] + m["r1"]) / 2.0
    m["mix_bps"] = m["dw"] * (m["r_avg"] - (r0 + r1) / 2.0) * 10000.0
    m["price_bps"] = (m["w0"] + m["w1"]) / 2.0 * (m["r1"] - m["r0"]) * 10000.0
    mix_t, price_t = float(m["mix_bps"].sum()), float(m["price_bps"].sum())
    rows = [
        _row("Dönem", f"{d0} → {d1}"),
        _row("Başlangıç WAvg", f"{r0 * 10000:,.0f} bps"),
        _row("Bitiş WAvg", f"{r1 * 10000:,.0f} bps",
             f"{(r1 - r0) * 10000:+,.0f} bps",
             "neg" if r1 > r0 else "pos"),
        _row("Mix etkisi", f"{mix_t:+,.0f} bps"),
        _row("Fiyat etkisi", f"{price_t:+,.0f} bps"),
    ]
    for col, tag in (("price_bps", "Fiyat sürükleyicisi"),
                     ("mix_bps", "Mix sürükleyicisi")):
        top = m.reindex(m[col].abs().sort_values(ascending=False).index).head(2)
        for _, r in top.iterrows():
            rows.append(_row(f"{tag}: {r['PRODUCT']}", f"{r[col]:+,.1f} bps"))
    return rows


def digest_camon_bubble() -> List[Row]:
    """Bubble: en büyük kümeler bakiye + faiz konumuyla."""
    from .engine import outstanding as eng
    from .engine.common import _cost_bubble_source

    snap = _dd_snapshot()
    if snap is None:
        return []
    df, d0, d1 = snap
    E = eng.DepositDetailEngine
    dim_map = {"PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT",
               "CUSTOMER_TYPE": "DIM_CUSTOMER", "AUM": "DIM_AUM",
               "SEGMENT": "DIM_SEGMENT"}
    df0 = df[df["MONTH"] == pd.to_datetime(d0)].copy()
    df1 = df[df["MONTH"] == pd.to_datetime(d1)].copy()
    m_bub, _ = _cost_bubble_source(df0, df1, list(E.DIMENSIONS), dim_map)
    # İnce hücreleri ürün etiketine geri topla (ekran varsayılanıyla hizalı).
    m_bub = m_bub.copy()
    m_bub["_wr1"] = m_bub["b1"] * m_bub["r1"]
    m_bub["_wr0"] = m_bub["b0"] * m_bub["r0"]
    g = m_bub.groupby("PRODUCT", dropna=False)[["b0", "b1", "_wr0", "_wr1"]].sum()
    g["r1"] = (g["_wr1"] / g["b1"]).where(g["b1"] > 0, 0.0)
    g["r0"] = (g["_wr0"] / g["b0"]).where(g["b0"] > 0, 0.0)
    g = g.sort_values("b1", ascending=False)
    rows = [_row("Dönem", f"{d0} → {d1}")]
    for name, r in g.head(5).iterrows():
        rows.append(_row(
            str(name), f"{_fmt_m(r['b1'] / 1e6)} @ %{r['r1'] * 100:.2f}",
            f"{(r['r1'] - r['r0']) * 10000:+,.0f} bps",
            "neg" if r["r1"] > r["r0"] else "pos"))
    big = g.head(8)
    if len(big):
        costly = big["r1"].idxmax()
        rows.append(_row("En pahalı büyük küme",
                         f"{costly} (%{big.loc[costly, 'r1'] * 100:.2f})"))
    return rows


def digest_camon_ratehm() -> List[Row]:
    """Faiz heatmap: |Δbps| en yüksek hücreler + toplam Δ."""
    from .engine.common import _rate_heatmap_seg_aum

    snap = _dd_snapshot()
    if snap is None:
        return []
    df, d0, d1 = snap
    cols = ["BALANCE", "INTEREST_RATE", "DIM_SEGMENT", "DIM_AUM"]
    if any(c not in df.columns for c in cols):
        return []
    hm = _rate_heatmap_seg_aum(
        df[df["MONTH"] == pd.to_datetime(d0)][cols].copy(),
        df[df["MONTH"] == pd.to_datetime(d1)][cols].copy())
    if not hm:
        return []
    cells = []
    for i, rname in enumerate(hm["rows"]):
        for j, cname in enumerate(hm["cols"]):
            dv = hm["delta_bps"][i][j]
            lv = hm["rate_t1_pct"][i][j]
            if dv is not None:
                cells.append((abs(dv), dv, lv, f"{rname} × {cname}"))
    cells.sort(reverse=True)
    rows = [_row("Dönem", f"{d0} → {d1}")]
    gt = hm.get("grand_total_delta_bps")
    if gt is not None:
        rows.append(_row("Toplam Δ", f"{gt:+,.0f} bps",
                         tone="neg" if gt > 0 else "pos"))
    for _, dv, lv, label in cells[:5]:
        lvl = f" (seviye %{lv:.2f})" if lv is not None else ""
        rows.append(_row(label, f"{dv:+,.0f} bps{lvl}",
                         tone="neg" if dv > 0 else "pos"))
    return rows


# ── mevduat.bakiye ──────────────────────────────────────────────────────────

def _balance_payload() -> Optional[dict]:
    from .engine import outstanding as eng

    snap = _dd_snapshot()
    if snap is None:
        return None
    _, d0, d1 = snap
    return eng.BalanceAnalysisEngine.build_snapshot(d0, d1, "SEGMENT")


def digest_bamon_bridge() -> List[Row]:
    """Köprü: start/end/net Δ + en büyük pozitif ve negatif katkılar."""
    p = _balance_payload()
    if not p:
        return []
    t = p["totals"]
    growth = f"%{t['growth_pct']:+.1f}" if t.get("growth_pct") is not None else ""
    rows = [
        _row("Başlangıç", _fmt_m(t["balance_t0_m"])),
        _row("Bitiş", _fmt_m(t["balance_t1_m"]),
             f"{t['delta_m']:+,.0f}M" + (f" ({growth})" if growth else ""),
             "pos" if t["delta_m"] >= 0 else "neg"),
    ]
    contrib = sorted(zip(p["categories"], p["delta_m"]),
                     key=lambda x: (x[1] if x[1] is not None else 0.0))
    for name, dv in [c for c in reversed(contrib) if c[1] and c[1] > 0][:3]:
        rows.append(_row(f"Katkı: {name}", f"{dv:+,.0f}M", tone="pos"))
    for name, dv in [c for c in contrib if c[1] and c[1] < 0][:2]:
        rows.append(_row(f"Kayıp: {name}", f"{dv:+,.0f}M", tone="neg"))
    return rows


def digest_bamon_heatmap() -> List[Row]:
    """Bakiye/müşteri heatmap: en büyük Δ hücreleri + yoğunlaşma sinyali."""
    p = _balance_payload()
    if not p or not p.get("heatmap"):
        return []
    hm, chm = p["heatmap"], p.get("customer_heatmap")
    cells = []
    for i, rname in enumerate(hm["rows"]):
        for j, cname in enumerate(hm["cols"]):
            dv = hm["delta_m"][i][j]
            cd = chm["delta_m"][i][j] if chm else None
            if dv is not None:
                cells.append((abs(dv), dv, cd, f"{rname} × {cname}"))
    cells.sort(reverse=True)
    rows = []
    for _, dv, cd, label in cells[:4]:
        cust = f", müşteri {cd:+,.0f}" if cd is not None else ""
        rows.append(_row(label, f"{dv:+,.0f}M{cust}",
                         tone="pos" if dv >= 0 else "neg"))
    # Yorum kuralı sinyali: bakiye artıp müşteri düşen hücreler (yoğunlaşma).
    conc = [(dv, cd, label) for _, dv, cd, label in cells
            if cd is not None and dv > 0 and cd < 0][:2]
    for dv, cd, label in conc:
        rows.append(_row(f"Yoğunlaşma: {label}",
                         f"bakiye {dv:+,.0f}M, müşteri {cd:+,.0f}", tone="neg"))
    return rows


# ── mevduat.vade ────────────────────────────────────────────────────────────

def _tenor_payload() -> Optional[dict]:
    from .engine import outstanding as eng

    snap = _dd_snapshot()
    if snap is None or "df" not in eng._SWAP_CACHE:
        # Swap cache'i soğuksa build_snapshot hedge için SQL tetikler — girmeyiz.
        return None
    _, d0, d1 = snap
    return eng.TenorAnalysisEngine.build_snapshot(d0, d1)


def digest_tamon_ladder() -> List[Row]:
    """Merdiven: WAT + kova bazında bakiye-hedge açığı."""
    p = _tenor_payload()
    if not p:
        return []
    rows = [_row("WAT", f"{p['wat']['t1']:.0f} gün",
                 f"{p['wat']['delta']:+.0f} gün",
                 "pos" if p["wat"]["delta"] >= 0 else "neg")]
    hedge = p.get("hedge_t1_m")
    gaps = []
    for i, b in enumerate(p["buckets"]):
        bal = p["balance_t1_m"][i]
        h = hedge[i] if hedge and i < len(hedge) else None
        if bal is None:
            continue
        if h is not None:
            gaps.append((bal - h, b, bal, h))
    if gaps:
        for gap, b, bal, h in sorted(gaps, reverse=True)[:4]:
            rows.append(_row(f"Açık: {b}",
                             f"{_fmt_m(bal)} bakiye vs {_fmt_m(h)} hedge "
                             f"(net {gap:+,.0f}M)",
                             tone="neg" if gap > 0 else "pos"))
        tot_bal = sum(g[2] for g in gaps)
        tot_h = sum(g[3] for g in gaps)
        if tot_bal > 0:
            rows.append(_row("Hedge örtüsü", f"%{tot_h / tot_bal * 100:.1f}"))
    else:
        # Hedge verisi payload'da yoksa yalnız en büyük kovalar (dürüst kısıt).
        top = sorted(zip(p["buckets"], p["balance_t1_m"]),
                     key=lambda x: -(x[1] or 0))[:4]
        rows += [_row(f"Kova: {b}", _fmt_m(v or 0)) for b, v in top]
        rows.append(_row("Not", "hedge verisi bu turda yok"))
    return rows


def digest_tamon_curve() -> List[Row]:
    """Vade eğrisi: kova bazında wavg faiz — LLM ters eğimi kuraldan okur."""
    p = _tenor_payload()
    if not p:
        return []
    rows = []
    t = p.get("totals") or {}
    if t.get("rate_delta_bps") is not None:
        rows.append(_row("Toplam Δ", f"{t['rate_delta_bps']:+,.0f} bps",
                         tone="neg" if t["rate_delta_bps"] > 0 else "pos"))
    for b, r in list(zip(p["buckets"], p["rate_t1_pct"]))[:10]:
        if r is not None:
            rows.append(_row(f"Eğri: {b}", f"%{r:.2f}"))
    return rows


# ── mevduat.donusler (weekly — prewarm edilmez; yalnız sıcak pencere) ───────

def digest_wr_rollovers() -> List[Row]:
    from .engine import weekly as wk

    df = _weekly_latest(wk._WEEKLY_AGG_DF_CACHE)
    if df is None or df.empty:
        return []
    t1 = wk.WeeklyRollingsEngine._pivot_currency(df)
    if not t1.get("rows"):
        return []
    footer = {f["label"]: f for f in t1.get("footer", [])}
    rows = []
    tot = footer.get("Total")
    if tot:
        rows.append(_row("Hafta toplamı", _fmt_m(tot["total"])))
        # En yüklü band (footer Total satırındaki en büyük kolon).
        bi = max(range(len(tot["values"])), key=lambda i: tot["values"][i])
        rows.append(_row(f"En yüklü band: {t1['columns'][bi]}",
                         _fmt_m(tot["values"][bi])))
        big = t1["columns"][-1]  # 200M+ (BAND_ORDER sonu)
        rows.append(_row(f"Büyük band ({big})", _fmt_m(tot["values"][-1])))
    for cur in ("TRY", "FX"):
        if cur in footer:
            rows.append(_row(f"{cur} toplamı", _fmt_m(footer[cur]["total"])))
    day = max(t1["rows"], key=lambda r: r["total"])
    rows.append(_row(f"En yoğun gün ({day['date']} {day['label']})",
                     _fmt_m(day["total"])))
    return rows


def digest_wr_dtm() -> List[Row]:
    from .engine import weekly as wk

    df_full = _weekly_latest(wk._WEEKLY_FULL_DF_CACHE)
    if df_full is None or df_full.empty:
        return []
    hist = wk.WeeklyRollingsEngine._dtm_histogram(df_full)
    total = sum(h["volume_m"] for h in hist) or 0.0
    rows = [_row(f"Kova {h['bucket']}",
                 f"{_fmt_m(h['volume_m'])} ({h['ticket_count']} adet)")
            for h in hist]
    if total > 0:
        top = max(hist, key=lambda h: h["volume_m"])
        rows.append(_row("En büyük kova payı",
                         f"{top['bucket']}: %{top['volume_m'] / total * 100:.1f}"))
    return rows


# ── mevduat.yeni_uretim ─────────────────────────────────────────────────────

def digest_np_rvhm() -> List[Row]:
    """NP heatmap: son gün kanal×AUM en büyük hücreler (compound wavg) + gün Δ."""
    from .engine import np_agg

    if np_agg._NP_CACHE is None:
        return []
    df = np_agg._NP_CACHE
    days = sorted(df["DAT"].dropna().unique())
    if len(days) < 2:
        return []
    t0, t1 = days[-2], days[-1]

    def _day(d):
        sub = df[df["DAT"] == d].copy()
        sub["_comp"] = np_agg.simple_to_compound_pct_series(
            sub["NP_FAIZ"], sub["TENOR_DAYS"])
        sub = sub[sub["_comp"].notna() & (sub["NP_HACIM"] > 0)]
        sub["_wr"] = sub["_comp"] * sub["NP_HACIM"]
        return sub

    s1, s0 = _day(t1), _day(t0)

    def _wavg_of(sub):
        vol = float(sub["NP_HACIM"].sum())
        return (float(sub["_wr"].sum()) / vol if vol else None), vol

    r1, v1 = _wavg_of(s1)
    r0, _ = _wavg_of(s0)
    rows = [_row("Son gün", str(pd.Timestamp(t1).date()))]
    if v1:
        rows.append(_row("Bağlanan hacim", _fmt_m(v1)))
    if r1 is not None:
        d = f"{(r1 - r0) * 100:+,.0f} bps/gün" if r0 is not None else ""
        rows.append(_row("WAvg faiz (bileşik)", f"%{r1:.2f}", d,
                         "neg" if r0 is not None and r1 > r0 else "pos"))
    g = (s1.groupby(["RELATED_PC", "AUM_BAND"], dropna=False, observed=False)
         [["NP_HACIM", "_wr"]].sum())
    g = g[g["NP_HACIM"] > 0]
    g["_r"] = g["_wr"] / g["NP_HACIM"]
    for (ch, band), r in g.sort_values("NP_HACIM", ascending=False).head(5).iterrows():
        rows.append(_row(f"{ch} × {band}",
                         f"{_fmt_m(r['NP_HACIM'])} @ %{r['_r']:.2f}"))
    return rows


def digest_np_aumcombo() -> List[Row]:
    """AUM combo: son hafta band bazında hacim + wavg faiz (band sırasıyla)."""
    from .engine import np_agg

    if np_agg._NP_CACHE is None:
        return []
    df_f = np_agg.apply_filters(np_agg._NP_CACHE, ccy=["TRY"])
    ts = np_agg.aggregate_timeseries(df_f, group_by=["AUM_BAND"], freq="W")
    if ts.empty:
        return []
    last = ts["DATE"].max()
    cur = ts[ts["DATE"] == last].copy()
    # Band sırası: _AUM_LABELS alt sınıra göre sıralı tanımlı — indeksini kullan.
    band_order = {lbl: i for i, lbl in enumerate(np_agg._AUM_LABELS.values())}
    cur = cur.sort_values("AUM_BAND",
                          key=lambda s: s.map(lambda b: band_order.get(b, 99)))
    rows = [_row("Hafta", str(pd.Timestamp(last).date()))]
    for _, r in cur.head(11).iterrows():
        if r["NP_HACIM"] and r["NP_HACIM"] > 0 and pd.notna(r["NP_FAIZ"]):
            rows.append(_row(str(r["AUM_BAND"]),
                             f"{_fmt_m(r['NP_HACIM'])} @ %{r['NP_FAIZ']:.2f}"))
    return rows


# ── mevduat.sektor ──────────────────────────────────────────────────────────

def digest_sec_mix() -> List[Row]:
    from .engine import sector_data as sd

    # vade_mix_comparison soğuk cache'te SQL tetikler — sıcaklık ön koşul.
    if sd._BANK_VADE_CACHE is None or sd._VADE_CACHE is None:
        return []
    mix = sd.vade_mix_comparison(None, "monthly")
    if not mix or not mix.get("buckets"):
        return []
    rows = [_row("Veri ayı", str(mix.get("date") or "")[:10])]
    for b, bank, sec, diff in zip(mix["buckets"], mix["bank_pct"],
                                  mix["sector_pct"], mix["diff_pp"]):
        if bank is None and sec is None:
            continue
        bank_s = f"%{bank:.1f}" if bank is not None else "—"
        sec_s = f"%{sec:.1f}" if sec is not None else "—"
        d = f"{diff:+.1f} pp" if diff is not None else ""
        rows.append(_row(f"Vade {b}", f"banka {bank_s} vs sektör {sec_s}", d))
    return rows


# ── Kayıt ───────────────────────────────────────────────────────────────────

def build_digest_registry() -> Dict[str, Callable[[], List[Row]]]:
    """block_id → digest fonksiyonu (hepsi _safe sarmalı — hata = boş liste)."""
    fns = {
        "camon_wf":      digest_camon_wf,
        "camon_bubble":  digest_camon_bubble,
        "camon_ratehm":  digest_camon_ratehm,
        "bamon_bridge":  digest_bamon_bridge,
        "bamon_heatmap": digest_bamon_heatmap,
        "tamon_ladder":  digest_tamon_ladder,
        "tamon_curve":   digest_tamon_curve,
        "wr_rollovers":  digest_wr_rollovers,
        "wr_dtm":        digest_wr_dtm,
        "np_rvhm":       digest_np_rvhm,
        "np_aumcombo":   digest_np_aumcombo,
        "sec_mix":       digest_sec_mix,
    }
    return {bid: (lambda f=fn: _safe(f)) for bid, fn in fns.items()}

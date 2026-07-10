"""deposits_dashboards.py — PRISMA_DEP_* / PRISMA_NP_* tablolarından 5 deposits
dashboard'unu, KAYNAK dashboard'un sayfalarıyla blok-blok BİREBİR üretir ve
S3'e yazar. TEK script, ofiste:

    python jobs/deposits_dashboards.py --sicil A63837

Kaynak eşleme (NIM_calculation templates/index.html envanteri):
  p_dep_cost     ← Outstanding Cost Analysis   (Monthly + Daily alt bölümleri:
                    Deposit Rate Waterfall, 2× Bubble, Interest Rate Heatmap)
  p_dep_balance  ← Outstanding Balance Analysis (KPI şeridi, Balance Bridge,
                    Balance+Customer heatmap, Composition Evolution + Δ)
  p_dep_tenor    ← Outstanding Tenor Analysis   (WAT KPI'ları, Maturity Ladder
                    + Δ, Term Structure dual-axis, Composition + Δ, Bucket Rate
                    Waterfall, günlükte Per-Bucket Rate Evolution + WAT trend)
  p_dep_rollings ← Future Deposit Rollings      (3 pivot tablo, DTM histogram,
                    segment donut, tarih×segment stacked, müşteri listesi)
  p_dep_newbiz   ← New Business Volume&Pricing  (2 bubble, Rate×Volume heatmap,
                    AUM dual-axis combo, konsantrasyon eğrisi)

İki-tarih (t₀→t₁) karşılaştırma deseni: dashboard'daki date_0/date_1 seçicisi
burada TEK date_range filtresine eşlenir — t₀ = :donem_from AS-OF snapshot'ı,
t₁ = :donem_to AS-OF snapshot'ı. Waterfall/bridge/bubble/heatmap'ler bu iki
snapshot'ın SQL'de hesaplanan farkıdır; filtre değiştikçe yeniden koşarlar.

Kaynak dashboard'da olup BİLİNÇLİ getirilmeyenler (onay bekleyen liste):
  * Drill-down (hücre/bar çift tık → müşteri modalı) ve çapraz-widget
    navigasyonu — Prisma'da drill kavramı yok.
  * Rollings pivot tablolarının AG-Grid hiyerarşisi — düz pivot tablo verilir.
  * NP heatmap hücre-hover yan combo'su; min-bubble-size slider'ı (bubble'lar
    boyuta göre ilk 40 ile sınırlanır — slider'ın yerine geçen düzenleme).

Cost Analysis sayfası kaynakla BİREBİR (bu revizyonda getirilenler):
  * Waterfall = 3 slaytlık carousel (kaynaktaki CA_MON_SLIDES = wf1/wf2/wf4):
    1) "Mix vs Pricing" özet köprüsü, 2) Pricing Drivers (Top 7 + Other) +
    altında Balance Growth companion bar'ı, 3) Mix Drivers (Top 7 + Other) +
    altında Weight Changes companion bar'ı. Bennet ayrıştırması SQL'de:
    mix_i = Δw_i·r̄_i, price_i = w̄_i·Δr_i; değerler % (etiket okunurluğu).
  * "Gruplama (Dimensions)" filtresi — kaynaktaki PRODUCT/SUBPRODUCT/
    CUSTOMER_TYPE/AUM/SEGMENT toggle'ları. Composite grup anahtarı SQL'de
    ('X' IN (:gruplama) CASE zinciri, '_' ile birleştirme — motorla aynı).
    Varsayılan kaynaktaki gibi: PRODUCT kapalı, kalan dördü açık.
  * Bubble'lar kaynak ölçülerle: Balance Evolution x=ΔBakiye ₺M,
    Interest Rate Evolution x=ΔFaiz bps; y=t₁ faizi %, boyut=(|b0|+|b1|)/2 ₺M.
  * Heatmap = 2 slaytlık carousel (kaynaktaki Δ↔seviye toggle'ının karşılığı):
    varsayılan slayt Δbps (kaynak varsayılanı), ikinci slayt t₁ seviyesi %.
  * t₀/t₁ varsayılanı: aylıkta verideki SON İKİ AY (kaynak dropdown
    varsayılanı), günlükte son gün + önceki takvim Perşembe'si (kaynak
    _prevThursday). AS-OF eşleme korunur (seçilen tarihe ≤ en yakın snapshot).

Statik: dataset/cron bayrağı yok; veri tazelemek = deposits_pipeline koşusu.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("deposits_dashboards")

S3_MANIFEST_KEY = "prisma-treasury/presentations/{sicil}/{pid}/manifest.json"


# ════════════════════════════════════════════════════════════════════════════
# Oracle erişimi — kurulum anı sorguları (domain keşfi + config doldurma)
# ════════════════════════════════════════════════════════════════════════════

class Runner:
    def __init__(self, dc, con):
        self.dc, self.con = dc, con

    def query(self, sql: str, params: dict | None = None):
        return self.dc.edw_query_to_pandas(self.con, sql, params=params or {})

    def distinct(self, table: str, col: str) -> list[str]:
        df = self.query(
            f"SELECT DISTINCT {col} AS V FROM {table} "
            f"WHERE {col} IS NOT NULL ORDER BY 1")
        return [str(v) for v in df["V"].tolist() if str(v).strip() != ""]

    def minmax_date(self, table: str, col: str) -> tuple[str, str]:
        df = self.query(f"SELECT MIN({col}) AS LO, MAX({col}) AS HI FROM {table}")
        lo, hi = df.iloc[0]["LO"], df.iloc[0]["HI"]
        f = lambda v: str(v)[:10]
        return f(lo), f(hi)

    def dates(self, table: str, col: str) -> list[str]:
        """Tablodaki tüm snapshot tarihleri (ISO, artan) — varsayılan t₀/t₁
        seçimi kaynak dashboard'daki tarih dropdown'larıyla aynı listeden."""
        df = self.query(f"SELECT DISTINCT {col} AS D FROM {table} "
                        f"WHERE {col} IS NOT NULL ORDER BY 1")
        return [str(v)[:10] for v in df["D"].tolist()]

    def rowcount(self, table: str) -> int:
        return int(self.query(f"SELECT COUNT(*) AS N FROM {table}").iloc[0]["N"])


# ════════════════════════════════════════════════════════════════════════════
# Manifest yapı taşları
# ════════════════════════════════════════════════════════════════════════════

def _var(name, tag, vtype, default, allowed=None):
    v = {"name": name, "semantic_tag": tag, "type": vtype,
         "required": True, "default": default}
    if allowed is not None:
        v["allowed_values"] = allowed
    return v


def _filter(fid, tag, ftype, label, default, allowed=None):
    f = {"id": fid, "semantic_tag": tag, "type": ftype,
         "label": label, "default": default}
    if allowed is not None:
        f["allowed_values"] = allowed
    return f


_SEED_CONFIG = {
    "kpi":         {"value": 0, "unit": "", "delta": 0, "delta_label": "", "period": ""},
    "bar_chart":   {"categories": [], "series": []},
    "combo_chart": {"categories": [], "series": [],
                    "left_axis_title": "", "right_axis_title": ""},
    "line_chart":  {"x_axis": [], "series": []},
    "area_chart":  {"x_axis": [], "series": []},
    "heatmap":     {"x_axis": [], "series": []},
    "pie_chart":   {"labels": [], "values": []},
    "data_table":  {"columns": [], "rows": []},
    "waterfall_chart": {"categories": [], "values": [], "totals": [], "unit": ""},
    "scatter_chart":   {"points": [], "x_title": "", "y_title": ""},
}


def _block(bid, btype, title, query, variables, bindings, source_table,
           width="1/2", config=None):
    cfg = dict(_SEED_CONFIG.get(btype, {}))
    cfg.update(config or {})
    return {
        "id": bid, "type": btype, "title": title, "locked": False,
        "width": width, "query": query.strip(),
        "variables": variables,
        "variable_bindings": {v["name"]: {"from_filter": bindings[v["name"]]}
                              for v in variables if v["name"] in bindings},
        "source_tables": [source_table],
        "config": cfg,
    }


def _tbl_cols(*names):
    return {"columns": [{"field": n, "header": n} for n in names], "rows": []}


def _section(sid, title, children):
    return {"id": sid, "type": "section_header", "title": title,
            "config": {}, "children": children}


def _enum_domain(runner, table, col, label, fid, tag, bindings_out):
    values = runner.distinct(table, col)
    filt = _filter(fid, tag, "enum_multi", label, values, values)
    var = _var(fid.removeprefix("f_"), tag, "enum_multi", values, values)
    bindings_out[var["name"]] = fid
    return filt, var


def _quote_list(values):
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


# ════════════════════════════════════════════════════════════════════════════
# Ortak SQL kalıpları — iki-snapshot (t₀ = as-of :donem_from, t₁ = :donem_to)
# ════════════════════════════════════════════════════════════════════════════

def _snap_cte(table, dcol, enum_where, dim, extra_measures=""):
    """f/t0/t1/s0/s1/g0/g1 CTE zinciri. s* = dim bazında BALANCE+WR_SUM
    (istenirse ek ölçüler), g* = toplamlar."""
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :donem_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :donem_to),
s0 AS (SELECT {dim} p, SUM(BALANCE) b, SUM(WR_SUM) wr{extra_measures}
       FROM f, t0 WHERE f.{dcol} = t0.m GROUP BY {dim}),
s1 AS (SELECT {dim} p, SUM(BALANCE) b, SUM(WR_SUM) wr{extra_measures}
       FROM f, t1 WHERE f.{dcol} = t1.m GROUP BY {dim}),
g0 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s0),
g1 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s1)"""


def sql_rate_waterfall(table, dcol, enum_where, dim):
    """Faiz köprüsü: Başlangıç (R0) + dim-üyesi katkıları + Bitiş (R1).
    Katkı_i = (wr1_i/TB1 − wr0_i/TB0)·100 → Σ katkı = R1−R0 birebir."""
    return f"""{_snap_cte(table, dcol, enum_where, dim)},
j AS (SELECT NVL(s1.p, s0.p) p,
             (NVL(s1.wr,0)/NULLIF(g1.tb,0) - NVL(s0.wr,0)/NULLIF(g0.tb,0))*100 c
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p CROSS JOIN g0 CROSS JOIN g1)
SELECT STEP, ROUND(DELTA, 3) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'Başlangıç' step, twr/NULLIF(tb,0)*100 delta, 1 is_total FROM g0
  UNION ALL SELECT 1, ROWNUM, p, c, 0 FROM (SELECT p, c FROM j ORDER BY c DESC)
  UNION ALL SELECT 2, 0, 'Bitiş', twr/NULLIF(tb,0)*100, 1 FROM g1
) ORDER BY ord, ord2"""


def sql_balance_bridge(table, dcol, enum_where, dim):
    """Bakiye köprüsü (₺Mr): Başlangıç + dim-üyesi Δ'ları + Bitiş."""
    return f"""{_snap_cte(table, dcol, enum_where, dim)},
j AS (SELECT NVL(s1.p, s0.p) p, (NVL(s1.b,0) - NVL(s0.b,0))/1e9 d
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT STEP, ROUND(DELTA, 3) AS DELTA_MLR, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'Başlangıç' step, tb/1e9 delta, 1 is_total FROM g0
  UNION ALL SELECT 1, ROWNUM, p, d, 0 FROM (SELECT p, d FROM j ORDER BY d DESC)
  UNION ALL SELECT 2, 0, 'Bitiş', tb/1e9, 1 FROM g1
) ORDER BY ord, ord2"""


def sql_heatmap_t1(table, dcol, enum_where, rowdim, coldim, measure):
    """t₁ snapshot heatmap (long format). measure: 'rate' | 'bal' | 'cust'."""
    z = {"rate": "SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100",
         "bal": "SUM(BALANCE)/1e9",
         "cust": "SUM(CUST_COUNT)"}[measure]
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :donem_to)
SELECT {rowdim} AS SATIR, {coldim} AS KOLON, ROUND({z}, 2) AS DEGER
FROM f, t1 WHERE f.{dcol} = t1.m
  AND {rowdim} IS NOT NULL AND {coldim} IS NOT NULL
GROUP BY {rowdim}, {coldim} ORDER BY 1, 2"""


def sql_mix(table, dcol, enum_where, dim, members):
    """Composition Evolution: 2 satır (Başlangıç/Bitiş) × üye payları (%)."""
    cases0 = ", ".join(
        f"ROUND(SUM(CASE WHEN p = '{m}' THEN b ELSE 0 END)/NULLIF(SUM(b),0)*100, 2) AS \"{m}\""
        for m in members)
    return f"""{_snap_cte(table, dcol, enum_where, dim)}
SELECT 'Başlangıç' AS DONEM, {cases0} FROM s0
UNION ALL
SELECT 'Bitiş', {cases0} FROM s1"""


def sql_mix_delta(table, dcol, enum_where, dim):
    """Composition Δ: üye başına pay farkı (puan)."""
    return f"""{_snap_cte(table, dcol, enum_where, dim)}
SELECT NVL(s1.p, s0.p) AS UYE,
       ROUND(NVL(s1.b,0)/NULLIF(g1.tb,0)*100 - NVL(s0.b,0)/NULLIF(g0.tb,0)*100, 2) AS DELTA_PUAN
FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p CROSS JOIN g0 CROSS JOIN g1
ORDER BY 2 DESC"""


# ════════════════════════════════════════════════════════════════════════════
# Cost Analysis SQL kalıpları — kaynak DepositDetailEngine.build_waterfalls
# (NIM_calculation app.py L1324-1470) birebir portu. Ayrıştırma Bennet
# (simetrik): mix_i = Δw_i·r̄_i, price_i = w̄_i·Δr_i,
# mix-driver_i = Δw_i·(r̄_i − r̄_toplam). Σmix_i = Mix, Σprice_i = Pricing,
# Start + Mix + Pricing = End birebir tutar.
# Birim: YÜZDE (kaynak bps gösterir ama 4-5 haneli bps etiketleri okunmuyor —
# kullanıcı kararı: değerler/etiketler/y-ekseni %; 1 bps = 0.01 çözünürlük
# ROUND(...,2) ile korunur).
# ════════════════════════════════════════════════════════════════════════════

CA_DIMS = [("PRODUCT", "DIM_PRODUCT"), ("SUBPRODUCT", "DIM_SUBPRODUCT"),
           ("CUSTOMER_TYPE", "DIM_CUSTOMER"), ("AUM", "DIM_AUM"),
           ("SEGMENT", "DIM_SEGMENT")]
CA_TOP_N = 7


def _prev_thursday(iso: str) -> str:
    """Günlük alt sekmenin kaynak varsayılanı: t₁'den ÖNCEKİ takvim Perşembesi
    (index.html _prevThursday). t₁ Perşembe ise 7 gün öncesi."""
    d = date.fromisoformat(iso[:10])
    diff = (d.weekday() - 3) % 7 or 7          # Mon=0 … Thu=3
    return (d - timedelta(days=diff)).isoformat()


def ca_grp_expr():
    """Composite grup anahtarı — motorun _group_by_dims'i: seçili boyutların
    boş olmayan değerleri '_' ile birleşir. Seçim :gruplama enum_multi
    değişkeninden gelir (binder aynı placeholder'ı tutarlı expand eder)."""
    parts = "\n        || ".join(
        f"CASE WHEN '{d}' IN (:gruplama) AND {c} IS NOT NULL "
        f"THEN {c} || '_' ELSE '' END"
        for d, c in CA_DIMS)
    return f"NVL(RTRIM({parts}, '_'), '-')"


def ca_core(table, dcol, enum_where, dv):
    """f→t0/t1→s0/s1→g0/g1→k→mm→eff→drv CTE zinciri. drv = grup başına
    b0/b1/r0/r1 + Bennet etkileri (%) + Δpay (puan)."""
    grp = ca_grp_expr()
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
s0 AS (SELECT {grp} p, SUM(BALANCE) b, SUM(WR_SUM) wr
       FROM f, t0 WHERE f.{dcol} = t0.m GROUP BY {grp}),
s1 AS (SELECT {grp} p, SUM(BALANCE) b, SUM(WR_SUM) wr
       FROM f, t1 WHERE f.{dcol} = t1.m GROUP BY {grp}),
g0 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s0),
g1 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s1),
k AS (SELECT g0.twr/NULLIF(g0.tb,0)*100 start_pct,
             g1.twr/NULLIF(g1.tb,0)*100 end_pct
      FROM g0 CROSS JOIN g1),
mm AS (SELECT NVL(s0.p, s1.p) p, NVL(s0.b,0) b0, NVL(s1.b,0) b1,
              NVL(s0.wr,0) wr0, NVL(s1.wr,0) wr1
       FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p),
eff AS (SELECT mm.p, mm.b0, mm.b1,
               CASE WHEN mm.b0 <> 0 THEN mm.wr0/mm.b0 ELSE 0 END r0,
               CASE WHEN mm.b1 <> 0 THEN mm.wr1/mm.b1 ELSE 0 END r1,
               NVL(mm.b0/NULLIF(g0.tb,0), 0) w0,
               NVL(mm.b1/NULLIF(g1.tb,0), 0) w1
        FROM mm CROSS JOIN g0 CROSS JOIN g1),
drv AS (SELECT p, b0, b1, r0, r1,
               (w1 - w0) * (r0 + r1)/2 * 100 mix_pct,
               (w0 + w1)/2 * (r1 - r0) * 100 price_pct,
               (w1 - w0) * ((r0 + r1)/2 * 100
                            - (SELECT (start_pct + end_pct)/2 FROM k)) mixdrv_pct,
               (w1 - w0) * 100 dw_pct
        FROM eff)"""


def ca_sql_wf1(table, dcol, enum_where, dv):
    """Slayt 1 — Rate Waterfall (%): Mix vs Pricing (kaynak wf1)."""
    return f"""{ca_core(table, dcol, enum_where, dv)}
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 1 ord, 'Start Rate' step, start_pct delta, 1 is_total FROM k
  UNION ALL
  SELECT 2, 'Mix / Interaction', (SELECT SUM(mix_pct) FROM drv), 0 FROM DUAL
  UNION ALL
  SELECT 3, 'Pricing (rate, detailed)', (SELECT SUM(price_pct) FROM drv), 0 FROM DUAL
  UNION ALL
  SELECT 4, 'End Rate', end_pct, 1 FROM k
) ORDER BY ord"""


def ca_sql_wf2(table, dcol, enum_where, dv):
    """Slayt 2 — Pricing Drivers (Top 7 + Other, %) (kaynak wf2).
    Bazal 'After Mix' = Start + Mix; üyeler |price_eff| sırasıyla."""
    return f"""{ca_core(table, dcol, enum_where, dv)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(price_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'After Mix' step,
         start_pct + (SELECT SUM(mix_pct) FROM drv) delta, 1 is_total FROM k
  UNION ALL
  SELECT 1, rn, p, price_pct, 0 FROM rnk WHERE rn <= {CA_TOP_N}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(price_pct) FROM rnk WHERE rn > {CA_TOP_N}), 0
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {CA_TOP_N})
  UNION ALL
  SELECT 3, 0, 'End Rate', end_pct, 1 FROM k
) ORDER BY ord, ord2"""


def ca_sql_wf2_bal(table, dcol, enum_where, dv):
    """Slayt 2 companion — Balance Growth (₺M), wf2 ile aynı üye sırası.
    Çapa kolonlara 0 (kaynakta None) → eksen hizası korunur."""
    return f"""{ca_core(table, dcol, enum_where, dv)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(price_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA_M, 2) AS "Bakiye Degisimi (MTL)" FROM (
  SELECT 0 ord, 0 ord2, 'After Mix' step, 0 delta_m FROM DUAL
  UNION ALL
  SELECT 1, rn, p, (b1 - b0)/1e6 FROM rnk WHERE rn <= {CA_TOP_N}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(b1 - b0)/1e6 FROM rnk WHERE rn > {CA_TOP_N})
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {CA_TOP_N})
  UNION ALL
  SELECT 3, 0, 'End Rate', 0 FROM DUAL
) ORDER BY ord, ord2"""


def ca_sql_wf4(table, dcol, enum_where, dv):
    """Slayt 3 — Mix Drivers (Top 7 + Other, %) (kaynak wf4).
    Üye değeri Δw·(r̄ − r̄toplam); Start Rate → After Mix köprüsü."""
    return f"""{ca_core(table, dcol, enum_where, dv)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(mixdrv_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'Start Rate' step, start_pct delta, 1 is_total FROM k
  UNION ALL
  SELECT 1, rn, p, mixdrv_pct, 0 FROM rnk WHERE rn <= {CA_TOP_N}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(mixdrv_pct) FROM rnk WHERE rn > {CA_TOP_N}), 0
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {CA_TOP_N})
  UNION ALL
  SELECT 3, 0, 'After Mix',
         start_pct + (SELECT SUM(mix_pct) FROM drv), 1 FROM k
) ORDER BY ord, ord2"""


def ca_sql_wf4_weights(table, dcol, enum_where, dv):
    """Slayt 3 companion — Weight Changes (Δ pay, puan), wf4 sırasıyla."""
    return f"""{ca_core(table, dcol, enum_where, dv)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(mixdrv_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA_W, 3) AS "Agirlik Degisimi (puan)" FROM (
  SELECT 0 ord, 0 ord2, 'Start Rate' step, 0 delta_w FROM DUAL
  UNION ALL
  SELECT 1, rn, p, dw_pct FROM rnk WHERE rn <= {CA_TOP_N}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(dw_pct) FROM rnk WHERE rn > {CA_TOP_N})
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {CA_TOP_N})
  UNION ALL
  SELECT 3, 0, 'After Mix', 0 FROM DUAL
) ORDER BY ord, ord2"""


def ca_sql_bubble(table, dcol, enum_where, dv, mode):
    """Bubble noktaları (kaynak _build_bubble_charts). mode='bal':
    x=ΔBakiye ₺M; mode='rate': x=ΔFaiz bps. y=t₁ faizi %,
    boyut=(|b0|+|b1|)/2 ₺M. Kaynaktaki min-size slider yerine boyuta göre
    ilk 40 nokta (derin gruplamada okunabilirlik). 5. kolon = WAvg t₁ faizi:
    scatter sözleşmesi gereği yatay referans çizgisi olur (kaynaktaki kesikli
    'WAvg %x.xx' çizgisi) ve filtre/gruplama değişince güncellenir."""
    x = "(b1 - b0)/1e6" if mode == "bal" else "(r1 - r0)*10000"
    return f"""{ca_core(table, dcol, enum_where, dv)}
SELECT AD, X_DEGER, FAIZ_T1_PCT, BOYUT_M, "WAvg (%)" FROM (
  SELECT p AS AD, ROUND({x}, 2) X_DEGER,
         ROUND(r1*100, 2) FAIZ_T1_PCT,
         ROUND((ABS(b0) + ABS(b1))/2/1e6, 2) BOYUT_M,
         (SELECT ROUND(end_pct, 2) FROM k) AS "WAvg (%)"
  FROM drv WHERE b0 <> 0 OR b1 <> 0
  ORDER BY (ABS(b0) + ABS(b1)) DESC
) WHERE ROWNUM <= 40"""


def ca_sql_heatmap(table, dcol, enum_where, dv, mode):
    """Interest Rate Heatmap — Segment × AUM (kaynak _rate_heatmap_seg_aum).
    mode='delta': Δbps (kaynak varsayılan görünümü; iki dönemde de veri yoksa
    0); mode='level': t₁ seviyesi %. Gruplamadan bağımsız — kaynakta da ham
    veriden kurulur. AUM kolonları sayısal banda göre sıralanır."""
    val = ("CASE WHEN r0 <> 0 AND r1 <> 0 THEN ROUND((r1 - r0)*10000, 1) "
           "ELSE 0 END" if mode == "delta"
           else "CASE WHEN r1 <> 0 THEN ROUND(r1*100, 2) ELSE 0 END")
    alias = "DELTA_BPS" if mode == "delta" else "FAIZ_PCT"
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
a0 AS (SELECT NVL(DIM_SEGMENT, '-') s, NVL(DIM_AUM, '-') a,
              SUM(BALANCE) b, SUM(WR_SUM) wr
       FROM f, t0 WHERE f.{dcol} = t0.m
       GROUP BY NVL(DIM_SEGMENT, '-'), NVL(DIM_AUM, '-')),
a1 AS (SELECT NVL(DIM_SEGMENT, '-') s, NVL(DIM_AUM, '-') a,
              SUM(BALANCE) b, SUM(WR_SUM) wr
       FROM f, t1 WHERE f.{dcol} = t1.m
       GROUP BY NVL(DIM_SEGMENT, '-'), NVL(DIM_AUM, '-')),
j AS (SELECT NVL(a0.s, a1.s) s, NVL(a0.a, a1.a) a,
             CASE WHEN NVL(a0.b, 0) <> 0 THEN a0.wr/a0.b ELSE 0 END r0,
             CASE WHEN NVL(a1.b, 0) <> 0 THEN a1.wr/a1.b ELSE 0 END r1
      FROM a0 FULL OUTER JOIN a1 ON a0.s = a1.s AND a0.a = a1.a)
SELECT s AS SEGMENT, a AS AUM_BANDI, {val} AS {alias}
FROM j
ORDER BY s, TO_NUMBER(REGEXP_SUBSTR(a, '\\d+')) NULLS FIRST, a"""


def _carousel(cid, title, children):
    return {"id": cid, "type": "carousel", "title": title,
            "locked": False, "children": children}


def _canvas(cid, title, children):
    return {"id": cid, "type": "canvas", "title": title,
            "locked": False, "children": children}


def _manifest_shell(pid, title, description, filters, sections, tables):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": pid, "version": 1, "created_at": now, "updated_at": now,
        "meta": {"title": title, "eyebrow": "Deposits",
                 "date": date.today().strftime("%d.%m.%Y"),
                 "description": description},
        "basket": [{"table": t, "alias": t.split(".")[-1].lower(),
                    "column_concepts": {}} for t in tables],
        "filters": filters,
        "blocks": sections,
        "uploads": [],
        "bound_experts": [],
    }


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 1 — Outstanding Cost Analysis (Monthly + Daily)
# ════════════════════════════════════════════════════════════════════════════

def build_cost(runner, sch):
    """Kaynak 'Outstanding Cost Analysis' ile blok-blok birebir:
    her alt sekmede (Monthly / Daily) 3 slaytlık waterfall carousel'i,
    yan yana 2 bubble, 2 slaytlık heatmap carousel'i (Δbps ↔ t₁ seviyesi)."""
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_sub, v_sub = _enum_domain(runner, M, "DIM_SUBPRODUCT", "Alt Ürün", "f_alturun", "other", b)
    f_cst, v_cst = _enum_domain(runner, M, "DIM_CUSTOMER", "Müşteri Tipi", "f_musteri", "other", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b)

    # Gruplama (kaynaktaki "Dimensions:" toggle'ları). Varsayılan = kaynak
    # varsayılanı: PRODUCT kapalı, SUBPRODUCT/CUSTOMER_TYPE/AUM/SEGMENT açık.
    dim_names = [d for d, _ in CA_DIMS]
    grp_default = ["SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    f_grp = _filter("f_gruplama", "other", "enum_multi",
                    "Gruplama (Dimensions)", grp_default, dim_names)
    v_grp = _var("gruplama", "other", "enum_multi", grp_default, dim_names)
    b["gruplama"] = "f_gruplama"

    # t₀/t₁ varsayılanları kaynak dropdown'larıyla aynı: aylıkta verideki son
    # iki ay, günlükte son gün + önceki takvim Perşembesi. (Önceki sürümdeki
    # "MIN tarihten bugüne" varsayılanı, sitede görünen değerlerden çok farklı
    # deltalar üretiyordu — veri yanlış değil, t₀ yanlıştı.)
    mon_dates = runner.dates(M, "MONTH")
    if not mon_dates:
        raise RuntimeError(f"{M} boş — önce deposits_pipeline koşulmalı")
    m_from = mon_dates[-2] if len(mon_dates) >= 2 else mon_dates[0]
    m_to = mon_dates[-1]
    day_dates = runner.dates(D, "DAT") or mon_dates
    d_to = day_dates[-1]
    d_from = _prev_thursday(d_to)
    if d_from < day_dates[0]:
        d_from = day_dates[0]

    f_don_m = _filter("f_donem_ay", "as_of_time", "date_range",
                      "Dönem — Aylık (t₀ → t₁)", {"from": m_from, "to": m_to})
    v_don_m = _var("donem_ay", "as_of_time", "date_range",
                   {"from": m_from, "to": m_to})
    b["donem_ay"] = "f_donem_ay"
    f_don_d = _filter("f_donem_gun", "as_of_time", "date_range",
                      "Dönem — Günlük (t₀ → t₁)", {"from": d_from, "to": d_to})
    v_don_d = _var("donem_gun", "as_of_time", "date_range",
                   {"from": d_from, "to": d_to})
    b["donem_gun"] = "f_donem_gun"

    # NULL boyutlar (ör. AUM'suz Kasa/O-N satırları) IN-filtreden düşmesin —
    # kaynak filtre paneli de boş değerleri listelemez ama satırları tutar.
    W = ("(DIM_SEGMENT IN (:segment) OR DIM_SEGMENT IS NULL) AND "
         "(DIM_PRODUCT IN (:urun) OR DIM_PRODUCT IS NULL) AND "
         "(DIM_SUBPRODUCT IN (:alturun) OR DIM_SUBPRODUCT IS NULL) AND "
         "(DIM_CUSTOMER IN (:musteri) OR DIM_CUSTOMER IS NULL) AND "
         "(DIM_AUM IN (:aum) OR DIM_AUM IS NULL)")

    def widgets(T, dcol, v_don, dv, sfx, label):
        enums = [v_seg, v_prd, v_sub, v_cst, v_aum]
        vars_wf = [v_don] + enums + [v_grp]
        vars_hm = [v_don] + enums

        def wf(bid, title, sql, unit="%"):
            return _block(bid, "waterfall_chart", title, sql, vars_wf, b, T,
                          width="full", config={"unit": unit})

        def bar(bid, title, sql):
            return _block(bid, "bar_chart", title, sql, vars_wf, b, T,
                          width="full")

        car_wf = _carousel(
            f"car_wf_{sfx}", f"Deposit Rate Waterfall ({label})",
            [wf(f"wf1_{sfx}", "Rate Waterfall (%): Mix vs Pricing",
                ca_sql_wf1(T, dcol, W, dv)),
             _canvas(f"cv_wf2_{sfx}", "Pricing Drivers",
                     [wf(f"wf2_{sfx}",
                         f"Pricing Drivers (Top {CA_TOP_N} + Other, %)",
                         ca_sql_wf2(T, dcol, W, dv)),
                      bar(f"wf2bal_{sfx}", "Balance Growth (₺M)",
                          ca_sql_wf2_bal(T, dcol, W, dv))]),
             _canvas(f"cv_wf4_{sfx}", "Mix Drivers",
                     [wf(f"wf4_{sfx}",
                         f"Mix Drivers (Top {CA_TOP_N} + Other, %)",
                         ca_sql_wf4(T, dcol, W, dv)),
                      bar(f"wf4w_{sfx}", "Weight Changes (Δ pay, puan)",
                          ca_sql_wf4_weights(T, dcol, W, dv))])])

        bub_bal = _block(
            f"bub_bal_{sfx}", "scatter_chart", f"Balance Evolution ({label})",
            ca_sql_bubble(T, dcol, W, dv, "bal"), vars_wf, b, T,
            config={"x_title": "Δ Bakiye (₺M)", "y_title": "Faiz t₁ (%)"})
        bub_rate = _block(
            f"bub_rate_{sfx}", "scatter_chart",
            f"Interest Rate Evolution ({label})",
            ca_sql_bubble(T, dcol, W, dv, "rate"), vars_wf, b, T,
            config={"x_title": "Δ Faiz (bps)", "y_title": "Faiz t₁ (%)"})

        car_hm = _carousel(
            f"car_hm_{sfx}", f"Interest Rate Heatmap — Segment × AUM ({label})",
            [_block(f"hm_delta_{sfx}", "heatmap",
                    "Interest Rate Delta (bps)",
                    ca_sql_heatmap(T, dcol, W, dv, "delta"),
                    vars_hm, b, T, width="full"),
             _block(f"hm_level_{sfx}", "heatmap",
                    "Interest Rate (t₁, %)",
                    ca_sql_heatmap(T, dcol, W, dv, "level"),
                    vars_hm, b, T, width="full")])

        return [car_wf, bub_bal, bub_rate, car_hm]

    manifest = _manifest_shell(
        "p_dep_cost", "Outstanding Cost Analysis",
        "Mevduat maliyet analizi — kaynak dashboard'la birebir: 3 slaytlık "
        "rate waterfall carousel'i (Mix vs Pricing / Pricing Drivers / Mix "
        "Drivers, Bennet ayrıştırması, %), Balance + Rate Evolution "
        "bubble'ları (₺M / bps), Segment × AUM faiz heatmap'i (Δbps ↔ t₁ "
        "seviye slaytları). t₀/t₁ = dönem filtresi (AS-OF, varsayılan son iki "
        "snapshot); 'Gruplama' filtresi kaynaktaki Dimensions toggle'ları.",
        [f_don_m, f_don_d, f_grp, f_seg, f_prd, f_sub, f_cst, f_aum],
        [_section("sec_monthly", "Monthly Averages",
                  widgets(M, "MONTH", v_don_m, "donem_ay", "mon", "Monthly")),
         _section("sec_daily", "Daily Evolution",
                  widgets(D, "DAT", v_don_d, "donem_gun", "dly", "Daily"))],
        [M, D])
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 2 — Outstanding Balance Analysis (Monthly + Daily)
# ════════════════════════════════════════════════════════════════════════════

def build_balance(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b)
    lo, _ = runner.minmax_date(M, "MONTH")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem (t₀ → t₁)",
                    {"from": lo, "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": "today"})
    b["donem"] = "f_donem"
    all_vars = [v_don, v_seg, v_prd, v_aum]
    W = "DIM_SEGMENT IN (:segment) AND DIM_PRODUCT IN (:urun) AND DIM_AUM IN (:aum)"
    products = runner.distinct(M, "DIM_PRODUCT")

    def kpis(T, dcol, sfx):
        base = _snap_cte(T, dcol, W, "DIM_PRODUCT")
        return [
            _block(f"kpi_b0_{sfx}", "kpi", "Bakiye t₀ (₺Mr)",
                   f"{base} SELECT ROUND(tb/1e9, 2) FROM g0",
                   all_vars, b, T, width="1/3", config={"unit": "Mr ₺"}),
            _block(f"kpi_b1_{sfx}", "kpi", "Bakiye t₁ (₺Mr)",
                   f"{base} SELECT ROUND(tb/1e9, 2) FROM g1",
                   all_vars, b, T, width="1/3", config={"unit": "Mr ₺"}),
            _block(f"kpi_gr_{sfx}", "kpi", "Büyüme (%)",
                   f"{base} SELECT ROUND((g1.tb/NULLIF(g0.tb,0) - 1)*100, 2) "
                   f"FROM g0 CROSS JOIN g1",
                   all_vars, b, T, width="1/3", config={"unit": "%"}),
        ]

    def widgets(T, dcol, sfx, label):
        return kpis(T, dcol, sfx) + [
            _block(f"bridge_{sfx}", "waterfall_chart",
                   f"Balance Bridge (₺Mr, {label})",
                   sql_balance_bridge(T, dcol, W, "DIM_PRODUCT"),
                   all_vars, b, T, width="full", config={"unit": "Mr ₺"}),
            _block(f"hm_bal_{sfx}", "heatmap",
                   f"Balance Heatmap — Segment × AUM (₺Mr, {label}, t₁)",
                   sql_heatmap_t1(T, dcol, W, "DIM_SEGMENT", "DIM_AUM", "bal"),
                   all_vars, b, T),
            _block(f"hm_cust_{sfx}", "heatmap",
                   f"Customer Heatmap — Segment × AUM (adet, {label}, t₁)",
                   sql_heatmap_t1(T, dcol, W, "DIM_SEGMENT", "DIM_AUM", "cust"),
                   all_vars, b, T),
            _block(f"mix_{sfx}", "bar_chart",
                   f"Composition Evolution by Ürün (%, {label})",
                   sql_mix(T, dcol, W, "DIM_PRODUCT", products),
                   all_vars, b, T,
                   config={"stacked": True, "horizontal": True}),
            _block(f"mixd_{sfx}", "bar_chart",
                   f"Composition Δ (puan, {label})",
                   sql_mix_delta(T, dcol, W, "DIM_PRODUCT"),
                   all_vars, b, T),
        ]

    manifest = _manifest_shell(
        "p_dep_balance", "Outstanding Balance Analysis",
        "Mevduat hacim analizi — KPI şeridi, Balance Bridge, Balance/Customer "
        "heatmap çifti, Composition Evolution + Δ (Monthly + Daily). Heatmap "
        "Δ-modu ve metrik slider'ı yerine iki heatmap yan yana verildi.",
        [f_don, f_seg, f_prd, f_aum],
        [_section("sec_monthly", "Monthly Averages", widgets(M, "MONTH", "mon", "aylık")),
         _section("sec_daily", "Daily Evolution", widgets(D, "DAT", "dly", "günlük"))],
        [M, D])
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 3 — Outstanding Tenor Analysis (Monthly + Daily)
# ════════════════════════════════════════════════════════════════════════════

def build_tenor(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_TENOR_MONTHLY", f"{sch}.PRISMA_DEP_TENOR_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_mod = _filter("f_mod", "other", "enum_single", "Vade Modu (TENOR ↔ DTM)",
                    "tenor", ["tenor", "dtm"])
    v_mod = _var("mod", "other", "enum_single", "tenor", ["tenor", "dtm"])
    b["mod"] = "f_mod"
    lo, _ = runner.minmax_date(M, "MONTH")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem (t₀ → t₁)",
                    {"from": lo, "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": "today"})
    b["donem"] = "f_donem"
    all_vars = [v_don, v_seg, v_mod]
    W = ("TENOR_MODE = :mod AND DIM_SEGMENT IN (:segment) "
         "AND DIM_BUCKET IS NOT NULL")
    ORD = "TO_NUMBER(REGEXP_SUBSTR(p, '^\\d+'))"
    buckets = [x for x in runner.distinct(M, "DIM_BUCKET") if x]
    buckets.sort(key=lambda s: int(s.split("-")[0]) if s.split("-")[0].isdigit() else 9999)

    def snap(T, dcol):
        return _snap_cte(T, dcol, W, "DIM_BUCKET", extra_measures=", SUM(WT_SUM) wt")

    def wat_kpis(T, dcol, sfx):
        base = _snap_cte(T, dcol, W, "DIM_BUCKET", extra_measures=", SUM(WT_SUM) wt")
        # g0/g1 wt içermiyor — WAT için s0/s1'den topla.
        return [
            _block(f"kpi_wat0_{sfx}", "kpi", "WAT t₀ (gün)",
                   f"{base} SELECT ROUND(SUM(wt)/NULLIF(SUM(b),0), 1) FROM s0",
                   all_vars, b, T, width="1/3", config={"unit": "gün"}),
            _block(f"kpi_wat1_{sfx}", "kpi", "WAT t₁ (gün)",
                   f"{base} SELECT ROUND(SUM(wt)/NULLIF(SUM(b),0), 1) FROM s1",
                   all_vars, b, T, width="1/3", config={"unit": "gün"}),
            _block(f"kpi_watd_{sfx}", "kpi", "Δ WAT (gün)",
                   f"{base} SELECT ROUND((SELECT SUM(wt)/NULLIF(SUM(b),0) FROM s1) - "
                   f"(SELECT SUM(wt)/NULLIF(SUM(b),0) FROM s0), 1) FROM dual",
                   all_vars, b, T, width="1/3", config={"unit": "gün"}),
        ]

    def ladder(T, dcol, sfx, label):
        return _block(f"ladder_{sfx}", "bar_chart",
                      f"Maturity Ladder — t₀ vs t₁ (₺Mr, {label})",
                      f"""{snap(T, dcol)},
j AS (SELECT NVL(s1.p, s0.p) p, NVL(s0.b,0)/1e9 b0, NVL(s1.b,0)/1e9 b1
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND(b0,2) AS "t₀ (₺Mr)", ROUND(b1,2) AS "t₁ (₺Mr)"
FROM j ORDER BY {ORD}""",
                      all_vars, b, T, width="full")

    def ladder_delta(T, dcol, sfx, label):
        return _block(f"ladderd_{sfx}", "bar_chart",
                      f"Balance Change per Bucket (₺Mr, {label})",
                      f"""{snap(T, dcol)},
j AS (SELECT NVL(s1.p, s0.p) p, (NVL(s1.b,0)-NVL(s0.b,0))/1e9 d
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND(d,2) AS DELTA_MLR FROM j ORDER BY {ORD}""",
                      all_vars, b, T)

    def term_structure(T, dcol, sfx, label):
        # combo: col1 Δbps (bar/sağ), col2 r0 (line/sol), col3 r1 (line/sol)
        return _block(f"curve_{sfx}", "combo_chart",
                      f"Term Structure — Rate per Bucket ({label})",
                      f"""{snap(T, dcol)},
j AS (SELECT NVL(s1.p, s0.p) p,
             NVL(s0.wr,0)/NULLIF(s0.b,0)*100 r0, NVL(s1.wr,0)/NULLIF(s1.b,0)*100 r1
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND((r1-r0)*100, 1) AS "Δ (bps)",
       ROUND(r0, 2) AS "Faiz t₀ (%)", ROUND(r1, 2) AS "Faiz t₁ (%)"
FROM j ORDER BY {ORD}""",
                      all_vars, b, T, width="full",
                      config={"left_axis_title": "Faiz (%)",
                              "right_axis_title": "Δ (bps)",
                              "series": [
                                  {"name": "Δ (bps)", "kind": "bar", "axis": "right"},
                                  {"name": "Faiz t₀ (%)", "kind": "line", "axis": "left"},
                                  {"name": "Faiz t₁ (%)", "kind": "line", "axis": "left"},
                              ]})

    def widgets(T, dcol, sfx, label):
        out = wat_kpis(T, dcol, sfx)
        out += [ladder(T, dcol, sfx, label), ladder_delta(T, dcol, sfx, label)]
        out.append(term_structure(T, dcol, sfx, label))
        out += [
            _block(f"mix_{sfx}", "bar_chart",
                   f"Bucket Composition (% of Total, {label})",
                   sql_mix(T, dcol, W, "DIM_BUCKET", buckets),
                   all_vars, b, T, config={"stacked": True, "horizontal": True}),
            _block(f"mixd_{sfx}", "bar_chart",
                   f"Composition Δ (puan, {label})",
                   sql_mix_delta(T, dcol, W, "DIM_BUCKET"),
                   all_vars, b, T),
            _block(f"wf_rate_{sfx}", "waterfall_chart",
                   f"Bucket Rate Waterfall ({label})",
                   sql_rate_waterfall(T, dcol, W, "DIM_BUCKET"),
                   all_vars, b, T, width="full", config={"unit": "%"}),
        ]
        return out

    # Günlük ekstralar: per-bucket rate evolution + WAT trend
    bucket_rate_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN DIM_BUCKET = '{bk}' THEN WR_SUM ELSE 0 END)"
        f"/NULLIF(SUM(CASE WHEN DIM_BUCKET = '{bk}' THEN BALANCE ELSE 0 END),0)*100, 2)"
        f" AS \"{bk}\"" for bk in buckets)
    daily_extra = [
        _block("rate_evo_dly", "line_chart",
               "Per-Bucket Rate Evolution (%, günlük)",
               f"""SELECT DAT, {bucket_rate_cases}
FROM {D} WHERE {W} AND DAT BETWEEN :donem_from AND :donem_to
GROUP BY DAT ORDER BY DAT""",
               all_vars, b, D, width="full"),
        _block("wat_trend_dly", "line_chart",
               "WAT Trend (gün, günlük)",
               f"""SELECT DAT, ROUND(SUM(WT_SUM)/NULLIF(SUM(BALANCE),0), 1) AS WAT_GUN
FROM {D} WHERE {W} AND DAT BETWEEN :donem_from AND :donem_to
GROUP BY DAT ORDER BY DAT""",
               all_vars, b, D),
    ]

    manifest = _manifest_shell(
        "p_dep_tenor", "Outstanding Tenor Analysis",
        "Vade analizi — WAT KPI'ları, Maturity Ladder (t₀/t₁) + Δ, Term "
        "Structure (çift eksen), Bucket Composition + Δ, Bucket Rate "
        "Waterfall; günlükte Per-Bucket Rate Evolution + WAT trend. TENOR↔DTM "
        "modu filtre. Swap hedge overlay kapsam dışı.",
        [f_don, f_seg, f_mod],
        [_section("sec_monthly", "Monthly Averages", widgets(M, "MONTH", "mon", "aylık")),
         _section("sec_daily", "Daily Evolution",
                  widgets(D, "DAT", "dly", "günlük") + daily_extra)],
        [M, D])
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 4 — Future Deposit Rollings
# ════════════════════════════════════════════════════════════════════════════

ROLL_BANDS = ["0-5M", "5M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+"]
ROLL_SEGMENTS = ["NPO", "Tüzel", "Private", "Affluent", "Maaşlı", "Diğer"]


def build_rollings(runner, sch):
    A, T = f"{sch}.PRISMA_DEP_ROLL_AGG", f"{sch}.PRISMA_DEP_ROLL_DETAIL"
    b = {}
    f_ccy, v_ccy = _enum_domain(runner, A, "CCY_CODE", "Para Birimi", "f_ccy", "currency", b)
    lo, hi = runner.minmax_date(A, "ROLL_DATE")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönüş Aralığı",
                    {"from": lo, "to": hi})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": hi})
    b["donem"] = "f_donem"
    vars_a = [v_don, v_ccy]

    R = "ROLL_DATE BETWEEN :donem_from AND :donem_to AND CCY_CODE IN (:ccy)"
    band_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN AUM_BAND = '{bd}' THEN TRY_BAKIYE_TOPLAM ELSE 0 END)/1e6, 1)"
        f" AS \"{bd}\"" for bd in ROLL_BANDS)
    seg_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN SEGMENT = '{sg}' THEN TRY_BALANCE ELSE 0 END)/1e6, 1)"
        f" AS \"{sg}\"" for sg in ROLL_SEGMENTS)

    blocks_s1 = [
        _block("grid_all", "data_table", "Weekly Rollings (mio TRY — TRY + FX)",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN, CURRENCY,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6, 1) AS TOPLAM
FROM {A} WHERE {R}
GROUP BY ROLL_DATE, CURRENCY ORDER BY ROLL_DATE, CURRENCY""",
               vars_a, b, A, width="full",
               config=_tbl_cols("GUN", "CURRENCY", *ROLL_BANDS, "TOPLAM")),
        _block("grid_g", "data_table",
               "TRY Standart Vadeli Dönüşler (mio TRY) — Gerçek",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6, 1) AS TOPLAM
FROM {A} WHERE {R} AND CUST_TP = 'G' AND CURRENCY = 'TRY'
GROUP BY ROLL_DATE ORDER BY ROLL_DATE""",
               vars_a, b, A, width="1/2",
               config=_tbl_cols("GUN", *ROLL_BANDS, "TOPLAM")),
        _block("grid_t", "data_table",
               "TRY Standart Vadeli Dönüşler (mio TRY) — Tüzel",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6, 1) AS TOPLAM
FROM {A} WHERE {R} AND CUST_TP = 'T' AND CURRENCY = 'TRY'
GROUP BY ROLL_DATE ORDER BY ROLL_DATE""",
               vars_a, b, A, width="1/2",
               config=_tbl_cols("GUN", *ROLL_BANDS, "TOPLAM")),
        _block("dtm_hist", "bar_chart", "Vade Bucket Dağılımı (mio TRY)",
               f"""SELECT CASE
  WHEN DTM <= 30 THEN '0-30' WHEN DTM <= 60 THEN '31-60'
  WHEN DTM <= 90 THEN '61-90' WHEN DTM <= 180 THEN '91-180'
  WHEN DTM <= 365 THEN '181-365' ELSE '366+' END AS VADE_GUN,
ROUND(SUM(TRY_BALANCE)/1e6, 1) AS BAKIYE_M
FROM {T} WHERE {R}
GROUP BY CASE WHEN DTM <= 30 THEN '0-30' WHEN DTM <= 60 THEN '31-60'
  WHEN DTM <= 90 THEN '61-90' WHEN DTM <= 180 THEN '91-180'
  WHEN DTM <= 365 THEN '181-365' ELSE '366+' END
ORDER BY MIN(DTM)""",
               vars_a, b, T),
    ]
    blocks_s2 = [
        _block("seg_donut", "pie_chart", "Segment Dağılımı — Dönem Geneli (mio TRY)",
               f"""SELECT SEGMENT, ROUND(SUM(TRY_BALANCE)/1e6, 1) AS M
FROM {T} WHERE {R} GROUP BY SEGMENT ORDER BY 2 DESC""",
               vars_a, b, T, config={"donut": True}),
        _block("seg_stack", "bar_chart", "Tarih × Segment (mio TRY)",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM') AS GUN, {seg_cases}
FROM {T} WHERE {R}
GROUP BY ROLL_DATE ORDER BY ROLL_DATE""",
               vars_a, b, T, width="full", config={"stacked": True}),
        _block("cust_grid", "data_table", "Müşteri Listesi — En Büyük 50 (maskeli)",
               f"""SELECT FULL_NM AS MUSTERI, SEGMENT, CCY_CODE,
ROUND(TRY_BALANCE/1e6, 2) AS BAKIYE_M, ROUND(INTRST_RT, 2) AS FAIZ,
DTM AS KALAN_GUN, TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS DONUS
FROM {T} WHERE {R}
ORDER BY TRY_BALANCE DESC FETCH FIRST 50 ROWS ONLY""",
               vars_a, b, T, width="full",
               config=_tbl_cols("MUSTERI", "SEGMENT", "CCY_CODE", "BAKIYE_M",
                                "FAIZ", "KALAN_GUN", "DONUS")),
    ]
    manifest = _manifest_shell(
        "p_dep_rollings", "Future Deposit Rollings",
        "Vadesi dolan mevduat dönüşleri — 3 pivot tablo (TRY+FX / Gerçek / "
        "Tüzel), DTM histogramı, segment donut, tarih×segment istifli bar, "
        "müşteri listesi (KVKK maskeli). Pencere deposits_pipeline koşusuyla "
        "belirlenir. Grid hücre-drill'i ve HHI başlığı kapsam dışı.",
        [f_don, f_ccy],
        [_section("sec_tables", "Mevduat Dönüş Raporu", blocks_s1),
         _section("sec_segments", "Müşteri Segmenti & Top Müşteri", blocks_s2)],
        [A, T])
    return manifest, [A, T]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 5 — New Business: Volume & Pricing
# ════════════════════════════════════════════════════════════════════════════

NP_COARSE_AUM = [
    ("0-1M", ["0-1M"]), ("1M-5M", ["1M-2M", "2M-5M"]), ("5M-10M", ["5M-10M"]),
    ("10M-25M", ["10M-25M"]), ("25M-50M", ["25M-50M"]),
    ("50M-100M", ["50M-100M"]), ("100M-200M", ["100M-200M"]),
    ("200M+", ["200M-500M", "500M-1B", "1B+"]),
]


def build_newbiz(runner, sch):
    N = f"{sch}.PRISMA_NP_FLOW_DAILY"
    b = {}
    f_ccy, v_ccy = _enum_domain(runner, N, "CCY_CODE", "Para Birimi", "f_ccy", "currency", b)
    f_seg, v_seg = _enum_domain(runner, N, "SUB_SEGMENT", "Alt Segment", "f_segment", "segment", b)
    f_ten, v_ten = _enum_domain(runner, N, "TENOR_GRP", "Vade Grubu", "f_vade", "tenor_bucket", b)
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem",
                    {"from": "today - 90d", "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range",
                 {"from": "today - 90d", "to": "today"})
    b["donem"] = "f_donem"
    all_vars = [v_don, v_ccy, v_seg, v_ten]

    ENUMS = ("CCY_CODE IN (:ccy) AND SUB_SEGMENT IN (:segment) "
             "AND TENOR_GRP IN (:vade)")
    CUR = f"DAT BETWEEN :donem_from AND :donem_to AND {ENUMS}"
    # Önceki eş-uzunluk pencere (bubble'ların t₀ penceresi).
    PRV = (f"DAT BETWEEN :donem_from - (:donem_to - :donem_from) - 1 "
           f"AND :donem_from - 1 AND {ENUMS}")

    def bubble(mode):
        x = ("(NVL(c.v,0) - NVL(p.v,0))" if mode == "bal"
             else "(NVL(c.wc,0)/NULLIF(c.v,0) - NVL(p.wc,0)/NULLIF(p.v,0))*100")
        return f"""WITH cur AS (
  SELECT SUB_SEGMENT g, SUM(NP_HACIM) v, SUM(WC_SUM) wc FROM {N}
  WHERE {CUR} GROUP BY SUB_SEGMENT),
prv AS (
  SELECT SUB_SEGMENT g, SUM(NP_HACIM) v, SUM(WC_SUM) wc FROM {N}
  WHERE {PRV} GROUP BY SUB_SEGMENT)
SELECT NVL(c.g, p.g) AS AD, ROUND({x}, 2) AS X,
       ROUND(NVL(c.wc,0)/NULLIF(c.v,0), 2) AS FAIZ_PCT,
       ROUND(NVL(c.v,0), 1) AS HACIM_M
FROM cur c FULL OUTER JOIN prv p ON c.g = p.g
WHERE NVL(c.v,0) > 0 ORDER BY 4 DESC"""

    aum_case = "CASE " + " ".join(
        f"WHEN AUM_BAND IN ({_quote_list(fine)}) THEN '{coarse}'"
        for coarse, fine in NP_COARSE_AUM) + " ELSE 'Bilinmiyor' END"
    aum_rate_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN {aum_case} = '{coarse}' THEN WC_SUM ELSE 0 END)"
        f"/NULLIF(SUM(CASE WHEN {aum_case} = '{coarse}' THEN NP_HACIM ELSE 0 END),0), 2)"
        f" AS \"{coarse}\"" for coarse, _f in NP_COARSE_AUM)

    combo_series = ([{"name": "Hacim (₺M)", "kind": "bar", "axis": "left"}] +
                    [{"name": c, "kind": "line", "axis": "right"}
                     for c, _f in NP_COARSE_AUM])

    blocks = [
        _block("bub_vol", "scatter_chart", "Bubble — Δ Hacim × Faiz (önceki pencereye göre)",
               bubble("bal"), all_vars, b, N,
               config={"x_title": "Δ Hacim (₺M)", "y_title": "Bileşik Faiz (%)"}),
        _block("bub_rate", "scatter_chart", "Bubble — Δ Faiz × Faiz (bps, önceki pencereye göre)",
               bubble("rate"), all_vars, b, N,
               config={"x_title": "Δ Faiz (bps)", "y_title": "Bileşik Faiz (%)"}),
        _block("hm_rv", "heatmap", "Rate × Volume Heatmap — Segment × AUM (bileşik %)",
               f"""SELECT SUB_SEGMENT AS SATIR, {aum_case} AS KOLON,
ROUND(SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0), 2) AS FAIZ_PCT
FROM {N} WHERE {CUR}
GROUP BY SUB_SEGMENT, {aum_case} ORDER BY 1, 2""",
               all_vars, b, N, width="full"),
        _block("aum_combo", "combo_chart", "New Business Volume & Interest Rate (AUM bantları)",
               f"""SELECT DAT, ROUND(SUM(NP_HACIM), 1) AS "Hacim (₺M)", {aum_rate_cases}
FROM {N} WHERE {CUR} GROUP BY DAT ORDER BY DAT""",
               all_vars, b, N, width="full",
               config={"left_axis_title": "Hacim (₺M)",
                       "right_axis_title": "Bileşik Faiz (%)",
                       "series": combo_series}),
        _block("conc_curve", "line_chart",
               "Faiz × Kümülatif Hacim Eğrisi (konsantrasyon)",
               f"""WITH cells AS (
  SELECT SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0) r, SUM(NP_HACIM) v
  FROM {N} WHERE {CUR}
  GROUP BY SUB_SEGMENT, AUM_BAND, TENOR_GRP
  HAVING SUM(NP_HACIM) > 0)
SELECT TO_CHAR(ROUND(100 * SUM(v) OVER (ORDER BY r) / SUM(v) OVER (), 1)) AS KUM_PAY_PCT,
       ROUND(r, 2) AS FAIZ_PCT
FROM cells ORDER BY r""",
               all_vars, b, N, width="full"),
    ]
    manifest = _manifest_shell(
        "p_dep_newbiz", "New Business — Volume & Pricing",
        "Bağlanan mevduat akışı — 2 bubble (mevcut pencere vs önceki "
        "eş-uzunluk pencere), Rate×Volume heatmap (bileşik %), AUM bantlı "
        "dual-axis combo, konsantrasyon eğrisi. Heatmap hücre-hover combo'su, "
        "drill ve min-bubble slider'ı kapsam dışı; Weekly/Daily seçimi günlük "
        "sabittir.",
        [f_don, f_ccy, f_seg, f_ten],
        [_section("sec_bubbles", "Bubble Analysis", blocks[:2]),
         _section("sec_pricing", "Fiyatlama ve Hacim", blocks[2:])],
        [N])
    return manifest, [N]


BUILDERS = {
    "p_dep_cost": build_cost,
    "p_dep_balance": build_balance,
    "p_dep_tenor": build_tenor,
    "p_dep_rollings": build_rollings,
    "p_dep_newbiz": build_newbiz,
}


# ════════════════════════════════════════════════════════════════════════════
# Config doldurma + scope + persist
# ════════════════════════════════════════════════════════════════════════════

def iter_leaf_blocks(manifest):
    """Carousel/canvas container'larının içine inerek yalnız leaf blokları
    döndürür (Cost sayfasındaki waterfall slaytları da doldurulsun)."""
    def _walk(blocks):
        for blk in blocks or []:
            if blk.get("type") in ("section_header", "carousel", "canvas"):
                yield from _walk(blk.get("children"))
            else:
                yield blk
    yield from _walk(manifest.get("blocks"))


def fill_block_configs(runner, manifest) -> list[str]:
    """Her bloğun SQL'ini sistemin kendi resolver/binder'ıyla bir kez koşup
    config + data_source doldur. Hata veren blok boş config'le kalır."""
    from presentations.blocks.schema import Block
    from presentations.variables.resolver import resolve_variables
    from presentations.sql.binder import expand_binds
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    from presentations import duck

    errors = []
    for blk in iter_leaf_blocks(manifest):
        if not blk.get("query"):
            continue
        try:
            stand_in = Block.model_validate({
                "id": blk["id"] if len(blk["id"]) >= 3 else f"blk_{blk['id']}",
                "version": 1,
                "title": blk.get("title") or "block",
                "team": "in_presentation",
                "owner": manifest.get("owner_id", "pipeline"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "query": blk["query"],
                "variables": blk.get("variables") or [],
                "visualization": {"type": "table", "config": {}},
            })
            resolved = resolve_variables(stand_in)
            bound = expand_binds(stand_in, resolved)
            df = runner.query(bound.sql, bound.params)
            rows = [[duck._jsonable(v) for v in row]
                    for row in df.itertuples(index=False, name=None)]
            blk["data_source"] = {
                "sql": blk["query"], "original_sql": blk["query"],
                "rewritten": False, "truncated": False,
                "cap": len(rows), "reason": "import_fill",
                "executed_at": datetime.now(timezone.utc)
                .replace(microsecond=0).isoformat(),
                "row_count": len(rows),
                "columns": [str(c) for c in df.columns],
                "preview_rows": rows[:5], "rows": rows,
                "view_name": f"v_{blk['id']}", "engine": "import_fill",
            }
            apply_data_to_config(blk, blk["data_source"])
            log.info("   ✓ %s: %d satır", blk["id"], len(rows))
        except Exception as exc:
            errors.append(f"{manifest['id']}/{blk['id']}: {exc}")
            log.error("   ✗ %s doldurulamadı: %s", blk["id"], exc)
    return errors


def build_scope(runner, dc, manifest, sicil, tables) -> None:
    from presentations.scope.schema import (
        ScopeContract, BasketItem, TableRef, Routing,
    )
    from presentations.scope.store import S3ScopeStore

    items = []
    for t in tables:
        sch, name = t.split(".")
        try:
            est = runner.rowcount(t) * 200
        except Exception:
            est = 1_000_000
        items.append(BasketItem(
            table_ref=TableRef(schema=sch, name=name),
            alias=name.lower(),
            routing=Routing(decision="lazy", decided_by="system",
                            estimated_bytes=int(est),
                            estimate_source="deposits_dashboards import"),
        ))
    scope = ScopeContract(
        presentation_id=manifest["id"], version=1, created_by=sicil,
        created_at=datetime.now(timezone.utc), basket=items,
    )
    version = S3ScopeStore(dc).save(scope)
    manifest["scope_ref"] = {"presentation_id": manifest["id"],
                             "scope_version": version}
    log.info("   ✓ scope v%s kaydedildi (%d tablo, lazy)", version, len(items))


def persist_manifest(dc, sicil, manifest) -> None:
    from presentations.manifest import validate_manifest

    errs = validate_manifest(manifest)
    if errs:
        raise RuntimeError(f"{manifest['id']} manifest doğrulaması: {errs}")
    key = S3_MANIFEST_KEY.format(sicil=sicil, pid=manifest["id"])
    body = json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    dc._upload_bytes(key, body, content_type="application/json")
    log.info("   ✓ manifest yazıldı: s3://%s (v%s, %d blok, %d filtre)",
             key, manifest["version"],
             sum(1 for _ in iter_leaf_blocks(manifest)), len(manifest["filters"]))


# ════════════════════════════════════════════════════════════════════════════
# main
# ════════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sicil", required=True,
                   help="SİTEYE GİRİŞ YAPTIĞIN sicil (örn. A63837) — sunum "
                        "listesi/editör bu kullanıcının klasörünü okur. DB "
                        "bağlantı kullanıcısı (örn. A63837PY) DEĞİL; tablo "
                        "şeması ondan bağımsız --schema/bağlantıdan gelir.")
    p.add_argument("--schema", default=None,
                   help="PRISMA_* tablolarının şeması (varsayılan: bağlantı kullanıcısı)")
    p.add_argument("--only", default=None, help="Virgülle ayrılmış pid alt kümesi")
    p.add_argument("--experts", default="dep",
                   help="bound_experts uzman id'leri (virgüllü; boş → bağlama yok)")
    p.add_argument("--skip-fill", action="store_true",
                   help="Blok SQL'lerini koşma (config'ler boş kalır)")
    args = p.parse_args(argv)

    # Bekçi: hangi presentations paketi yüklendi? Script başka bir dizine
    # kopyalanıp oradaki ESKİ repo kopyasıyla koşarsa (Desktop\prisma vakası)
    # manifest doğrulaması anlaşılmaz hatalar üretir — erken ve net patla.
    import presentations
    from presentations.manifest import LEAF_BLOCK_TYPES
    log.info("presentations paketi: %s", presentations.__file__)
    if "waterfall_chart" not in LEAF_BLOCK_TYPES:
        raise SystemExit(
            f"ESKİ presentations paketi yüklendi: {presentations.__file__}\n"
            "Bu sürümde waterfall_chart/scatter_chart tanımlı değil — script'i "
            "güncel repo kökünden çalıştır (git pull sonrası) ve eski repo "
            "kopyalarını (örn. Desktop\\prisma) kaldır.")

    from DataClient import DataClient
    dc = DataClient()
    con = dc.get_connection()
    all_errors: list[str] = []
    try:
        sch = (args.schema or con.username).upper()
        runner = Runner(dc, con)
        experts = [e.strip() for e in (args.experts or "").split(",") if e.strip()]

        selected = {t.strip() for t in args.only.split(",")} if args.only else set(BUILDERS)
        unknown = selected - set(BUILDERS)
        if unknown:
            raise SystemExit(f"--only bilinmeyen pid: {sorted(unknown)}")

        for pid, builder in BUILDERS.items():
            if pid not in selected:
                continue
            log.info("══ %s kuruluyor…", pid)
            manifest, tables = builder(runner, sch)
            manifest["owner_id"] = args.sicil
            manifest["bound_experts"] = experts
            if not args.skip_fill:
                all_errors += fill_block_configs(runner, manifest)
            try:
                build_scope(runner, dc, manifest, args.sicil, tables)
            except Exception as exc:
                log.error("   ✗ scope kaydedilemedi (%s): %s — manifest scope'suz "
                          "yazılıyor", pid, exc)
                all_errors.append(f"{pid}/scope: {exc}")
            persist_manifest(dc, args.sicil, manifest)
    finally:
        dc.drop_connection(con)

    if all_errors:
        log.warning("Tamamlandı — %d blok/scope hatası: %s",
                    len(all_errors), "; ".join(all_errors[:5]))
        return 1
    log.info("Bitti — 5 dashboard S3'te. Sunum listesinden açabilirsin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

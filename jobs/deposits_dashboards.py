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

Kalan 4 sayfa da kaynakla hizalandı (bu revizyonun kapsamı):
  * Balance: 4'lü KPI şeridi (₺M), Kırılım/2.Kırılım filtreleri (kaynak
    Decomposition/Second Dec. Dim select'leri), Top-8 |Δ| + Other bridge,
    Balance+Customer heatmap carousel'leri (Δ varsayılan ↔ t₁), Composition.
  * Tenor: bucket Bennet waterfall carousel'i (kesmesiz), ladder/Δ ₺M
    (₺Mr değildi), Term Structure, TENOR↔DTM, tüm boyut filtreleri.
  * Rollings: DTM histogramı kaynak kovaları (≤14/15-32/33-90/91-180/180+),
    ≥100 mio müşteri listesi + türetilmiş kolonlar, tam-sayı pivotlar.
  * NewBiz: bileşik→basit geri çevrim (POWER), Balance X = outstanding Δ
    (AS-OF), D/W pencere frekansı, kanal×ortak-AUM heatmap carousel'i,
    6'lı kaba AUM combo binleri, WAvg çizgili bubble'lar (TRY).
  * AUM/vade bantları HER YERDE sayısal alt sınıra göre sıralı
    (K/M/B çarpanlı — band_order_expr / _band_sort_key).

Cost Analysis sayfası kaynakla BİREBİR (önceki revizyon):
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
import re
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


def _section(sid, title, children, page=None):
    sec = {"id": sid, "type": "section_header", "title": title,
           "config": {}, "children": children}
    if page:
        sec["page"] = page
    return sec


def _band_sort_key(label) -> float:
    """Kaynak _aum_numeric_key'in genelleştirilmiş portu: etiketteki İLK
    sayı × K/M/B çarpanı (AUM_500K_1M → 500000, AUM_1M_5M → 1000000,
    '0-30' → 0). Sayısız etiket → inf (sona)."""
    s = str(label or "").strip()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([KMB])?", s, re.IGNORECASE)
    if not m:
        return float("inf")
    val = float(m.group(1).replace(",", "."))
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1.0)
    return val * mult


def band_order_expr(col: str) -> str:
    """_band_sort_key'in Oracle karşılığı — ORDER BY ifadesi. Sayı içermeyen
    etiketler NULL döner (NULLS LAST + alfabetik tie-break çağıranda)."""
    return (
        f"TO_NUMBER(REPLACE(REGEXP_SUBSTR({col}, '\\d+([.,]\\d+)?'), ',', '.'))"
        f" * CASE UPPER(REGEXP_SUBSTR({col}, '\\d+([.,]\\d+)?\\s*([KMB])', 1, 1, 'i', 2))"
        f" WHEN 'K' THEN 1000 WHEN 'M' THEN 1000000 WHEN 'B' THEN 1000000000"
        f" ELSE 1 END"
    )


def _enum_domain(runner, table, col, label, fid, tag, bindings_out,
                 band_order=False):
    values = runner.distinct(table, col)
    if band_order:
        # AUM/vade bantları küçükten büyüğe (alfabetik değil) listelensin.
        values = sorted(values, key=lambda v: (_band_sort_key(v), str(v)))
    filt = _filter(fid, tag, "enum_multi", label, values, values)
    var = _var(fid.removeprefix("f_"), tag, "enum_multi", values, values)
    bindings_out[var["name"]] = fid
    return filt, var


def _quote_list(values):
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


# ════════════════════════════════════════════════════════════════════════════
# Ortak SQL kalıpları — iki-snapshot (t₀ = as-of :donem_from, t₁ = :donem_to)
# ════════════════════════════════════════════════════════════════════════════

def _snap_cte(table, dcol, enum_where, dim, extra_measures="", dv="donem"):
    """f/t0/t1/s0/s1/g0/g1 CTE zinciri. s* = dim bazında BALANCE+WR_SUM
    (istenirse ek ölçüler), g* = toplamlar. dv = date_range değişken adı."""
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
s0 AS (SELECT {dim} p, SUM(BALANCE) b, SUM(WR_SUM) wr{extra_measures}
       FROM f, t0 WHERE f.{dcol} = t0.m GROUP BY {dim}),
s1 AS (SELECT {dim} p, SUM(BALANCE) b, SUM(WR_SUM) wr{extra_measures}
       FROM f, t1 WHERE f.{dcol} = t1.m GROUP BY {dim}),
g0 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s0),
g1 AS (SELECT SUM(b) tb, SUM(wr) twr FROM s1)"""





def sql_mix(table, dcol, enum_where, dim, members, dv="donem"):
    """Composition Evolution: 2 satır (Başlangıç/Bitiş) × üye payları (%)."""
    cases0 = ", ".join(
        f"ROUND(SUM(CASE WHEN p = '{m}' THEN b ELSE 0 END)/NULLIF(SUM(b),0)*100, 2) AS \"{m}\""
        for m in members)
    return f"""{_snap_cte(table, dcol, enum_where, dim, dv=dv)}
SELECT 'Başlangıç' AS DONEM, {cases0} FROM s0
UNION ALL
SELECT 'Bitiş', {cases0} FROM s1"""


def sql_mix_delta(table, dcol, enum_where, dim, dv="donem"):
    """Composition Δ: üye başına pay farkı (puan)."""
    return f"""{_snap_cte(table, dcol, enum_where, dim, dv=dv)}
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

PAGES_MON_DLY = [{"id": "pg_mon", "title": "Monthly Averages"},
                 {"id": "pg_dly", "title": "Daily Evolution"}]

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


def ca_core(table, dcol, enum_where, dv, grp=None):
    """f→t0/t1→s0/s1→g0/g1→k→mm→eff→drv CTE zinciri. drv = grup başına
    b0/b1/r0/r1 + Bennet etkileri (%) + Δpay (puan). grp verilmezse
    :gruplama'lı composite anahtar (Cost); Tenor sabit DIM_BUCKET geçirir."""
    grp = grp or ca_grp_expr()
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


def ca_sql_wf1(table, dcol, enum_where, dv, grp=None):
    """Slayt 1 — Rate Waterfall (%): Mix vs Pricing (kaynak wf1)."""
    return f"""{ca_core(table, dcol, enum_where, dv, grp)}
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 1 ord, 'Start Rate' step, start_pct delta, 1 is_total FROM k
  UNION ALL
  SELECT 2, 'Mix / Interaction', (SELECT SUM(mix_pct) FROM drv), 0 FROM DUAL
  UNION ALL
  SELECT 3, 'Pricing (rate, detailed)', (SELECT SUM(price_pct) FROM drv), 0 FROM DUAL
  UNION ALL
  SELECT 4, 'End Rate', end_pct, 1 FROM k
) ORDER BY ord"""


def ca_sql_wf2(table, dcol, enum_where, dv, grp=None, top_n=CA_TOP_N):
    """Slayt 2 — Pricing Drivers (Top N + Other, %) (kaynak wf2).
    Bazal 'After Mix' = Start + Mix; üyeler |price_eff| sırasıyla."""
    return f"""{ca_core(table, dcol, enum_where, dv, grp)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(price_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'After Mix' step,
         start_pct + (SELECT SUM(mix_pct) FROM drv) delta, 1 is_total FROM k
  UNION ALL
  SELECT 1, rn, p, price_pct, 0 FROM rnk WHERE rn <= {top_n}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(price_pct) FROM rnk WHERE rn > {top_n}), 0
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {top_n})
  UNION ALL
  SELECT 3, 0, 'End Rate', end_pct, 1 FROM k
) ORDER BY ord, ord2"""


def ca_sql_wf2_bal(table, dcol, enum_where, dv, grp=None, top_n=CA_TOP_N):
    """Slayt 2 companion — Balance Growth (₺M), wf2 ile aynı üye sırası.
    Çapa kolonlara 0 (kaynakta None) → eksen hizası korunur."""
    return f"""{ca_core(table, dcol, enum_where, dv, grp)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(price_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA_M, 2) AS "Bakiye Degisimi (MTL)" FROM (
  SELECT 0 ord, 0 ord2, 'After Mix' step, 0 delta_m FROM DUAL
  UNION ALL
  SELECT 1, rn, p, (b1 - b0)/1e6 FROM rnk WHERE rn <= {top_n}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(b1 - b0)/1e6 FROM rnk WHERE rn > {top_n})
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {top_n})
  UNION ALL
  SELECT 3, 0, 'End Rate', 0 FROM DUAL
) ORDER BY ord, ord2"""


def ca_sql_wf4(table, dcol, enum_where, dv, grp=None, top_n=CA_TOP_N):
    """Slayt 3 — Mix Drivers (Top N + Other, %) (kaynak wf4).
    Üye değeri Δw·(r̄ − r̄toplam); Start Rate → After Mix köprüsü."""
    return f"""{ca_core(table, dcol, enum_where, dv, grp)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(mixdrv_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA, 2) AS DELTA_PCT, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'Start Rate' step, start_pct delta, 1 is_total FROM k
  UNION ALL
  SELECT 1, rn, p, mixdrv_pct, 0 FROM rnk WHERE rn <= {top_n}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(mixdrv_pct) FROM rnk WHERE rn > {top_n}), 0
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {top_n})
  UNION ALL
  SELECT 3, 0, 'After Mix',
         start_pct + (SELECT SUM(mix_pct) FROM drv), 1 FROM k
) ORDER BY ord, ord2"""


def ca_sql_wf4_weights(table, dcol, enum_where, dv, grp=None, top_n=CA_TOP_N):
    """Slayt 3 companion — Weight Changes (Δ pay, puan), wf4 sırasıyla."""
    return f"""{ca_core(table, dcol, enum_where, dv, grp)},
rnk AS (SELECT drv.*, ROW_NUMBER() OVER (ORDER BY ABS(mixdrv_pct) DESC, p) rn
        FROM drv)
SELECT STEP, ROUND(DELTA_W, 3) AS "Agirlik Degisimi (puan)" FROM (
  SELECT 0 ord, 0 ord2, 'Start Rate' step, 0 delta_w FROM DUAL
  UNION ALL
  SELECT 1, rn, p, dw_pct FROM rnk WHERE rn <= {top_n}
  UNION ALL
  SELECT 2, 0, 'Other Items',
         (SELECT SUM(dw_pct) FROM rnk WHERE rn > {top_n})
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {top_n})
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
ORDER BY s, {band_order_expr('a')} NULLS LAST, a"""


def _snapshot_date_filters(runner, M, D, bindings_out, mcol="MONTH", dcol="DAT"):
    """Aylık + günlük dönem filtreleri, kaynak varsayılanlarıyla: aylıkta son
    iki snapshot, günlükte son gün + önceki takvim Perşembesi."""
    mon = runner.dates(M, mcol)
    if not mon:
        raise RuntimeError(f"{M} boş — önce deposits_pipeline koşulmalı")
    m_from = mon[-2] if len(mon) >= 2 else mon[0]
    m_to = mon[-1]
    day = runner.dates(D, dcol) or mon
    d_to = day[-1]
    d_from = _prev_thursday(d_to)
    if d_from < day[0]:
        d_from = day[0]
    f_m = _filter("f_donem_ay", "as_of_time", "date_range",
                  "Dönem — Aylık (t₀ → t₁)", {"from": m_from, "to": m_to})
    v_m = _var("donem_ay", "as_of_time", "date_range",
               {"from": m_from, "to": m_to})
    f_d = _filter("f_donem_gun", "as_of_time", "date_range",
                  "Dönem — Günlük (t₀ → t₁)", {"from": d_from, "to": d_to})
    v_d = _var("donem_gun", "as_of_time", "date_range",
               {"from": d_from, "to": d_to})
    # Sayfa hiyerarşisi: aylık dönem filtresi Monthly sekmesinde, günlük
    # olan Daily sekmesinde görünür (ikisi aynı barda karışmasın).
    f_m["page"] = "pg_mon"
    f_d["page"] = "pg_dly"
    bindings_out["donem_ay"] = "f_donem_ay"
    bindings_out["donem_gun"] = "f_donem_gun"
    return f_m, v_m, f_d, v_d


def _carousel(cid, title, children):
    return {"id": cid, "type": "carousel", "title": title,
            "locked": False, "children": children}


def _canvas(cid, title, children):
    return {"id": cid, "type": "canvas", "title": title,
            "locked": False, "children": children}


def _manifest_shell(pid, title, description, filters, sections, tables,
                    pages=None):
    now = datetime.now(timezone.utc).isoformat()
    out = {
        "id": pid, "version": 1, "created_at": now, "updated_at": now,
        "meta": {"title": title, "eyebrow": "Deposits",
                 "date": date.today().strftime("%d.%m.%Y"),
                 "description": description},
        # duck_cache: PRISMA_* tabloları küçük, plot-hazır agregalardır —
        # apply-filters onları İLK kullanımda oturum DuckDB'sine çeker ve
        # blok SQL'lerini lokalde koşar (filtre değişimi = sıfır Oracle turu).
        "basket": [{"table": t, "alias": t.split(".")[-1].lower(),
                    "column_concepts": {}, "duck_cache": True}
                   for t in tables],
        "filters": filters,
        "blocks": sections,
        "uploads": [],
        "bound_experts": [],
    }
    if pages:
        # Sayfa hiyerarşisi: canvas üstünde sekmeler; section'lar `page`
        # alanıyla bağlanır, sayfa-kapsamlı filtreler yalnız kendi
        # sekmesinde görünür (aylık/günlük dönem karışıklığını bitirir).
        out["pages"] = pages
    return out


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
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b,
                                band_order=True)

    # Gruplama (kaynaktaki "Dimensions:" toggle'ları). Varsayılan = kaynak
    # varsayılanı: PRODUCT kapalı, SUBPRODUCT/CUSTOMER_TYPE/AUM/SEGMENT açık.
    dim_names = [d for d, _ in CA_DIMS]
    grp_default = ["SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
    f_grp = _filter("f_gruplama", "other", "enum_multi",
                    "Gruplama (Dimensions)", grp_default, dim_names)
    v_grp = _var("gruplama", "other", "enum_multi", grp_default, dim_names)
    b["gruplama"] = "f_gruplama"

    # t₀/t₁ varsayılanları kaynak dropdown'larıyla aynı. (Önceki sürümdeki
    # "MIN tarihten bugüne" varsayılanı, sitede görünen değerlerden çok farklı
    # deltalar üretiyordu — veri yanlış değil, t₀ yanlıştı.)
    f_don_m, v_don_m, f_don_d, v_don_d = _snapshot_date_filters(runner, M, D, b)

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
                  widgets(M, "MONTH", v_don_m, "donem_ay", "mon", "Monthly"),
                  page="pg_mon"),
         _section("sec_daily", "Daily Evolution",
                  widgets(D, "DAT", v_don_d, "donem_gun", "dly", "Daily"),
                  page="pg_dly")],
        [M, D], pages=PAGES_MON_DLY)
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 2 — Outstanding Balance Analysis (Monthly + Daily)
# Kaynak: BalanceAnalysisEngine / DailyBalanceEngine + _build_balance_payload
# (app.py L2560-2940). Birim ₺M; bridge Top-8 |Δ| + Other; heatmap'ler
# varsayılan Δ modunda (kaynak toggle default'u), rows=Kırılım, cols=2. Kırılım.
# ════════════════════════════════════════════════════════════════════════════

BA_TOP_N = 8   # kaynak BalanceAnalysisEngine.TOP_N

_DIM_CASE = ("CASE :{bind} WHEN 'PRODUCT' THEN DIM_PRODUCT "
             "WHEN 'SUBPRODUCT' THEN DIM_SUBPRODUCT "
             "WHEN 'CUSTOMER_TYPE' THEN DIM_CUSTOMER "
             "WHEN 'AUM' THEN DIM_AUM ELSE DIM_SEGMENT END")


def dim_case_expr(bind: str) -> str:
    """enum_single kırılım değişkenini DIM_* kolonuna çeviren ifade —
    kaynak sayfalardaki 'Decomposition Dim' select'inin SQL karşılığı."""
    return "NVL(" + _DIM_CASE.format(bind=bind) + ", '-')"


def ba_core(table, dcol, enum_where, dv, grp):
    """Balance snapshot çekirdeği: grup başına b0/b1 + toplamlar (₺M değil,
    ham TL — ölçek final select'te)."""
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
s0 AS (SELECT {grp} p, SUM(BALANCE) b, SUM(CUST_COUNT) cc
       FROM f, t0 WHERE f.{dcol} = t0.m GROUP BY {grp}),
s1 AS (SELECT {grp} p, SUM(BALANCE) b, SUM(CUST_COUNT) cc
       FROM f, t1 WHERE f.{dcol} = t1.m GROUP BY {grp}),
g0 AS (SELECT SUM(b) tb FROM s0),
g1 AS (SELECT SUM(b) tb FROM s1),
m AS (SELECT NVL(s0.p, s1.p) p, NVL(s0.b,0) b0, NVL(s1.b,0) b1,
             NVL(s0.cc,0) c0, NVL(s1.cc,0) c1
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)"""


def ba_sql_kpi(table, dcol, enum_where, dv, what):
    """what: t0 | t1 | delta | growth — kaynak KPI şeridi (₺M / %)."""
    core = f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
g0 AS (SELECT SUM(BALANCE) tb FROM f, t0 WHERE f.{dcol} = t0.m),
g1 AS (SELECT SUM(BALANCE) tb FROM f, t1 WHERE f.{dcol} = t1.m)"""
    sel = {
        "t0":     "SELECT ROUND(g0.tb/1e6) FROM g0",
        "t1":     "SELECT ROUND(g1.tb/1e6) FROM g1",
        "delta":  "SELECT ROUND((g1.tb - g0.tb)/1e6) FROM g0 CROSS JOIN g1",
        "growth": "SELECT ROUND((g1.tb/NULLIF(g0.tb,0) - 1)*100, 2) "
                  "FROM g0 CROSS JOIN g1",
    }[what]
    return f"{core}\n{sel}"


def ba_sql_bridge(table, dcol, enum_where, dv, grp):
    """Balance Bridge (₺M): Start + Top-8 |Δ| üye + Other + End (kaynak
    _build_balance_payload bridge'i)."""
    return f"""{ba_core(table, dcol, enum_where, dv, grp)},
rnk AS (SELECT m.*, ROW_NUMBER() OVER (ORDER BY ABS(b1 - b0) DESC, p) rn FROM m)
SELECT STEP, ROUND(DELTA_M) AS DELTA_M, IS_TOTAL FROM (
  SELECT 0 ord, 0 ord2, 'Start' step, g0.tb/1e6 delta_m, 1 is_total FROM g0
  UNION ALL
  SELECT 1, rn, p, (b1 - b0)/1e6, 0 FROM rnk WHERE rn <= {BA_TOP_N}
  UNION ALL
  SELECT 2, 0, 'Other', (SELECT SUM(b1 - b0)/1e6 FROM rnk WHERE rn > {BA_TOP_N}), 0
  FROM DUAL WHERE EXISTS (SELECT 1 FROM rnk WHERE rn > {BA_TOP_N})
  UNION ALL
  SELECT 3, 0, 'End', g1.tb/1e6, 1 FROM g1
) ORDER BY ord, ord2"""



def ba_sql_heatmap(table, dcol, enum_where, dv, grp_row, grp_col, measure, mode):
    """Kırılım × 2. Kırılım heatmap'i (kaynak growth/customer heatmap'leri).
    measure: 'bal' (₺M) | 'cust' (adet); mode: 'delta' (kaynak varsayılanı) |
    'abs' (t₁). Eksenler banda göre sıralı (AUM K/M çarpanlı alt sınır)."""
    m_col = "BALANCE" if measure == "bal" else "CUST_COUNT"
    scale = "/1e6" if measure == "bal" else ""
    val = (f"ROUND((NVL(a1.v,0) - NVL(a0.v,0)){scale}, 1)" if mode == "delta"
           else f"ROUND(NVL(a1.v,0){scale}, 1)")
    alias = ("DELTA_M" if measure == "bal" else "DELTA_ADET") if mode == "delta" \
        else ("BAKIYE_M" if measure == "bal" else "ADET")
    return f"""WITH f AS (SELECT * FROM {table} WHERE {enum_where}),
t0 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_from),
t1 AS (SELECT MAX({dcol}) m FROM f WHERE {dcol} <= :{dv}_to),
a0 AS (SELECT {grp_row} s, {grp_col} a, SUM({m_col}) v
       FROM f, t0 WHERE f.{dcol} = t0.m GROUP BY {grp_row}, {grp_col}),
a1 AS (SELECT {grp_row} s, {grp_col} a, SUM({m_col}) v
       FROM f, t1 WHERE f.{dcol} = t1.m GROUP BY {grp_row}, {grp_col})
SELECT NVL(a0.s, a1.s) AS SATIR, NVL(a0.a, a1.a) AS KOLON, {val} AS {alias}
FROM a0 FULL OUTER JOIN a1 ON a0.s = a1.s AND a0.a = a1.a
ORDER BY {band_order_expr('NVL(a0.s, a1.s)')} NULLS LAST, 1,
         {band_order_expr('NVL(a0.a, a1.a)')} NULLS LAST, 2"""


def ba_sql_mix(table, dcol, enum_where, dv, grp):
    """Composition — grup başına t₀/t₁ pay (%). Kategoriler t₁ bakiyesine
    göre azalan (kaynak sıralaması)."""
    return f"""{ba_core(table, dcol, enum_where, dv, grp)}
SELECT p AS GRUP,
       ROUND(b0/NULLIF(g0.tb,0)*100, 2) AS "Pay t0 (%)",
       ROUND(b1/NULLIF(g1.tb,0)*100, 2) AS "Pay t1 (%)"
FROM m CROSS JOIN g0 CROSS JOIN g1
ORDER BY b1 DESC"""


def ba_sql_mix_delta(table, dcol, enum_where, dv, grp):
    """Composition Change — grup başına Δ pay (puan), t₁ bakiye sırasıyla."""
    return f"""{ba_core(table, dcol, enum_where, dv, grp)}
SELECT p AS GRUP,
       ROUND(b1/NULLIF(g1.tb,0)*100 - b0/NULLIF(g0.tb,0)*100, 2) AS DELTA_PUAN
FROM m CROSS JOIN g0 CROSS JOIN g1
ORDER BY b1 DESC"""


def build_balance(runner, sch):
    """Kaynak 'Outstanding Balance Analysis' ile blok-blok birebir: 4'lü KPI
    şeridi (₺M), Kırılım'a göre Balance Bridge (Top-8 + Other), Balance ve
    Customer heatmap carousel'leri (Δ varsayılan ↔ t₁), Composition + Δ."""
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_sub, v_sub = _enum_domain(runner, M, "DIM_SUBPRODUCT", "Alt Ürün", "f_alturun", "other", b)
    f_cst, v_cst = _enum_domain(runner, M, "DIM_CUSTOMER", "Müşteri Tipi", "f_musteri", "other", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b,
                                band_order=True)

    DIMS = ["SEGMENT", "AUM", "PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE"]
    f_k1 = _filter("f_kirilim", "other", "enum_single",
                   "Kırılım (Decomposition Dim)", "SEGMENT", DIMS)
    v_k1 = _var("kirilim", "other", "enum_single", "SEGMENT", DIMS)
    b["kirilim"] = "f_kirilim"
    f_k2 = _filter("f_kirilim2", "other", "enum_single",
                   "2. Kırılım (heatmap kolonları)", "AUM", DIMS)
    v_k2 = _var("kirilim2", "other", "enum_single", "AUM", DIMS)
    b["kirilim2"] = "f_kirilim2"

    f_don_m, v_don_m, f_don_d, v_don_d = _snapshot_date_filters(runner, M, D, b)

    W = ("(DIM_SEGMENT IN (:segment) OR DIM_SEGMENT IS NULL) AND "
         "(DIM_PRODUCT IN (:urun) OR DIM_PRODUCT IS NULL) AND "
         "(DIM_SUBPRODUCT IN (:alturun) OR DIM_SUBPRODUCT IS NULL) AND "
         "(DIM_CUSTOMER IN (:musteri) OR DIM_CUSTOMER IS NULL) AND "
         "(DIM_AUM IN (:aum) OR DIM_AUM IS NULL)")
    GRP1 = dim_case_expr("kirilim")
    GRP2 = dim_case_expr("kirilim2")

    def widgets(T, dcol, v_don, dv, sfx, label):
        enums = [v_seg, v_prd, v_sub, v_cst, v_aum]
        base_vars = [v_don] + enums
        vars_k1 = base_vars + [v_k1]
        vars_hm = base_vars + [v_k1, v_k2]

        def kpi(what, title, unit):
            return _block(f"kpi_{what}_{sfx}", "kpi", title,
                          ba_sql_kpi(T, dcol, W, dv, what),
                          base_vars, b, T, width="1/3",
                          config={"unit": unit})

        def hm_car(measure, title_tr):
            return _carousel(
                f"car_hm_{measure}_{sfx}", f"{title_tr} ({label})",
                [_block(f"hm_{measure}_d_{sfx}", "heatmap",
                        f"{title_tr} — Δ (t₀→t₁)",
                        ba_sql_heatmap(T, dcol, W, dv, GRP1, GRP2, measure, "delta"),
                        vars_hm, b, T, width="full"),
                 _block(f"hm_{measure}_a_{sfx}", "heatmap",
                        f"{title_tr} — t₁ seviyesi",
                        ba_sql_heatmap(T, dcol, W, dv, GRP1, GRP2, measure, "abs"),
                        vars_hm, b, T, width="full")])

        return [
            kpi("t0", "Bakiye t₀", "₺M"),
            kpi("t1", "Bakiye t₁", "₺M"),
            kpi("delta", "Δ Bakiye", "₺M"),
            kpi("growth", "Büyüme", "%"),
            _block(f"bridge_{sfx}", "waterfall_chart",
                   f"Balance Bridge (₺M, Top {BA_TOP_N} + Other, {label})",
                   ba_sql_bridge(T, dcol, W, dv, GRP1),
                   vars_k1, b, T, width="full", config={"unit": "M ₺"}),
            hm_car("bal", "Balance Heatmap (₺M)"),
            hm_car("cust", "Customer Heatmap (adet)"),
            _block(f"mix_{sfx}", "bar_chart",
                   f"Composition — t₀ vs t₁ pay (%, {label})",
                   ba_sql_mix(T, dcol, W, dv, GRP1),
                   vars_k1, b, T, width="1/2"),
            _block(f"mixd_{sfx}", "bar_chart",
                   f"Composition Change (puan, {label})",
                   ba_sql_mix_delta(T, dcol, W, dv, GRP1),
                   vars_k1, b, T, width="1/2"),
        ]

    manifest = _manifest_shell(
        "p_dep_balance", "Outstanding Balance Analysis",
        "Mevduat hacim analizi — kaynak sayfayla birebir: 4'lü KPI şeridi "
        "(Bakiye t₀/t₁/Δ ₺M + büyüme %), Kırılım'a göre Balance Bridge "
        "(Top-8 |Δ| + Other), Balance ve Customer heatmap carousel'leri "
        "(varsayılan slayt Δ — kaynak toggle varsayılanı; ikinci slayt t₁), "
        "Composition t₀/t₁ + Δ. 'Kırılım' filtresi kaynaktaki Decomposition "
        "Dim, '2. Kırılım' heatmap kolon ekseni.",
        [f_don_m, f_don_d, f_k1, f_k2, f_seg, f_prd, f_sub, f_cst, f_aum],
        [_section("sec_monthly", "Monthly Averages",
                  widgets(M, "MONTH", v_don_m, "donem_ay", "mon", "Monthly"),
                  page="pg_mon"),
         _section("sec_daily", "Daily Evolution",
                  widgets(D, "DAT", v_don_d, "donem_gun", "dly", "Daily"),
                  page="pg_dly")],
        [M, D], pages=PAGES_MON_DLY)
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 3 — Outstanding Tenor Analysis (Monthly + Daily)
# Kaynak: TenorAnalysisEngine / DailyTenorEngine + _build_tenor_payload
# (app.py L1999-2380). Birim ₺M; bucket Bennet waterfall carousel'i (tüm
# kovalar, Top-N kesmesi yok); TENOR↔DTM modu; swap hedge overlay kapsam dışı.
# ════════════════════════════════════════════════════════════════════════════

def build_tenor(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_TENOR_MONTHLY", f"{sch}.PRISMA_DEP_TENOR_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_sub, v_sub = _enum_domain(runner, M, "DIM_SUBPRODUCT", "Alt Ürün", "f_alturun", "other", b)
    f_cst, v_cst = _enum_domain(runner, M, "DIM_CUSTOMER", "Müşteri Tipi", "f_musteri", "other", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b,
                                band_order=True)
    f_kov, v_kov = _enum_domain(runner, M, "DIM_BUCKET", "Vade Kovası", "f_kova",
                                "tenor_bucket", b, band_order=True)
    f_mod = _filter("f_mod", "other", "enum_single", "Vade Modu (TENOR ↔ DTM)",
                    "tenor", ["tenor", "dtm"])
    v_mod = _var("mod", "other", "enum_single", "tenor", ["tenor", "dtm"])
    b["mod"] = "f_mod"
    f_don_m, v_don_m, f_don_d, v_don_d = _snapshot_date_filters(runner, M, D, b)

    W = ("TENOR_MODE = :mod AND DIM_BUCKET IN (:kova) AND "
         "(DIM_SEGMENT IN (:segment) OR DIM_SEGMENT IS NULL) AND "
         "(DIM_PRODUCT IN (:urun) OR DIM_PRODUCT IS NULL) AND "
         "(DIM_SUBPRODUCT IN (:alturun) OR DIM_SUBPRODUCT IS NULL) AND "
         "(DIM_CUSTOMER IN (:musteri) OR DIM_CUSTOMER IS NULL) AND "
         "(DIM_AUM IN (:aum) OR DIM_AUM IS NULL)")
    GRP = "NVL(DIM_BUCKET, '-')"
    ORD = band_order_expr("p")
    buckets = sorted([x for x in runner.distinct(M, "DIM_BUCKET") if x],
                     key=lambda s: (_band_sort_key(s), s))

    def snap(T, dcol, dv):
        return _snap_cte(T, dcol, W, "DIM_BUCKET",
                         extra_measures=", SUM(WT_SUM) wt", dv=dv)

    def widgets(T, dcol, v_don, dv, sfx, label):
        all_vars = [v_don, v_mod, v_seg, v_prd, v_sub, v_cst, v_aum, v_kov]

        def blk(bid, btype, title, sql, **kw):
            return _block(bid, btype, title, sql, all_vars, b, T, **kw)

        base = snap(T, dcol, dv)
        wat_kpis = [
            blk(f"kpi_wat0_{sfx}", "kpi", "WAT t₀ (gün)",
                f"{base} SELECT ROUND(SUM(wt)/NULLIF(SUM(b),0), 1) FROM s0",
                width="1/3", config={"unit": "gün"}),
            blk(f"kpi_wat1_{sfx}", "kpi", "WAT t₁ (gün)",
                f"{base} SELECT ROUND(SUM(wt)/NULLIF(SUM(b),0), 1) FROM s1",
                width="1/3", config={"unit": "gün"}),
            blk(f"kpi_watd_{sfx}", "kpi", "Δ WAT (gün)",
                f"{base} SELECT ROUND((SELECT SUM(wt)/NULLIF(SUM(b),0) FROM s1) - "
                f"(SELECT SUM(wt)/NULLIF(SUM(b),0) FROM s0), 1) FROM DUAL",
                width="1/3", config={"unit": "gün"}),
        ]

        # Maturity Ladder: t₀/t₁ yan yana (₺M — kaynak birimi; ₺Mr DEĞİL)
        ladder = blk(f"ladder_{sfx}", "bar_chart",
                     f"Maturity Ladder — Balance t₀ vs t₁ (₺M, {label})",
                     f"""{base},
j AS (SELECT NVL(s1.p, s0.p) p, NVL(s0.b,0)/1e6 b0, NVL(s1.b,0)/1e6 b1
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND(b0) AS "t0 (MTL)", ROUND(b1) AS "t1 (MTL)"
FROM j ORDER BY {ORD} NULLS LAST, p""",
                     width="full")
        ladder_d = blk(f"ladderd_{sfx}", "bar_chart",
                       f"Balance Change per Bucket (₺M, {label})",
                       f"""{base},
j AS (SELECT NVL(s1.p, s0.p) p, (NVL(s1.b,0)-NVL(s0.b,0))/1e6 d
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND(d) AS DELTA_M FROM j ORDER BY {ORD} NULLS LAST, p""",
                       width="full")

        # Term Structure: Δbps bar (sağ) + t₀/t₁ faiz çizgileri (sol) — kaynakla aynı
        curve = blk(f"curve_{sfx}", "combo_chart",
                    f"Term Structure — Rate per Bucket ({label})",
                    f"""{base},
j AS (SELECT NVL(s1.p, s0.p) p,
             NVL(s0.wr,0)/NULLIF(s0.b,0)*100 r0, NVL(s1.wr,0)/NULLIF(s1.b,0)*100 r1
      FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p)
SELECT p AS KOVA, ROUND((r1-r0)*100) AS "Rate Delta (bps)",
       ROUND(r0, 2) AS "Faiz t0 (%)", ROUND(r1, 2) AS "Faiz t1 (%)"
FROM j ORDER BY {ORD} NULLS LAST, p""",
                    width="full",
                    config={"left_axis_title": "Faiz (%)",
                            "right_axis_title": "Δ (bps)",
                            "series": [
                                {"name": "Rate Delta (bps)", "kind": "bar", "axis": "right"},
                                {"name": "Faiz t0 (%)", "kind": "line", "axis": "left"},
                                {"name": "Faiz t1 (%)", "kind": "line", "axis": "left"},
                            ]})

        mix = blk(f"mix_{sfx}", "bar_chart",
                  f"Bucket Composition (% of Total, {label})",
                  sql_mix(T, dcol, W, "DIM_BUCKET", buckets, dv=dv),
                  width="1/2", config={"stacked": True, "horizontal": True})
        mixd = blk(f"mixd_{sfx}", "bar_chart",
                   f"Composition Δ (puan, {label})",
                   sql_mix_delta(T, dcol, W, "DIM_BUCKET", dv=dv),
                   width="1/2")

        # Bucket Bennet waterfall carousel'i — Cost'la aynı motor, sabit
        # DIM_BUCKET kırılımı, Top-N kesmesi yok (kova sayısı az).
        NO_CUT = 999
        car_wf = _carousel(
            f"car_wf_{sfx}", f"Bucket Rate Waterfall ({label})",
            [blk(f"wf1_{sfx}", "waterfall_chart",
                 "Bucket Rate Waterfall (%): Mix vs Pricing",
                 ca_sql_wf1(T, dcol, W, dv, grp=GRP),
                 width="full", config={"unit": "%"}),
             _canvas(f"cv_wf2_{sfx}", "Pricing Drivers",
                     [blk(f"wf2_{sfx}", "waterfall_chart",
                          "Bucket Pricing Drivers (%)",
                          ca_sql_wf2(T, dcol, W, dv, grp=GRP, top_n=NO_CUT),
                          width="full", config={"unit": "%"}),
                      blk(f"wf2bal_{sfx}", "bar_chart",
                          "Bucket Balance Δ (₺M)",
                          ca_sql_wf2_bal(T, dcol, W, dv, grp=GRP, top_n=NO_CUT),
                          width="full")]),
             _canvas(f"cv_wf4_{sfx}", "Mix Drivers",
                     [blk(f"wf4_{sfx}", "waterfall_chart",
                          "Bucket Mix Drivers (%)",
                          ca_sql_wf4(T, dcol, W, dv, grp=GRP, top_n=NO_CUT),
                          width="full", config={"unit": "%"}),
                      blk(f"wf4w_{sfx}", "bar_chart",
                          "Bucket Weight Changes (Δ pay, puan)",
                          ca_sql_wf4_weights(T, dcol, W, dv, grp=GRP, top_n=NO_CUT),
                          width="full")])])

        return wat_kpis + [ladder, ladder_d, curve, mix, mixd, car_wf]

    # Günlük ekstra: per-bucket rate evolution (kaynak ta-dly-rate)
    daily_vars = [v_don_d, v_mod, v_seg, v_prd, v_sub, v_cst, v_aum, v_kov]
    bucket_rate_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN DIM_BUCKET = '{bk}' THEN WR_SUM ELSE 0 END)"
        f"/NULLIF(SUM(CASE WHEN DIM_BUCKET = '{bk}' THEN BALANCE ELSE 0 END),0)*100, 2)"
        f" AS \"{bk}\"" for bk in buckets)
    daily_extra = [
        _block("rate_evo_dly", "line_chart",
               "Per-Bucket Rate Evolution (%, günlük)",
               f"""SELECT DAT, {bucket_rate_cases}
FROM {D} WHERE {W} AND DAT BETWEEN :donem_gun_from AND :donem_gun_to
GROUP BY DAT ORDER BY DAT""",
               daily_vars, b, D, width="full"),
    ]

    manifest = _manifest_shell(
        "p_dep_tenor", "Outstanding Tenor Analysis",
        "Vade analizi — kaynak sayfayla birebir: WAT KPI'ları, Maturity "
        "Ladder t₀/t₁ + Δ (₺M), Term Structure (Δbps bar + t₀/t₁ faiz "
        "çizgileri), Bucket Composition + Δ, bucket Bennet waterfall "
        "carousel'i (Mix vs Pricing / Pricing Drivers / Mix Drivers, %); "
        "günlükte Per-Bucket Rate Evolution. TENOR↔DTM modu filtre. Swap "
        "hedge overlay kapsam dışı.",
        [f_don_m, f_don_d, f_mod, f_kov, f_seg, f_prd, f_sub, f_cst, f_aum],
        [_section("sec_monthly", "Monthly Averages",
                  widgets(M, "MONTH", v_don_m, "donem_ay", "mon", "Monthly"),
                  page="pg_mon"),
         _section("sec_daily", "Daily Evolution",
                  widgets(D, "DAT", v_don_d, "donem_gun", "dly", "Daily")
                  + daily_extra, page="pg_dly")],
        [M, D], pages=PAGES_MON_DLY)
    return manifest, [M, D]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 4 — Future Deposit Rollings
# Kaynak: WeeklyRollingsEngine (app.py L2955-3290). Pivotlar tam-sayı ₺mn;
# DTM histogramı kaynak kovalarıyla (≤14/15-32/33-90/91-180/180+); müşteri
# listesi ≥100 mio TRY + TRY-önce sıralama + türetilmiş kolonlar (kaynak
# customers_by_date). AG-Grid hiyerarşisi ve HHI başlığı kapsam dışı.
# ════════════════════════════════════════════════════════════════════════════

ROLL_BANDS = ["0-5M", "5M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+"]
# Kaynak WeeklyRollingsEngine.DTM_BUCKET_ORDER — '0-30/31-60/...' DEĞİL.
ROLL_DTM_EDGES = [("<=14", 14), ("15-32", 32), ("33-90", 90), ("91-180", 180)]


def _roll_dtm_case():
    branches = " ".join(f"WHEN DTM <= {hi} THEN '{lbl}'"
                        for lbl, hi in ROLL_DTM_EDGES)
    return f"CASE {branches} ELSE '180+' END"


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
        f"ROUND(SUM(CASE WHEN AUM_BAND = '{bd}' THEN TRY_BAKIYE_TOPLAM ELSE 0 END)/1e6)"
        f" AS \"{bd}\"" for bd in ROLL_BANDS)

    # Segment kolon sırası kaynaktaki gibi dönem toplamına göre azalan.
    seg_df = runner.query(
        f"SELECT SEGMENT FROM {T} GROUP BY SEGMENT "
        f"ORDER BY SUM(TRY_BALANCE) DESC")
    segments = [str(s) for s in seg_df.iloc[:, 0].tolist() if str(s).strip()]
    seg_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN SEGMENT = '{sg}' THEN TRY_BALANCE ELSE 0 END)/1e6, 1)"
        f" AS \"{sg}\"" for sg in segments)

    dtm_case = _roll_dtm_case()
    blocks_s1 = [
        _block("grid_all", "data_table", "Weekly Rollings (mio TRY — TRY + FX)",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN, CURRENCY,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6) AS TOPLAM
FROM {A} WHERE {R}
GROUP BY ROLL_DATE, CURRENCY ORDER BY ROLL_DATE, CURRENCY DESC""",
               vars_a, b, A, width="full",
               config=_tbl_cols("GUN", "CURRENCY", *ROLL_BANDS, "TOPLAM")),
        _block("grid_g", "data_table",
               "TRY Standart Vadeli Dönüşler (mio TRY) — Gerçek",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6) AS TOPLAM
FROM {A} WHERE {R} AND CUST_TP = 'G' AND CURRENCY = 'TRY'
GROUP BY ROLL_DATE ORDER BY ROLL_DATE""",
               vars_a, b, A, width="1/2",
               config=_tbl_cols("GUN", *ROLL_BANDS, "TOPLAM")),
        _block("grid_t", "data_table",
               "TRY Standart Vadeli Dönüşler (mio TRY) — Tüzel",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS GUN,
{band_cases}, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6) AS TOPLAM
FROM {A} WHERE {R} AND CUST_TP = 'T' AND CURRENCY = 'TRY'
GROUP BY ROLL_DATE ORDER BY ROLL_DATE""",
               vars_a, b, A, width="1/2",
               config=_tbl_cols("GUN", *ROLL_BANDS, "TOPLAM")),
        _block("dtm_hist", "bar_chart", "Vade Bucket Dağılımı (mio TRY)",
               f"""SELECT {dtm_case} AS VADE_GUN,
ROUND(SUM(TRY_BALANCE)/1e6, 1) AS BAKIYE_M
FROM {T} WHERE {R}
GROUP BY {dtm_case}
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
        # Kaynak customers_by_date: gün içinde (müşteri, CCY) bazında toplanır,
        # 100 mio TRY altı listelenmez, TRY satırları önce; türetilmiş kolonlar
        # ort. faiz = Σ(r·B)/ΣB, işlem adedi, ort. DTM, gün içi pay (%).
        _block("cust_grid", "data_table",
               "Müşteri Listesi (≥100 mio TRY, KVKK maskeli)",
               f"""SELECT TO_CHAR(ROLL_DATE, 'DD/MM/YYYY') AS DONUS,
       FULL_NM AS MUSTERI, SEGMENT, CCY_CODE,
       ROUND(SUM(TRY_BALANCE)/1e6, 2) AS BAKIYE_M,
       COUNT(ACCT_ID) AS ISLEM_ADEDI,
       ROUND(SUM(INTRST_RT * TRY_BALANCE)/NULLIF(SUM(TRY_BALANCE),0), 2) AS ORT_FAIZ,
       ROUND(AVG(DTM)) AS ORT_KALAN_GUN,
       ROUND(RATIO_TO_REPORT(SUM(TRY_BALANCE))
             OVER (PARTITION BY ROLL_DATE) * 100, 2) AS GUN_PAYI_PCT
FROM {T} WHERE {R}
GROUP BY ROLL_DATE, FULL_NM, SEGMENT, CCY_CODE
HAVING SUM(TRY_BALANCE) >= 1e8
ORDER BY ROLL_DATE,
         CASE WHEN CCY_CODE = 'TRY' THEN 0 ELSE 1 END, CCY_CODE,
         SUM(TRY_BALANCE) DESC""",
               vars_a, b, T, width="full",
               config=_tbl_cols("DONUS", "MUSTERI", "SEGMENT", "CCY_CODE",
                                "BAKIYE_M", "ISLEM_ADEDI", "ORT_FAIZ",
                                "ORT_KALAN_GUN", "GUN_PAYI_PCT")),
    ]
    manifest = _manifest_shell(
        "p_dep_rollings", "Future Deposit Rollings",
        "Vadesi dolan mevduat dönüşleri — 3 pivot tablo (TRY+FX / Gerçek / "
        "Tüzel, tam-sayı mio TRY), kaynak kovalı DTM histogramı "
        "(≤14/15-32/33-90/91-180/180+), segment donut, tarih×segment istifli "
        "bar (segmentler dönem toplamına göre sıralı), ≥100 mio TRY müşteri "
        "listesi (KVKK maskeli; ort. faiz/DTM + gün içi pay). Pencere "
        "deposits_pipeline koşusuyla belirlenir. Grid hücre-drill'i ve HHI "
        "başlığı kapsam dışı.",
        [f_don, f_ccy],
        [_section("sec_tables", "Mevduat Dönüş Raporu", blocks_s1),
         _section("sec_segments", "Müşteri Segmenti & Top Müşteri", blocks_s2)],
        [A, T])
    return manifest, [A, T]


# ════════════════════════════════════════════════════════════════════════════
# SAYFA 5 — New Business: Volume & Pricing
# Kaynak: api_np_rate_volume_bubble / api_np_rate_volume_heatmap /
# api_np_aum_rate_chart / api_np_rate_volume_curve (app.py L5867-7215).
# Pencere modeli kaynaktaki gibi: t₀/t₁ = dönem filtresinin uçları; frekans
# D → tek gün, W → o tarihte biten 7 günlük pencere. Faiz karşılaştırmaları
# BİLEŞİK (WC_SUM/NP_HACIM) üzerinden; bubble y/Δ'ları ağırlıklı vade ile
# BASİT faize geri çevrilir (kaynak reverse conversion, POWER ile).
# ════════════════════════════════════════════════════════════════════════════

# new-prod ince AUM bandı → ortak 8-band (kaynak NP_AUM_TO_COMMON).
NP_AUM_TO_COMMON = [
    ("0-1M", ["0-1M"]),
    ("1M-5M", ["1M-2M", "2M-5M"]),
    ("5M-10M", ["5M-10M"]),
    ("10M-25M", ["10M-25M"]),
    ("25M-50M", ["25M-50M"]),
    ("50M-100M", ["50M-100M"]),
    ("100M-200M", ["100M-200M"]),
    ("200M+", ["200M-500M", "500M-1B", "1B+"]),
]
# AUM combo grafiğinin 6'lı kaba grupları (kaynak _AUM_GROUP_MAP — ortak
# 8-band'den FARKLI bir gruplama; birebirlik için ayrı tutulur).
NP_COMBO_AUM = [
    ("0-5M", ["0-1M", "1M-2M", "2M-5M"]),
    ("5-25M", ["5M-10M", "10M-25M"]),
    ("25-50M", ["25M-50M"]),
    ("50-100M", ["50M-100M"]),
    ("100-200M", ["100M-200M"]),
    ("200M+", ["200M-500M", "500M-1B", "1B+"]),
]


def _np_case(pairs, col="AUM_BAND", other="Bilinmiyor"):
    return ("CASE " + " ".join(
        f"WHEN {col} IN ({_quote_list(fine)}) THEN '{coarse}'"
        for coarse, fine in pairs) + f" ELSE '{other}' END")


# Basit faize geri çevrim (kaynak np_compound_to_simple_pct):
# simple% = ((1 + comp/100)^(t/365) − 1) · (365/t) · 100
def _np_simple(comp, ten):
    return (f"CASE WHEN {ten} > 0 THEN "
            f"(POWER(1 + ({comp})/100, ({ten})/365) - 1) * (365/({ten})) * 100 "
            f"ELSE NULL END")


def _np_win(dv, edge):
    """Frekansa göre pencere: W → [tarih-6, tarih], D → [tarih, tarih]."""
    d = f":{dv}_{edge}"
    return (f"DAT BETWEEN {d} - (CASE WHEN :frek = 'W' THEN 6 ELSE 0 END) "
            f"AND {d}")


def np_sql_bubble(N, O, enum_where, mode):
    """Kaynak np_rate_volume_bubble'ın outstanding-grenli birebiri.

    Kaynak, OS bakiyesini ince NP hücrelerine hacim payıyla DAĞITIP client'ta
    yeniden toplar; hücre toplamları OS greninde (kanal × müşteri tipi ×
    ortak AUM) birebir korunur. Burada noktalar doğrudan o grende üretilir —
    dağıtım/toplama sapması yok. Yalnız TRY (OS verisi TRY'dir).
    x: mode='bal' → Δ outstanding (₺M, AS-OF t₀/t₁); mode='rate' → Δ basit
    faiz (bps). y = t₁ basit faizi (%); boyut = pencere new-prod hacmi ort.
    (₺M); 5. kolon = WAvg t₁ (yatay çizgi)."""
    aum_common = _np_case(NP_AUM_TO_COMMON)
    grp = f"RELATED_PC || '_' || CUST_TP || '_' || {aum_common}"
    win0, win1 = _np_win("donem", "from"), _np_win("donem", "to")

    def w(win, name):
        return f"""{name} AS (
  SELECT RELATED_PC ch, CUST_TP ct, {aum_common} au,
         SUM(NP_HACIM) v, SUM(WC_SUM) wc, SUM(WT_SUM) wt
  FROM {N} WHERE {win} AND CCY_CODE = 'TRY' AND {enum_where}
  GROUP BY RELATED_PC, CUST_TP, {aum_common})"""

    def o(edge, name):
        return f"""{name} AS (
  SELECT CHANNEL ch, CUST_TP ct, AUM_COMMON au, SUM(BAL_SUM) ob
  FROM {O} WHERE DAT = (SELECT MAX(DAT) FROM {O} WHERE DAT <= :donem_{edge})
  GROUP BY CHANNEL, CUST_TP, AUM_COMMON)"""

    x = ("(NVL(o1.ob,0) - NVL(o0.ob,0))" if mode == "bal"
         else "(NVL(e.s1, e.s0) - NVL(e.s0, e.s1)) * 100")
    return f"""WITH {w(win0, 'w0')},
{w(win1, 'w1')},
{o('from', 'o0')},
{o('to', 'o1')},
m AS (SELECT NVL(w0.ch, w1.ch) ch, NVL(w0.ct, w1.ct) ct, NVL(w0.au, w1.au) au,
             NVL(w0.v, 0) v0, NVL(w1.v, 0) v1,
             w0.wc wc0, w0.wt wt0, w1.wc wc1, w1.wt wt1
      FROM w0 FULL OUTER JOIN w1
        ON w0.ch = w1.ch AND w0.ct = w1.ct AND w0.au = w1.au),
e AS (SELECT m.*,
             {_np_simple('m.wc0/NULLIF(m.v0,0)', 'm.wt0/NULLIF(m.v0,0)')} s0,
             {_np_simple('m.wc1/NULLIF(m.v1,0)', 'm.wt1/NULLIF(m.v1,0)')} s1
      FROM m),
tot AS (SELECT SUM(CASE WHEN s1 IS NOT NULL THEN v1 * s1 END)
             / NULLIF(SUM(CASE WHEN s1 IS NOT NULL THEN v1 END), 0) wavg
        FROM e)
SELECT AD, X_DEGER, FAIZ_T1_PCT, HACIM_M, "WAvg (%)" FROM (
  SELECT e.ch || '_' || e.ct || '_' || e.au AS AD,
         ROUND({x}, 2) X_DEGER,
         ROUND(NVL(e.s1, e.s0), 2) FAIZ_T1_PCT,
         ROUND((e.v0 + e.v1)/2, 1) HACIM_M,
         (SELECT ROUND(wavg, 2) FROM tot) AS "WAvg (%)"
  FROM e
  LEFT JOIN o0 ON e.ch = o0.ch AND e.ct = o0.ct AND e.au = o0.au
  LEFT JOIN o1 ON e.ch = o1.ch AND e.ct = o1.ct AND e.au = o1.au
  WHERE e.v0 > 0 OR e.v1 > 0
  ORDER BY (e.v0 + e.v1) DESC
) WHERE ROWNUM <= 40"""


def np_sql_heatmap(N, enum_where, mode):
    """Rate × Volume heatmap — kanal (kaynak 'Segment' = RELATED_PC) × ortak
    AUM. mode='t1': t₁ penceresi bileşik faiz (%); mode='delta': Δ bileşik
    (bps, kaynak per-cell tanımı (t₁−t₀)·100)."""
    aum_common = _np_case(NP_AUM_TO_COMMON)
    win0, win1 = _np_win("donem", "from"), _np_win("donem", "to")
    val = ("ROUND(NVL(h1.wc/NULLIF(h1.v,0), 0), 2)" if mode == "t1"
           else "CASE WHEN h0.v > 0 AND h1.v > 0 THEN "
                "ROUND((h1.wc/h1.v - h0.wc/h0.v) * 100, 1) ELSE 0 END")
    alias = "FAIZ_PCT" if mode == "t1" else "DELTA_BPS"
    return f"""WITH h0 AS (
  SELECT RELATED_PC ch, {aum_common} au, SUM(NP_HACIM) v, SUM(WC_SUM) wc
  FROM {N} WHERE {win0} AND {enum_where}
  GROUP BY RELATED_PC, {aum_common}),
h1 AS (
  SELECT RELATED_PC ch, {aum_common} au, SUM(NP_HACIM) v, SUM(WC_SUM) wc
  FROM {N} WHERE {win1} AND {enum_where}
  GROUP BY RELATED_PC, {aum_common})
SELECT NVL(h0.ch, h1.ch) AS KANAL, NVL(h0.au, h1.au) AS AUM_BANDI,
       {val} AS {alias}
FROM h0 FULL OUTER JOIN h1 ON h0.ch = h1.ch AND h0.au = h1.au
ORDER BY 1, {band_order_expr('NVL(h0.au, h1.au)')} NULLS LAST, 2"""


def np_sql_aum_combo(N, enum_where):
    """New Business Volume & Rate — kaynak np_aum_rate_chart: frekansa göre
    binlenmiş (W → t₁'de biten 7 günlük pencereler) hacim barı + 6'lı kaba
    AUM grubu başına bileşik faiz çizgileri."""
    combo_case = _np_case(NP_COMBO_AUM)
    rate_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN {combo_case} = '{coarse}' THEN WC_SUM ELSE 0 END)"
        f"/NULLIF(SUM(CASE WHEN {combo_case} = '{coarse}' THEN NP_HACIM ELSE 0 END),0), 2)"
        f" AS \"{coarse}\"" for coarse, _f in NP_COMBO_AUM)
    bin_len = "CASE WHEN :frek = 'W' THEN 7 ELSE 1 END"
    bin_date = f":donem_to - FLOOR((:donem_to - DAT)/({bin_len})) * ({bin_len})"
    return f"""SELECT TO_CHAR({bin_date}, 'YYYY-MM-DD') AS TARIH,
       ROUND(SUM(NP_HACIM), 1) AS "Hacim (MTL)", {rate_cases}
FROM {N}
WHERE DAT BETWEEN :donem_from AND :donem_to AND {enum_where}
GROUP BY {bin_date}
ORDER BY 1"""


def np_sql_curve(N, enum_where):
    """Konsantrasyon eğrisi (kaynak np_rate_volume_curve, t₁ penceresi):
    hücreler bileşik faize göre artan sıralanır, X = kümülatif hacim payı (%).
    Kaynak satır-bazlı detay kullanır; burada 6-boyutlu hücre yaklaşımı
    (bilinen, kabul edilmiş sadeleştirme)."""
    win1 = _np_win("donem", "to")
    return f"""WITH cells AS (
  SELECT SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0) r, SUM(NP_HACIM) v
  FROM {N} WHERE {win1} AND {enum_where}
  GROUP BY CCY_CODE, CUST_TP, RELATED_PC, AUM_BAND, TENOR_GRP, SUB_SEGMENT
  HAVING SUM(NP_HACIM) > 0)
SELECT TO_CHAR(ROUND(100 * SUM(v) OVER (ORDER BY r) / SUM(v) OVER (), 1)) AS KUM_PAY_PCT,
       ROUND(r, 2) AS FAIZ_PCT
FROM cells ORDER BY r"""


def build_newbiz(runner, sch):
    N = f"{sch}.PRISMA_NP_FLOW_DAILY"
    O = f"{sch}.PRISMA_NP_OUT_DAILY"
    b = {}
    f_ccy, v_ccy = _enum_domain(runner, N, "CCY_CODE", "Para Birimi", "f_ccy", "currency", b)
    f_seg, v_seg = _enum_domain(runner, N, "SUB_SEGMENT", "Alt Segment", "f_segment", "segment", b)
    f_cst, v_cst = _enum_domain(runner, N, "CUST_TP", "Müşteri Tipi", "f_musteri", "other", b)
    f_ten, v_ten = _enum_domain(runner, N, "TENOR_GRP", "Vade Grubu", "f_vade",
                                "tenor_bucket", b, band_order=True)

    # t₀/t₁ varsayılanı: son gün + önceki takvim Perşembesi (Cost/Balance ile
    # aynı desen; kaynakta da t₁=son gün, t₀ kullanıcı seçimi).
    dates = runner.dates(N, "DAT")
    if not dates:
        raise RuntimeError(f"{N} boş — önce deposits_pipeline koşulmalı")
    d_to = dates[-1]
    d_from = max(_prev_thursday(d_to), dates[0])
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem (t₀ → t₁)",
                    {"from": d_from, "to": d_to})
    v_don = _var("donem", "as_of_time", "date_range",
                 {"from": d_from, "to": d_to})
    b["donem"] = "f_donem"
    f_frk = _filter("f_frekans", "other", "enum_single",
                    "Frekans (pencere: D=1 gün, W=7 gün)", "W", ["D", "W"])
    v_frk = _var("frek", "other", "enum_single", "W", ["D", "W"])
    b["frek"] = "f_frekans"

    ENUMS = ("CCY_CODE IN (:ccy) AND SUB_SEGMENT IN (:segment) AND "
             "CUST_TP IN (:musteri) AND TENOR_GRP IN (:vade)")
    all_vars = [v_don, v_frk, v_ccy, v_seg, v_cst, v_ten]
    # Bubble'lar OS verisi gereği TRY'ye sabit → ccy değişkeni kullanılmaz.
    bub_enums = ("SUB_SEGMENT IN (:segment) AND CUST_TP IN (:musteri) "
                 "AND TENOR_GRP IN (:vade)")
    bub_vars = [v_don, v_frk, v_seg, v_cst, v_ten]

    blocks_bub = [
        _block("bub_bal", "scatter_chart", "Balance Evolution (outstanding, TRY)",
               np_sql_bubble(N, O, bub_enums, "bal"), bub_vars, b, N,
               config={"x_title": "Δ Outstanding (₺M)",
                       "y_title": "Basit Faiz t₁ (%)"}),
        _block("bub_rate", "scatter_chart", "Interest Rate Evolution (TRY)",
               np_sql_bubble(N, O, bub_enums, "rate"), bub_vars, b, N,
               config={"x_title": "Δ Basit Faiz (bps)",
                       "y_title": "Basit Faiz t₁ (%)"}),
    ]
    blocks_pricing = [
        _carousel("car_hm_rv", "Rate × Volume Heatmap — Kanal × AUM",
                  [_block("hm_rv_t1", "heatmap",
                          "Bileşik Faiz — t₁ penceresi (%)",
                          np_sql_heatmap(N, ENUMS, "t1"),
                          all_vars, b, N, width="full"),
                   _block("hm_rv_d", "heatmap",
                          "Δ Bileşik Faiz — t₀→t₁ (bps)",
                          np_sql_heatmap(N, ENUMS, "delta"),
                          all_vars, b, N, width="full")]),
        _block("aum_combo", "combo_chart",
               "New Business Volume & Interest Rate (AUM grupları)",
               np_sql_aum_combo(N, ENUMS), all_vars, b, N, width="full",
               config={"left_axis_title": "Hacim (₺M)",
                       "right_axis_title": "Bileşik Faiz (%)",
                       "series": ([{"name": "Hacim (MTL)", "kind": "bar",
                                    "axis": "left"}] +
                                  [{"name": c, "kind": "line", "axis": "right"}
                                   for c, _f in NP_COMBO_AUM])}),
        _block("conc_curve", "line_chart",
               "Faiz × Kümülatif Hacim Eğrisi (t₁ penceresi)",
               np_sql_curve(N, ENUMS), all_vars, b, N, width="full"),
    ]
    manifest = _manifest_shell(
        "p_dep_newbiz", "New Business — Volume & Pricing",
        "Bağlanan mevduat akışı — kaynak sayfayla hizalı: 2 bubble (t₀/t₁ "
        "pencereleri; Balance X = outstanding Δ, faizler bileşikten basite "
        "geri çevrilir, WAvg çizgili, TRY), Rate×Volume heatmap carousel'i "
        "(t₁ bileşik % ↔ Δ bps, kanal × ortak AUM), frekans binli AUM combo, "
        "konsantrasyon eğrisi (hücre yaklaşımı — kaynak satır-bazlı). "
        "Heatmap hücre-hover combo'su ve drill kapsam dışı.",
        [f_don, f_frk, f_ccy, f_seg, f_cst, f_ten],
        [_section("sec_bubbles", "Bubble Analysis", blocks_bub),
         _section("sec_pricing", "Fiyatlama ve Hacim", blocks_pricing)],
        [N, O])
    return manifest, [N, O]



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

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
  * Waterfall carousel'inin 2-3. slaytları (pricing/mix driver ayrıştırması) —
    tek birleşik katkı köprüsü verilir (Σ katkı = R1−R0 birebir tutar).
  * Heatmap "Δ ↔ seviye" ve mix "%↔mutlak" görünüm toggle'ları — seviye/%
    modu sabit verilir.
  * Rollings pivot tablolarının AG-Grid hiyerarşisi — düz pivot tablo verilir.
  * NP heatmap hücre-hover yan combo'su; min-bubble-size slider'ı.

Statik: dataset/cron bayrağı yok; veri tazelemek = deposits_pipeline koşusu.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
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


def sql_bubbles(table, dcol, enum_where, dim, mode):
    """Bubble noktaları (dim üyesi başına). mode='bal': x=ΔBakiye ₺Mr;
    mode='rate': x=ΔFaiz bps. y=Faiz t₁ %, boyut=Bakiye t₁ ₺Mr."""
    x = ("(NVL(s1.b,0) - NVL(s0.b,0))/1e9" if mode == "bal"
         else "(NVL(s1.wr,0)/NULLIF(s1.b,0) - NVL(s0.wr,0)/NULLIF(s0.b,0))*10000")
    return f"""{_snap_cte(table, dcol, enum_where, dim)}
SELECT NVL(s1.p, s0.p) AS AD,
       ROUND({x}, 2) AS X,
       ROUND(NVL(s1.wr,0)/NULLIF(s1.b,0)*100, 2) AS FAIZ_T1_PCT,
       ROUND(NVL(s1.b,0)/1e9, 3) AS BOYUT_MLR
FROM s0 FULL OUTER JOIN s1 ON s0.p = s1.p
WHERE NVL(s1.b,0) > 0 ORDER BY 4 DESC"""


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
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b)
    lo, _hi = runner.minmax_date(M, "MONTH")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem (t₀ → t₁)",
                    {"from": lo, "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": "today"})
    b["donem"] = "f_donem"
    all_vars = [v_don, v_seg, v_prd, v_aum]
    W = "DIM_SEGMENT IN (:segment) AND DIM_PRODUCT IN (:urun) AND DIM_AUM IN (:aum)"

    def widgets(T, dcol, sfx, label):
        return [
            _block(f"wf_rate_{sfx}", "waterfall_chart",
                   f"Deposit Rate Waterfall ({label})",
                   sql_rate_waterfall(T, dcol, W, "DIM_PRODUCT"),
                   all_vars, b, T, width="full", config={"unit": "%"}),
            _block(f"bub_bal_{sfx}", "scatter_chart",
                   f"Bubble — Δ Bakiye × Faiz ({label})",
                   sql_bubbles(T, dcol, W, "DIM_PRODUCT", "bal"),
                   all_vars, b, T,
                   config={"x_title": "Δ Bakiye (₺Mr)", "y_title": "Faiz t₁ (%)"}),
            _block(f"bub_rate_{sfx}", "scatter_chart",
                   f"Bubble — Δ Faiz × Faiz ({label})",
                   sql_bubbles(T, dcol, W, "DIM_PRODUCT", "rate"),
                   all_vars, b, T,
                   config={"x_title": "Δ Faiz (bps)", "y_title": "Faiz t₁ (%)"}),
            _block(f"hm_rate_{sfx}", "heatmap",
                   f"Interest Rate Heatmap — Segment × AUM ({label}, t₁)",
                   sql_heatmap_t1(T, dcol, W, "DIM_SEGMENT", "DIM_AUM", "rate"),
                   all_vars, b, T, width="full"),
        ]

    manifest = _manifest_shell(
        "p_dep_cost", "Outstanding Cost Analysis",
        "Mevduat maliyet analizi — kaynak dashboard'un Monthly Averages + "
        "Daily Evolution alt sekmeleri iki bölüm olarak. t₀/t₁ = dönem "
        "filtresinin başı/sonu (AS-OF). Waterfall driver-slide'ları ve drill "
        "bilinçli kapsam dışı.",
        [f_don, f_seg, f_prd, f_aum],
        [_section("sec_monthly", "Monthly Averages", widgets(M, "MONTH", "mon", "aylık")),
         _section("sec_daily", "Daily Evolution", widgets(D, "DAT", "dly", "günlük"))],
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
    for sec in manifest["blocks"]:
        for blk in sec.get("children", []):
            yield blk


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

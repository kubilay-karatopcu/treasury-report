"""deposits_dashboards.py — PRISMA_DEP_* / PRISMA_NP_* tablolarından 5 deposits
dashboard'unu Prisma'da yapılmış gibi üretir ve S3'e yazar. TEK script, ofiste:

    python jobs/deposits_dashboards.py --sicil A63837

Üretilenler (dashboard başına):
  * Manifest → s3://…/presentations/<sicil>/<pid>/manifest.json — sunum
    listesinde görünür, editörde açılır, LLM/patch/properties dahil bütün
    Prisma özellikleriyle düzenlenebilir.
  * Scope contract (v1, lazy routing — cron/materializasyon YOK, statik) →
    hazırlık aşaması dolu gelir; manifest.scope_ref bağlanır.
  * basket[] → keşif/hazırlık tablo bağlamı.
  * filters[] + bloklarda :bind'li SQL + variables[] + variable_bindings —
    filtre çubuğu ÇALIŞIR: değer değişince apply-filters Phase 6.5 yoluyla
    bloklar yeniden veri çeker (Oracle, per-session cache). Enum filtre
    değerleri kurulum anında tablodan DISTINCT ile çekilir (statik).
  * Blok config'leri kurulum anında bir kez doldurulur (SQL koşulur) —
    dashboard açılışta veriyle gelir.
  * bound_experts → uzman bağlama (--experts, varsayılan: dep).

Bilinçli kapsam sınırları:
  * Yalnız mevcut blok tipleri kullanılır (kpi, bar, line, combo, area, pie,
    heatmap, data_table). Waterfall / bubble / hiyerarşik tablo grafikleri
    ATLANIR — o tipler eklendiğinde bloklar sonradan eklenir.
  * Statik: dataset_binding / cron / lazy_ttl bayrağı YOK. Veri tazelemek =
    deposits_pipeline.py'yi koşmak; bloklar aynı tablolardan okur.

Seçenekler:
    --sicil A63837       manifest sahibi (zorunlu)
    --schema X           PRISMA_* tablolarının şeması (varsayılan: bağlantı kullanıcısı)
    --only p_dep_cost,…  yalnız bu dashboard'lar
    --experts dep,fnd    bound_experts listesi (boş ver → bağlama yok)
    --skip-fill          blok SQL'lerini koşma (config boş kalır, ilk Çalıştır doldurur)
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
    """data_table için kolon tohumu (manifest validasyonu boş columns kabul
    etmez; ilk doldurma/apply_data_to_config gerçek kolonlarla üzerine yazar)."""
    return {"columns": [{"field": n, "header": n} for n in names], "rows": []}


def _section(sid, title, children):
    return {"id": sid, "type": "section_header", "title": title,
            "config": {}, "children": children}


def _enum_domain(runner, table, col, label, fid, tag, bindings_out):
    """Enum filtre + değişken çifti üret (allowed=DISTINCT, default=hepsi)."""
    values = runner.distinct(table, col)
    filt = _filter(fid, tag, "enum_multi", label, values, values)
    var = _var(fid.removeprefix("f_"), tag, "enum_multi", values, values)
    bindings_out[var["name"]] = fid
    return filt, var


# ════════════════════════════════════════════════════════════════════════════
# 5 dashboard tanımı — her biri (manifest, tablolar) döner
# ════════════════════════════════════════════════════════════════════════════
# Ortak kalıp: her dashboard'da tag'ler TEKİL (auto-binding belirsizliği
# olmasın); her blok yalnız SQL'inin kullandığı değişkenleri taşır; tarih
# filtresi :donem_from/:donem_to accessor'larıyla girer (date_range kuralı).

def build_cost(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b)
    lo, _hi = runner.minmax_date(M, "MONTH")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem",
                    {"from": lo, "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": "today"})
    b["donem"] = "f_donem"

    seg_in = "DIM_SEGMENT IN (:segment)"
    prd_in = "DIM_PRODUCT IN (:urun)"
    aum_in = "DIM_AUM IN (:aum)"
    mrange = "MONTH BETWEEN :donem_from AND :donem_to"
    drange = "DAT BETWEEN :donem_from AND :donem_to"
    last_m = (f"MONTH = (SELECT MAX(MONTH) FROM {M} WHERE {mrange})")
    all_vars = [v_don, v_seg, v_prd, v_aum]

    blocks = [
        _block("kpi_bal", "kpi", "Toplam Bakiye (₺Mr, son ay)",
               f"SELECT ROUND(SUM(BALANCE)/1e9, 2) AS BAKIYE_MLR FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in}",
               all_vars, b, M, width="1/3",
               config={"unit": "Mr ₺", "period": "Son ay"}),
        _block("kpi_rate", "kpi", "Ağırlıklı Ort. Faiz (%, son ay)",
               f"SELECT ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100, 2) AS FAIZ "
               f"FROM {M} WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in}",
               all_vars, b, M, width="1/3",
               config={"unit": "%", "period": "Son ay"}),
        _block("kpi_cust", "kpi", "Müşteri Adedi (son ay)",
               f"SELECT ROUND(SUM(CUST_COUNT)) AS MUSTERI FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in}",
               all_vars, b, M, width="1/3", config={"unit": "adet"}),
        _block("cmb_prod", "combo_chart", "Ürün Bazında Bakiye ve Faiz (son ay)",
               f"SELECT DIM_PRODUCT, ROUND(SUM(BALANCE)/1e9,2) AS BAKIYE_MLR, "
               f"ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS FAIZ_PCT "
               f"FROM {M} WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"GROUP BY DIM_PRODUCT ORDER BY 2 DESC",
               all_vars, b, M),
        _block("hm_rate", "heatmap", "Faiz Isı Haritası — Segment × AUM (son ay)",
               f"SELECT DIM_SEGMENT, DIM_AUM, "
               f"ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS FAIZ_PCT "
               f"FROM {M} WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"AND DIM_SEGMENT IS NOT NULL AND DIM_AUM IS NOT NULL "
               f"GROUP BY DIM_SEGMENT, DIM_AUM ORDER BY 1, 2",
               all_vars, b, M),
        _block("ln_rate", "line_chart", "Günlük Ağırlıklı Ort. Faiz (%)",
               f"SELECT DAT, ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS FAIZ_PCT "
               f"FROM {D} WHERE {drange} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"GROUP BY DAT ORDER BY DAT",
               all_vars, b, D, width="full"),
        _block("bar_aum", "bar_chart", "AUM Bandına Göre Bakiye (₺Mr, son ay)",
               f"SELECT DIM_AUM, ROUND(SUM(BALANCE)/1e9,2) AS BAKIYE_MLR FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"AND DIM_AUM IS NOT NULL GROUP BY DIM_AUM ORDER BY 2 DESC",
               all_vars, b, M),
    ]
    manifest = _manifest_shell(
        "p_dep_cost", "Mevduat Maliyet Analizi",
        "Outstanding Cost Analysis — stok mevduatın maliyet (faiz) kesiti. "
        "Waterfall ve bubble grafikleri blok tipi eklenince taşınacak.",
        [f_don, f_seg, f_prd, f_aum],
        [_section("sec_ozet", "Genel Bakış", blocks[:3]),
         _section("sec_maliyet", "Maliyet Kesitleri", blocks[3:])],
        [M, D])
    return manifest, [M, D]


def build_balance(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_OUT_MONTHLY", f"{sch}.PRISMA_DEP_OUT_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_prd, v_prd = _enum_domain(runner, M, "DIM_PRODUCT", "Ürün", "f_urun", "product_group", b)
    f_aum, v_aum = _enum_domain(runner, M, "DIM_AUM", "AUM Bandı", "f_aum", "other", b)
    lo, _ = runner.minmax_date(D, "DAT")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem",
                    {"from": "today - 180d", "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range",
                 {"from": "today - 180d", "to": "today"})
    b["donem"] = "f_donem"

    seg_in, prd_in, aum_in = ("DIM_SEGMENT IN (:segment)",
                              "DIM_PRODUCT IN (:urun)", "DIM_AUM IN (:aum)")
    mrange = "MONTH BETWEEN :donem_from AND :donem_to"
    drange = "DAT BETWEEN :donem_from AND :donem_to"
    last_m = f"MONTH = (SELECT MAX(MONTH) FROM {M} WHERE {mrange})"
    all_vars = [v_don, v_seg, v_prd, v_aum]

    blocks = [
        _block("kpi_bal", "kpi", "Toplam Bakiye (₺Mr, son ay)",
               f"SELECT ROUND(SUM(BALANCE)/1e9,2) AS MLR FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in}",
               all_vars, b, M, width="1/3", config={"unit": "Mr ₺"}),
        _block("kpi_cust", "kpi", "Müşteri Adedi (son ay)",
               f"SELECT ROUND(SUM(CUST_COUNT)) AS N FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in}",
               all_vars, b, M, width="1/3", config={"unit": "adet"}),
        _block("kpi_seg", "kpi", "Aktif Segment Sayısı (son ay)",
               f"SELECT COUNT(DISTINCT DIM_SEGMENT) AS N FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"AND BALANCE > 0",
               all_vars, b, M, width="1/3", config={"unit": "adet"}),
        _block("ln_bal", "line_chart", "Günlük Toplam Bakiye (₺Mr)",
               f"SELECT DAT, ROUND(SUM(BALANCE)/1e9,2) AS BAKIYE_MLR FROM {D} "
               f"WHERE {drange} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"GROUP BY DAT ORDER BY DAT",
               all_vars, b, D, width="full"),
        _block("bar_prod", "bar_chart", "Ürün Bazında Bakiye (₺Mr, son ay)",
               f"SELECT DIM_PRODUCT, ROUND(SUM(BALANCE)/1e9,2) AS MLR FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"GROUP BY DIM_PRODUCT ORDER BY 2 DESC",
               all_vars, b, M),
        _block("pie_aum", "pie_chart", "AUM Kompozisyonu (son ay)",
               f"SELECT DIM_AUM, ROUND(SUM(BALANCE)/1e9,2) AS MLR FROM {M} "
               f"WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"AND DIM_AUM IS NOT NULL GROUP BY DIM_AUM ORDER BY 2 DESC",
               all_vars, b, M),
        _block("hm_bal", "heatmap", "Bakiye Isı Haritası — Segment × AUM (₺Mr, son ay)",
               f"SELECT DIM_SEGMENT, DIM_AUM, ROUND(SUM(BALANCE)/1e9,2) AS MLR "
               f"FROM {M} WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"AND DIM_SEGMENT IS NOT NULL AND DIM_AUM IS NOT NULL "
               f"GROUP BY DIM_SEGMENT, DIM_AUM ORDER BY 1, 2",
               all_vars, b, M),
        _block("tbl_snap", "data_table", "Son Ay Özeti (ürün × segment)",
               f"SELECT DIM_PRODUCT AS URUN, DIM_SEGMENT AS SEGMENT, "
               f"ROUND(SUM(BALANCE)/1e9,3) AS BAKIYE_MLR, "
               f"ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS FAIZ_PCT, "
               f"ROUND(SUM(CUST_COUNT)) AS MUSTERI "
               f"FROM {M} WHERE {last_m} AND {seg_in} AND {prd_in} AND {aum_in} "
               f"GROUP BY DIM_PRODUCT, DIM_SEGMENT ORDER BY 3 DESC",
               all_vars, b, M, width="full",
               config=_tbl_cols("URUN", "SEGMENT", "BAKIYE_MLR",
                                "FAIZ_PCT", "MUSTERI")),
    ]
    manifest = _manifest_shell(
        "p_dep_balance", "Mevduat Hacim Analizi",
        "Outstanding Balance Analysis — stok mevduatın hacim/müşteri kesiti. "
        "Bridge (waterfall) grafikleri blok tipi eklenince taşınacak.",
        [f_don, f_seg, f_prd, f_aum],
        [_section("sec_ozet", "Genel Bakış", blocks[:3]),
         _section("sec_hacim", "Hacim Kesitleri", blocks[3:])],
        [M, D])
    return manifest, [M, D]


def build_tenor(runner, sch):
    M, D = f"{sch}.PRISMA_DEP_TENOR_MONTHLY", f"{sch}.PRISMA_DEP_TENOR_DAILY"
    b = {}
    f_seg, v_seg = _enum_domain(runner, M, "DIM_SEGMENT", "Segment", "f_segment", "segment", b)
    f_bkt, v_bkt = _enum_domain(runner, M, "DIM_BUCKET", "Vade Kovası", "f_kova", "tenor_bucket", b)
    f_mod = _filter("f_mod", "other", "enum_single", "Vade Modu", "tenor", ["tenor", "dtm"])
    v_mod = _var("mod", "other", "enum_single", "tenor", ["tenor", "dtm"])
    b["mod"] = "f_mod"
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem",
                    {"from": "today - 90d", "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range",
                 {"from": "today - 90d", "to": "today"})
    b["donem"] = "f_donem"

    seg_in, bkt_in = "DIM_SEGMENT IN (:segment)", "DIM_BUCKET IN (:kova)"
    modq = "TENOR_MODE = :mod"
    mrange = "MONTH BETWEEN :donem_from AND :donem_to"
    drange = "DAT BETWEEN :donem_from AND :donem_to"
    last_m = f"MONTH = (SELECT MAX(MONTH) FROM {M} WHERE {mrange})"
    bkt_ord = "TO_NUMBER(REGEXP_SUBSTR(DIM_BUCKET, '^\\d+'))"
    nonempty = "DIM_BUCKET IS NOT NULL"
    all_vars = [v_don, v_seg, v_bkt, v_mod]

    blocks = [
        _block("kpi_wat", "kpi", "Ağırlıklı Ort. Vade (gün, son ay)",
               f"SELECT ROUND(SUM(WT_SUM)/NULLIF(SUM(BALANCE),0),1) AS GUN "
               f"FROM {M} WHERE {last_m} AND {modq} AND {seg_in} AND {bkt_in}",
               all_vars, b, M, width="1/3", config={"unit": "gün"}),
        _block("kpi_rate", "kpi", "Ağırlıklı Ort. Faiz (%, son ay)",
               f"SELECT ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS PCT "
               f"FROM {M} WHERE {last_m} AND {modq} AND {seg_in} AND {bkt_in}",
               all_vars, b, M, width="1/3", config={"unit": "%"}),
        _block("kpi_bal", "kpi", "Toplam Bakiye (₺Mr, son ay)",
               f"SELECT ROUND(SUM(BALANCE)/1e9,2) AS MLR FROM {M} "
               f"WHERE {last_m} AND {modq} AND {seg_in} AND {bkt_in}",
               all_vars, b, M, width="1/3", config={"unit": "Mr ₺"}),
        _block("bar_ladder", "bar_chart", "Vade Merdiveni — Bakiye (₺Mr, son ay)",
               f"SELECT DIM_BUCKET, ROUND(SUM(BALANCE)/1e9,2) AS MLR FROM {M} "
               f"WHERE {last_m} AND {modq} AND {seg_in} AND {bkt_in} AND {nonempty} "
               f"GROUP BY DIM_BUCKET ORDER BY {bkt_ord}",
               all_vars, b, M),
        _block("cmb_ladder", "combo_chart", "Vade Merdiveni — Bakiye ve Faiz (son ay)",
               f"SELECT DIM_BUCKET, ROUND(SUM(BALANCE)/1e9,2) AS BAKIYE_MLR, "
               f"ROUND(SUM(WR_SUM)/NULLIF(SUM(BALANCE),0)*100,2) AS FAIZ_PCT "
               f"FROM {M} WHERE {last_m} AND {modq} AND {seg_in} AND {bkt_in} "
               f"AND {nonempty} GROUP BY DIM_BUCKET ORDER BY {bkt_ord}",
               all_vars, b, M),
        _block("ln_wat", "line_chart", "Günlük Ağırlıklı Ort. Vade (gün)",
               f"SELECT DAT, ROUND(SUM(WT_SUM)/NULLIF(SUM(BALANCE),0),1) AS GUN "
               f"FROM {D} WHERE {drange} AND {modq} AND {seg_in} AND {bkt_in} "
               f"GROUP BY DAT ORDER BY DAT",
               all_vars, b, D, width="full"),
    ]
    manifest = _manifest_shell(
        "p_dep_tenor", "Mevduat Vade Analizi",
        "Outstanding Tenor Analysis — vade merdiveni ve evrim (tenor/dtm "
        "modları). Günlük kova-dağılım stacked-area'sı ve swap hedge overlay "
        "sonraki fazda.",
        [f_don, f_seg, f_bkt, f_mod],
        [_section("sec_ozet", "Genel Bakış", blocks[:3]),
         _section("sec_ladder", "Vade Merdiveni", blocks[3:])],
        [M, D])
    return manifest, [M, D]


def build_rollings(runner, sch):
    A, T = f"{sch}.PRISMA_DEP_ROLL_AGG", f"{sch}.PRISMA_DEP_ROLL_DETAIL"
    b = {}
    f_ccy, v_ccy = _enum_domain(runner, A, "CCY_CODE", "Para Birimi", "f_ccy", "currency", b)
    f_seg, v_seg = _enum_domain(runner, T, "SEGMENT", "Segment", "f_segment", "segment", b)
    lo, hi = runner.minmax_date(A, "ROLL_DATE")
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönüş Aralığı",
                    {"from": lo, "to": hi})
    v_don = _var("donem", "as_of_time", "date_range", {"from": lo, "to": hi})
    b["donem"] = "f_donem"

    rrange = "ROLL_DATE BETWEEN :donem_from AND :donem_to"
    ccy_in = "CCY_CODE IN (:ccy)"
    seg_in = "SEGMENT IN (:segment)"
    bands = ["0-5M", "5M-25M", "25M-50M", "50M-100M", "100M-200M", "200M+"]
    band_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN AUM_BAND = '{bd}' THEN TRY_BAKIYE_TOPLAM ELSE 0 END)/1e6, 1) AS \"{bd}\""
        for bd in bands)

    blocks = [
        _block("kpi_vol", "kpi", "Dönen Toplam Bakiye (₺M)",
               f"SELECT ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6,1) AS M FROM {A} "
               f"WHERE {rrange} AND {ccy_in}",
               [v_don, v_ccy], b, A, width="1/3", config={"unit": "M ₺"}),
        _block("kpi_rate", "kpi", "Dönen Bakiye Ort. Faizi (%)",
               f"SELECT ROUND(SUM(TRY_X_INTRST)/NULLIF(SUM(TRY_BAKIYE_TOPLAM),0),2) "
               f"AS PCT FROM {A} WHERE {rrange} AND {ccy_in}",
               [v_don, v_ccy], b, A, width="1/3", config={"unit": "%"}),
        _block("kpi_cust", "kpi", "Müşteri Adedi",
               f"SELECT SUM(MUSTERI_SAYISI) AS N FROM {A} WHERE {rrange} AND {ccy_in}",
               [v_don, v_ccy], b, A, width="1/3", config={"unit": "adet"}),
        _block("bar_bands", "bar_chart", "Günlük Dönüşler — AUM Bandına Göre (₺M)",
               f"SELECT TO_CHAR(ROLL_DATE, 'DD/MM') AS GUN, {band_cases} "
               f"FROM {A} WHERE {rrange} AND {ccy_in} "
               f"GROUP BY ROLL_DATE ORDER BY ROLL_DATE",
               [v_don, v_ccy], b, A, width="full",
               config={"stacked": True}),
        _block("pie_seg", "pie_chart", "Segment Dağılımı (₺M)",
               f"SELECT SEGMENT, ROUND(SUM(TRY_BALANCE)/1e6,1) AS M FROM {T} "
               f"WHERE {rrange} AND {ccy_in} AND {seg_in} "
               f"GROUP BY SEGMENT ORDER BY 2 DESC",
               [v_don, v_ccy, v_seg], b, T),
        _block("ln_daily", "line_chart", "Günlük Dönen Bakiye (₺M)",
               f"SELECT ROLL_DATE, ROUND(SUM(TRY_BAKIYE_TOPLAM)/1e6,1) AS M "
               f"FROM {A} WHERE {rrange} AND {ccy_in} "
               f"GROUP BY ROLL_DATE ORDER BY ROLL_DATE",
               [v_don, v_ccy], b, A),
        _block("tbl_top", "data_table", "En Büyük 50 Dönüş (müşteri maskeli)",
               f"SELECT FULL_NM AS MUSTERI, SEGMENT, CCY_CODE, "
               f"ROUND(TRY_BALANCE/1e6,2) AS BAKIYE_M, "
               f"ROUND(INTRST_RT,2) AS FAIZ, DTM AS KALAN_GUN, "
               f"TO_CHAR(ROLL_DATE,'DD/MM/YYYY') AS DONUS "
               f"FROM {T} WHERE {rrange} AND {ccy_in} AND {seg_in} "
               f"ORDER BY TRY_BALANCE DESC FETCH FIRST 50 ROWS ONLY",
               [v_don, v_ccy, v_seg], b, T, width="full",
               config=_tbl_cols("MUSTERI", "SEGMENT", "CCY_CODE", "BAKIYE_M",
                                "FAIZ", "KALAN_GUN", "DONUS")),
    ]
    manifest = _manifest_shell(
        "p_dep_rollings", "Mevduat Dönüşleri",
        "Future Deposit Rollings — vadesi dolan mevduatlar (ileriye dönük "
        "pencere; pencere deposits_pipeline koşusuyla belirlenir). Müşteri "
        "adları KVKK maskelidir. DTM histogramı sonraki fazda.",
        [f_don, f_ccy, f_seg],
        [_section("sec_ozet", "Pencere Özeti", blocks[:3]),
         _section("sec_detay", "Dönüş Kesitleri", blocks[3:])],
        [A, T])
    return manifest, [A, T]


def build_newbiz(runner, sch):
    N, O = f"{sch}.PRISMA_NP_FLOW_DAILY", f"{sch}.PRISMA_NP_OUT_DAILY"
    b = {}
    f_ccy, v_ccy = _enum_domain(runner, N, "CCY_CODE", "Para Birimi", "f_ccy", "currency", b)
    f_seg, v_seg = _enum_domain(runner, N, "SUB_SEGMENT", "Alt Segment", "f_segment", "segment", b)
    f_ten, v_ten = _enum_domain(runner, N, "TENOR_GRP", "Vade Grubu", "f_vade", "tenor_bucket", b)
    f_aum, v_aum = _enum_domain(runner, N, "AUM_BAND", "AUM Bandı", "f_aum", "other", b)
    f_don = _filter("f_donem", "as_of_time", "date_range", "Dönem",
                    {"from": "today - 90d", "to": "today"})
    v_don = _var("donem", "as_of_time", "date_range",
                 {"from": "today - 90d", "to": "today"})
    b["donem"] = "f_donem"

    drange = "DAT BETWEEN :donem_from AND :donem_to"
    ins = ("CCY_CODE IN (:ccy)", "SUB_SEGMENT IN (:segment)",
           "TENOR_GRP IN (:vade)", "AUM_BAND IN (:aum)")
    W = " AND ".join((drange,) + ins)
    all_vars = [v_don, v_ccy, v_seg, v_ten, v_aum]

    blocks = [
        _block("kpi_vol", "kpi", "Bağlanan Toplam Hacim (₺Mr)",
               f"SELECT ROUND(SUM(NP_HACIM)/1e3,2) AS MLR FROM {N} WHERE {W}",
               all_vars, b, N, width="1/3", config={"unit": "Mr ₺"}),
        _block("kpi_rate", "kpi", "Ort. Bileşik Faiz (%)",
               f"SELECT ROUND(SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0),2) AS PCT "
               f"FROM {N} WHERE {W}",
               all_vars, b, N, width="1/3", config={"unit": "%"}),
        _block("kpi_new", "kpi", "Yeni Para (₺Mr)",
               f"SELECT ROUND(SUM(YENI_PARA)/1e3,2) AS MLR FROM {N} WHERE {W}",
               all_vars, b, N, width="1/3", config={"unit": "Mr ₺"}),
        _block("cmb_daily", "combo_chart", "Günlük Hacim ve Bileşik Faiz",
               f"SELECT DAT, ROUND(SUM(NP_HACIM),1) AS HACIM_M, "
               f"ROUND(SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0),2) AS FAIZ_PCT "
               f"FROM {N} WHERE {W} GROUP BY DAT ORDER BY DAT",
               all_vars, b, N, width="full"),
        _block("bar_seg", "bar_chart", "Alt Segment Bazında Hacim (₺M)",
               f"SELECT SUB_SEGMENT, ROUND(SUM(NP_HACIM),1) AS M FROM {N} "
               f"WHERE {W} GROUP BY SUB_SEGMENT ORDER BY 2 DESC",
               all_vars, b, N),
        _block("pie_ccy", "pie_chart", "Para Birimi Dağılımı (₺M)",
               f"SELECT CCY_CODE, ROUND(SUM(NP_HACIM),1) AS M FROM {N} "
               f"WHERE {W} GROUP BY CCY_CODE ORDER BY 2 DESC",
               all_vars, b, N),
        _block("hm_pc", "heatmap", "Bileşik Faiz — Kâr Merkezi × AUM",
               f"SELECT RELATED_PC, AUM_BAND, "
               f"ROUND(SUM(WC_SUM)/NULLIF(SUM(NP_HACIM),0),2) AS FAIZ_PCT "
               f"FROM {N} WHERE {W} GROUP BY RELATED_PC, AUM_BAND ORDER BY 1, 2",
               all_vars, b, N, width="full"),
        _block("ln_stock", "line_chart", "Stok Bakiye Evrimi (₺Mr, ortak şema)",
               f"SELECT DAT, ROUND(SUM(BAL_SUM)/1e3,2) AS MLR FROM {O} "
               f"WHERE {drange} GROUP BY DAT ORDER BY DAT",
               [v_don], b, O),
    ]
    manifest = _manifest_shell(
        "p_dep_newbiz", "Yeni Üretim — Hacim ve Fiyatlama",
        "New Business Volume & Pricing — bağlanan mevduat akışı. Faizler "
        "bileşik (%WC_SUM/NP_HACIM). Bubble grafikleri ve rate-volume "
        "curve blok tipi eklenince taşınacak.",
        [f_don, f_ccy, f_seg, f_ten, f_aum],
        [_section("sec_ozet", "Dönem Özeti", blocks[:3]),
         _section("sec_akis", "Akış ve Fiyatlama", blocks[3:])],
        [N, O])
    return manifest, [N, O]


BUILDERS = {
    "p_dep_cost": build_cost,
    "p_dep_balance": build_balance,
    "p_dep_tenor": build_tenor,
    "p_dep_rollings": build_rollings,
    "p_dep_newbiz": build_newbiz,
}


def _manifest_shell(pid, title, description, filters, sections, tables):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": pid,
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "meta": {
            "title": title,
            "eyebrow": "Deposits",
            "date": date.today().strftime("%d.%m.%Y"),
            "description": description,
        },
        "basket": [
            {"table": t, "alias": t.split(".")[-1].lower(), "column_concepts": {}}
            for t in tables
        ],
        "filters": filters,
        "blocks": sections,
        "uploads": [],
        "bound_experts": [],
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
    config + data_source doldur. Hata veren blok boş config'le kalır (editörde
    'Çalıştır' ile tekrar denenebilir); hatalar listeyle döner."""
    from presentations.blocks.schema import Block, Variable  # noqa: F401
    from presentations.variables.resolver import resolve_variables
    from presentations.sql.binder import expand_binds
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    from presentations import duck

    errors = []
    for blk in iter_leaf_blocks(manifest):
        if not blk.get("query"):
            continue
        try:
            # routes._execute_manual_block_sql'deki stand-in ile aynı şekil.
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
    """Hazırlık'ın okuyacağı minimal scope contract (lazy — cron yok, statik)."""
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

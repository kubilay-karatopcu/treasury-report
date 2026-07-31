# -*- coding: utf-8 -*-
"""fi_desk_dashboards.py — FI masası verisini PRISMA'ya tanıtır (Faz 3).

OFİSTE tek koşu (Spyder → Run/F5; argparse YOK, davranış KONFİG
sabitlerinden). `jobs/fi_desk_schema.py` koşulmuş ve tablolar dolmaya
başlamış olmalı. Ne yapar:

  1. TABLO DOKÜMANLARI — V_FI_OFFER_CURRENT (raporlamanın ana kaynağı),
     FI_OFFER_EVENTS (zaman damgalı geçmiş) ve FI_OFFER_SCHEDULE (ödeme
     planı) için Prisma TableDoc'larını S3'e upsert eder → tablolar sunum
     editörünün basket/katalog akışında ve LLM bağlamında görünür olur.

  2. DASHBOARD — `p_fi_desk` sunumunu (3 sayfa) S3 manifest'ine yazar:
       Özet       huni (adet + USD mio), ürün bazlı hacim (statü yığılı),
                  lender Top-15, region dağılımı, son kayıtlar tablosu
       Fiyatlama  maliyet haritası (vade × all-in, boyut=USD), ürün bazlı
                  ağırlıklı all-in, WON vs LOST karşılaştırması
       Vade & ESG final maturity profili, anapara itfa takvimi (ödeme
                  planından), ESG payı ve tipi
     Filtreler: ürün, reporting status, para birimi, lender — hepsi
     enum_multi, blok değişkenlerine semantic_tag ile bağlanır.

Yalnız ONAYLI son durum raporlanır; bekleyen event'ler dashboard'a girmez.
"Current" ilişkisi VIEW'A BAĞLANMAZ: EDW kişisel şemasında CREATE VIEW
yetkisi olmayabiliyor (ORA-01031) — bloklar jobs/fi_desk_schema.py::
CURRENT_SELECT'i INLINE alt-sorgu olarak taşır, basket base tabloları
(FI_DEALS + FI_OFFER_EVENTS + FI_OFFER_SCHEDULE) içerir. View mevcutsa
yalnız dokümanı yayınlanır (ad-hoc SQL kolaylığı). Statik: veri tazelemek =
uygulamada onay akışının işlemesi; manifest yeniden üretim gerektirmez
(SQL'ler her apply-filters'ta yeniden koşar). deposits_dashboards.py ile
aynı manifest yapı taşları; blok SQL lehçesi Oracle'dır ve duck_cache açık
olduğundan üretimde oracle_duck çevirisiyle oturum DuckDB'sinde koşar —
yalnız çevirmenin desteklediği yapılar kullanılır (NVL/TO_CHAR/ROWNUM/
TRUNC(SYSDATE)).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("fi_desk_dashboards")

# ─────────────────────────────────────────────────────────────────────────
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ─────────────────────────────────────────────────────────────────────────
SICIL = "A16438"        # SİTEYE GİRİŞ sicili — sunumun SAHİBİ (düzenlenebilir
                        # kopya "Rapor Şablonları"nda bu kullanıcıda durur;
                        # DB bağlantı kullanıcısı DEĞİL). Masaya yayın sahiplik
                        # üzerinden DEĞİL, aşağıdaki snapshot+uzman bağıyla olur.
SCHEMA: str | None = None   # FI_* tablolarının şeması (None → bağlantı kullanıcısı)
EXPERTS: list[str] | str = ["fi"]  # bağlanacak uzman id'leri (seed_fi_expert.py
                                   # önce koşulmuş olmalı; boş → bağlama yok)
SKIP_FILL = False       # True → blok SQL'leri koşulmaz, config'ler boş kalır
PUBLISH_DOCS = True     # TableDoc'ları S3'e yaz
PUBLISH_DASHBOARD = True  # p_fi_desk manifest'ini S3'e yaz
PUBLISH_SNAPSHOT = True   # manifest'i dondurup EXPERTS uzman(lar)ına bağlı
                          # snapshot yaz — MASA YAYINI budur: uzmanı görebilen
                          # herkes (access_scope.read departmanları) panoyu
                          # uzman sayfasındaki ızgaradan açar. Önceki otomatik
                          # snapshot silinir (birikme olmaz).

S3_MANIFEST_KEY = "prisma-treasury/presentations/{sicil}/{pid}/manifest.json"
PID = "p_fi_desk"

REPORTING_STATUSES = ["BIDDING", "PENDING", "REALIZED", "UNREALIZED"]


def _load_schema_defs():
    """jobs/fi_desk_schema.py sabitleri (CURRENT_SELECT tek kaynak orada)."""
    spec = importlib.util.spec_from_file_location(
        "fi_desk_schema_defs", Path(__file__).resolve().parent / "fi_desk_schema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────────────────
# Yardımcılar (deposits_dashboards.py yapı taşlarının FI kopyası)
# ─────────────────────────────────────────────────────────────────────────
def _as_list(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return [str(v).strip() for v in value if str(v).strip()]


class Runner:
    def __init__(self, dc, con):
        self.dc, self.con = dc, con

    def query(self, sql: str, params: dict | None = None):
        return self.dc.edw_query_to_pandas(self.con, sql, params=params or {})

    def distinct(self, table: str, col: str) -> list[str]:
        df = self.query(f"SELECT DISTINCT {col} AS V FROM {table} "
                        f"WHERE {col} IS NOT NULL ORDER BY 1")
        return [str(v) for v in df["V"].tolist() if str(v).strip() != ""]

    def rowcount(self, table: str) -> int:
        return int(self.query(f"SELECT COUNT(*) AS N FROM {table}").iloc[0]["N"])


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
    "bar_chart":   {"categories": [], "series": []},
    "combo_chart": {"categories": [], "series": [],
                    "left_axis_title": "", "right_axis_title": ""},
    "pie_chart":   {"labels": [], "values": []},
    "data_table":  {"columns": [], "rows": []},
    "scatter_chart": {"points": [], "x_title": "", "y_title": ""},
}


def _block(bid, btype, title, query, variables, bindings, source_tables,
           width="1/2", config=None):
    cfg = dict(_SEED_CONFIG.get(btype, {}))
    cfg.update(config or {})
    return {
        "id": bid, "type": btype, "title": title, "locked": False,
        "width": width, "query": query.strip(),
        "variables": variables,
        "variable_bindings": {v["name"]: {"from_filter": bindings[v["name"]]}
                              for v in variables if v["name"] in bindings},
        "source_tables": list(source_tables),
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


def _manifest_shell(pid, title, description, filters, sections, tables, pages):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": pid, "version": 1, "created_at": now, "updated_at": now,
        "meta": {"title": title, "eyebrow": "FI Masası",
                 "date": date.today().strftime("%d.%m.%Y"),
                 "description": description},
        # duck_cache: FI hacmi küçük (masa girişi) — tablolar ilk kullanımda
        # oturum DuckDB'sine çekilir, filtre değişimi sıfır Oracle turu.
        "basket": [{"table": t, "alias": t.split(".")[-1].lower(),
                    "column_concepts": {}, "duck_cache": True}
                   for t in tables],
        "filters": filters,
        "blocks": sections,
        "pages": pages,
        "uploads": [],
        "bound_experts": [],
    }


# ─────────────────────────────────────────────────────────────────────────
# Filtre domain'leri — veri boşsa lookup tablolarından (kurulum günü senaryosu)
# ─────────────────────────────────────────────────────────────────────────
def load_product_types() -> list[str]:
    matrix = json.loads((REPO_ROOT / "fi_desk" / "field_matrix.json")
                        .read_text("utf-8"))
    return list(matrix["products"].keys())


def discover_domains(runner: Runner, sch: str, V: str) -> dict:
    ccy = runner.distinct(V, "CURRENCY")
    if not ccy:
        df = runner.query(
            f"SELECT VALUE_CD AS V FROM {sch}.FI_LU_LIST "
            "WHERE LIST_NAME = 'CURRENCY' AND ACTIVE_FLG = 1 ORDER BY SORT_NO")
        ccy = [str(v) for v in df["V"].tolist()]
    lender = runner.distinct(V, "LENDER_BANK")
    if not lender:
        df = runner.query(
            f"SELECT BANK_NM AS V FROM {sch}.FI_LU_BANK "
            "WHERE ACTIVE_FLG = 1 ORDER BY BANK_NM")
        lender = [str(v) for v in df["V"].tolist()]
    return {
        "urun": load_product_types(),
        "rapor": REPORTING_STATUSES,
        "ccy": ccy,
        "lender": lender,
    }


# ─────────────────────────────────────────────────────────────────────────
# Dashboard kurucusu
# ─────────────────────────────────────────────────────────────────────────
# Tüm bloklar aynı 4 filtreyi kullanır; savunmacı NULL toleransı deposits
# deseniyle aynı (değer NULL ise satır filtre dışı bırakılmaz).
W = ("(PRODUCT_TYPE IN (:urun) OR PRODUCT_TYPE IS NULL) AND "
     "(REPORTING_STATUS IN (:rapor)) AND "
     "(CURRENCY IN (:ccy) OR CURRENCY IS NULL) AND "
     "(LENDER_BANK IN (:lender) OR LENDER_BANK IS NULL)")


def build_dashboard(runner: Runner, sch: str) -> tuple[dict, list[str]]:
    # Current ilişkisi INLINE (view yetkisinden bağımsız — modül docstring'i).
    defs = _load_schema_defs()
    V = "(" + defs.CURRENT_SELECT.format(s=f"{sch}.") + ")"
    S = f"{sch}.FI_OFFER_SCHEDULE"
    base_tables = [f"{sch}.FI_DEALS", f"{sch}.FI_OFFER_EVENTS", S]
    dom = discover_domains(runner, sch, V)

    bindings: dict[str, str] = {}
    filters, variables = [], []
    for name, tag, label in (
            ("urun", "product_group", "Ürün"),
            ("rapor", "other", "Reporting Status"),
            ("ccy", "currency", "Para Birimi"),
            ("lender", "counterparty", "Lender")):
        fid = f"f_{name}"
        filters.append(_filter(fid, tag, "enum_multi", label,
                               dom[name], dom[name]))
        variables.append(_var(name, tag, "enum_multi", dom[name], dom[name]))
        bindings[name] = fid

    def blk(bid, btype, title, query, source=(), width="1/2", config=None):
        return _block(bid, btype, title, query, [dict(v) for v in variables],
                      bindings, source or base_tables[:2], width=width,
                      config=config)

    # ── Sayfa 1: Özet ───────────────────────────────────────────────────
    b_funnel = blk("b_fi_funnel", "combo_chart", "İşlem Hunisi",
        f"""SELECT REPORTING_STATUS AS DURUM,
       COUNT(*) AS ADET,
       ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
FROM {V} WHERE {W}
GROUP BY REPORTING_STATUS
ORDER BY CASE REPORTING_STATUS WHEN 'BIDDING' THEN 1 WHEN 'PENDING' THEN 2
         WHEN 'REALIZED' THEN 3 ELSE 4 END""",
        config={"series": [{"name": "ADET", "kind": "bar", "axis": "left"},
                           {"name": "USD_MIO", "kind": "line", "axis": "right"}],
                "left_axis_title": "Adet",
                "right_axis_title": "USD (mio)"})

    status_cases = ", ".join(
        f"ROUND(SUM(CASE WHEN REPORTING_STATUS = '{s}' THEN USD_EQV ELSE 0 END)"
        f"/1000000, 1) AS {s}" for s in REPORTING_STATUSES)
    b_urun = blk("b_fi_urun_hacim", "bar_chart", "Ürün Bazında Hacim (USD mio)",
        f"""SELECT PRODUCT_TYPE, {status_cases}
FROM {V} WHERE {W}
GROUP BY PRODUCT_TYPE ORDER BY SUM(USD_EQV) DESC""",
        config={"stacked": True})

    b_lender = blk("b_fi_lender", "bar_chart", "Lender Bazında Hacim — Top 15",
        f"""SELECT * FROM (
  SELECT LENDER_BANK, ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
  FROM {V} WHERE {W}
  GROUP BY LENDER_BANK ORDER BY 2 DESC
) WHERE ROWNUM <= 15""",
        config={"horizontal": True})

    b_region = blk("b_fi_region", "pie_chart", "Lender Region Dağılımı (USD)",
        f"""SELECT NVL(LENDER_REGION, 'Bilinmiyor') AS REGION,
       ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
FROM {V} WHERE {W}
GROUP BY NVL(LENDER_REGION, 'Bilinmiyor') ORDER BY 2 DESC""",
        config={"donut": True})

    b_son = blk("b_fi_son_kayitlar", "data_table", "Son Onaylı Kayıtlar",
        f"""SELECT * FROM (
  SELECT DEAL_ID, PRODUCT_TYPE, BORROWER_BANK, LENDER_BANK, DEAL_STATUS,
         REPORTING_STATUS, CURRENCY, FUNDING_AMT,
         ROUND(USD_EQV/1000000, 2) AS USD_MIO,
         TO_CHAR(VALUE_DT, 'YYYY-MM-DD') AS VALUE_DT,
         TO_CHAR(MATURITY_DT, 'YYYY-MM-DD') AS MATURITY_DT,
         ALL_IN_RATE_BPS,
         TO_CHAR(EVENT_TS, 'YYYY-MM-DD HH24:MI') AS SON_ISLEM
  FROM {V} WHERE {W}
  ORDER BY EVENT_TS DESC
) WHERE ROWNUM <= 100""",
        width="full",
        config=_tbl_cols("DEAL_ID", "PRODUCT_TYPE", "BORROWER_BANK",
                         "LENDER_BANK", "DEAL_STATUS", "REPORTING_STATUS",
                         "CURRENCY", "FUNDING_AMT", "USD_MIO", "VALUE_DT",
                         "MATURITY_DT", "ALL_IN_RATE_BPS", "SON_ISLEM"))

    # ── Sayfa 2: Fiyatlama ──────────────────────────────────────────────
    b_harita = blk("b_fi_maliyet_harita", "scatter_chart",
        "Maliyet Haritası — Vade × All-in (boyut: USD mio)",
        f"""SELECT LENDER_BANK, TENOR_DAYS, ALL_IN_RATE_BPS,
       ROUND(USD_EQV/1000000, 2) AS USD_MIO
FROM {V}
WHERE {W} AND ALL_IN_RATE_BPS IS NOT NULL AND TENOR_DAYS IS NOT NULL""",
        width="full",
        config={"x_title": "Vade (gün)", "y_title": "All-in (bps)"})

    b_urun_maliyet = blk("b_fi_urun_maliyet", "bar_chart",
        "Ürün Bazında Ağırlıklı All-in (bps)",
        f"""SELECT PRODUCT_TYPE,
       ROUND(SUM(ALL_IN_RATE_BPS * USD_EQV) / NULLIF(SUM(USD_EQV), 0), 0)
         AS ALL_IN_BPS
FROM {V} WHERE {W} AND ALL_IN_RATE_BPS IS NOT NULL
GROUP BY PRODUCT_TYPE ORDER BY 2 DESC""")

    b_won_lost = blk("b_fi_won_lost", "bar_chart",
        "WON vs LOST — Ağırlıklı All-in (bps)",
        f"""SELECT PRODUCT_TYPE,
       ROUND(SUM(CASE WHEN DEAL_STATUS = 'WON' THEN ALL_IN_RATE_BPS * USD_EQV END)
             / NULLIF(SUM(CASE WHEN DEAL_STATUS = 'WON' THEN USD_EQV END), 0), 0)
         AS WON_BPS,
       ROUND(SUM(CASE WHEN DEAL_STATUS = 'LOST' THEN ALL_IN_RATE_BPS * USD_EQV END)
             / NULLIF(SUM(CASE WHEN DEAL_STATUS = 'LOST' THEN USD_EQV END), 0), 0)
         AS LOST_BPS
FROM {V} WHERE {W} AND ALL_IN_RATE_BPS IS NOT NULL AND DEAL_STATUS IS NOT NULL
GROUP BY PRODUCT_TYPE ORDER BY 1""")

    # ── Sayfa 3: Vade & ESG ─────────────────────────────────────────────
    b_maturity = blk("b_fi_maturity", "bar_chart",
        "Final Maturity Profili (USD mio)",
        f"""SELECT TO_CHAR(MATURITY_DT, 'YYYY') AS YIL,
       ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
FROM {V} WHERE {W} AND MATURITY_DT IS NOT NULL
GROUP BY TO_CHAR(MATURITY_DT, 'YYYY') ORDER BY 1""")

    b_itfa = blk("b_fi_itfa", "bar_chart",
        "Anapara İtfa Takvimi — Amortized (mio, işlem ccy)",
        f"""SELECT TO_CHAR(s.PAY_DT, 'YYYY') AS YIL,
       ROUND(SUM(s.PRINCIPAL_AMT)/1000000, 1) AS ANAPARA_MIO
FROM {S} s
JOIN (SELECT EVENT_ID FROM {V} WHERE {W}) c ON c.EVENT_ID = s.EVENT_ID
GROUP BY TO_CHAR(s.PAY_DT, 'YYYY') ORDER BY 1""",
        source=base_tables)

    b_esg = blk("b_fi_esg", "pie_chart", "ESG Payı (USD)",
        f"""SELECT NVL(SUSTAINABILITY_FLG, 'Bilinmiyor') AS ESG,
       ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
FROM {V} WHERE {W}
GROUP BY NVL(SUSTAINABILITY_FLG, 'Bilinmiyor')""",
        config={"donut": True})

    b_esg_tip = blk("b_fi_esg_tip", "bar_chart", "ESG Tipi (USD mio)",
        f"""SELECT ESG_TYPE, ROUND(SUM(USD_EQV)/1000000, 1) AS USD_MIO
FROM {V} WHERE {W} AND SUSTAINABILITY_FLG = 'YES' AND ESG_TYPE IS NOT NULL
GROUP BY ESG_TYPE ORDER BY 2 DESC""")

    sections = [
        _section("s_fi_ozet", "Özet", [b_funnel, b_urun, b_lender, b_region,
                                       b_son], page="pg_ozet"),
        _section("s_fi_fiyat", "Fiyatlama", [b_harita, b_urun_maliyet,
                                             b_won_lost], page="pg_fiyat"),
        _section("s_fi_vade", "Vade & ESG", [b_maturity, b_itfa, b_esg,
                                             b_esg_tip], page="pg_vade"),
    ]
    pages = [{"id": "pg_ozet", "title": "Özet"},
             {"id": "pg_fiyat", "title": "Fiyatlama"},
             {"id": "pg_vade", "title": "Vade & ESG"}]

    manifest = _manifest_shell(
        PID, "FI Masası — İşlem Panosu",
        "FI masası veri girişinin (onaylı kayıtlar) raporlama panosu: huni, "
        "hacim kırılımları, fiyatlama karşılaştırmaları, vade ve ESG.",
        filters, sections, base_tables, pages)
    return manifest, base_tables


# ─────────────────────────────────────────────────────────────────────────
# Tablo dokümanları
# ─────────────────────────────────────────────────────────────────────────
def _col(type_, desc, tag=None, role=None, filterable=None, aggregatable=False):
    d = {"type": type_, "description": desc}
    if tag:
        d.update(filterable=True, filter_role=role or "dimension",
                 suggested_semantic_tag=tag)
    if filterable is not None:
        d["filterable"] = filterable
    if aggregatable:
        d["aggregatable"] = True
    return d


def build_table_docs(runner: Runner, sch: str,
                     include_view: bool = True) -> list[dict]:
    """include_view: V_FI_OFFER_CURRENT şemada gerçekten varsa True —
    CREATE VIEW yetkisi olmayan kurulumlarda view yoktur, dokümanı da
    yayınlanmaz (current mantığı FI_OFFER_EVENTS dokümanında tariflidir)."""
    docs = []

    def _core(name, desc, partition, cols):
        # TableDoc kuralı: suggested_semantic_tag varsa suggested_variable
        # da zorunlu — kolon adından türet (deposits_pipeline ile aynı).
        for cname, cdoc in cols.items():
            if cdoc.get("suggested_semantic_tag") and not cdoc.get("suggested_variable"):
                cdoc["suggested_variable"] = cname.lower()[:40]
        try:
            n = runner.rowcount(f"{sch}.{name}")
        except Exception:
            n = None
        docs.append({"table": name, "schema": sch, "description": desc,
                     "partition_column": partition,
                     "estimated_total_rows": n, "columns": cols})

    _core("FI_DEALS",
          "FI masası üst işlem kimliği: borçlanma ihtiyacı (borrower + ürün). "
          "Aynı DEAL_ID'ye birden fazla lender teklifi (FI_OFFER_EVENTS "
          "satırları) bağlanır. Küçük başlık tablosudur; hacim/fiyat ölçüleri "
          "event tablosundadır.",
          "CREATED_TS", {
              "DEAL_ID": _col("VARCHAR2(24)", "Üst işlem kimliği", tag="deal_id"),
              "BORROWER_BANK": _col("VARCHAR2(128)", "Borçlanan banka (varsayılan biz; piyasa istihbaratında rakip)", tag="counterparty"),
              "PRODUCT_TYPE": _col("VARCHAR2(32)", "Ürün (TRADE_LOAN, EUROBOND, ...)", tag="product_group"),
              "DEAL_LABEL": _col("VARCHAR2(256)", "Serbest açıklama", filterable=False),
              "CREATED_TS": _col("DATE", "Kayıt zamanı", tag="as_of_time", role="time_axis"),
              "CREATED_BY": _col("VARCHAR2(16)", "Kaydı açan sicil", tag="user_id"),
          })

    if include_view:
        _core("V_FI_OFFER_CURRENT",
              "FI masası (Muhabir Bankacılık ve Yapılandırılmış Fonlama) işlem "
              "kayıtlarının RAPORLAMA görünümü: teklif başına EN SON ONAYLI "
              "snapshot + türetilen kolonlar. Onay bekleyen değişiklikler burada "
              "GÖRÜNMEZ. Satır greni = teklif (deal × lender); aynı DEAL_ID "
              "birden fazla lender teklifi taşıyabilir. Kaynak: fi_desk veri "
              "giriş uygulaması (docs/FI_DESK_SPEC.md).",
              "EVENT_TS", {
                  "DEAL_ID": _col("VARCHAR2(24)", "Üst işlem kimliği (borçlanma ihtiyacı)", tag="deal_id"),
                  "OFFER_ID": _col("VARCHAR2(24)", "Teklif kimliği (deal × lender)", filterable=False),
                  "EVENT_SEQ": _col("NUMBER", "Onaylı snapshot'ın sıra no'su", filterable=False),
                  "EVENT_TS": _col("DATE", "Onaylı son işlemin zamanı", tag="as_of_time", role="time_axis"),
                  "PRODUCT_TYPE": _col("VARCHAR2(32)", "Ürün (TRADE_LOAN, EUROBOND, ...)", tag="product_group"),
                  "BORROWER_BANK": _col("VARCHAR2(128)", "Borçlanan banka (varsayılan biz; piyasa istihbaratı girişlerinde rakip)", tag="counterparty"),
                  "LENDER_BANK": _col("VARCHAR2(128)", "Fonlayan banka / yatırımcı", tag="counterparty"),
                  "LENDER_COUNTRY": _col("VARCHAR2(64)", "Lender ülkesi", tag="region"),
                  "LENDER_REGION": _col("VARCHAR2(64)", "Lender bölgesi (ülkeden türetilir)", tag="region"),
                  "GROUP_COMPANY": _col("VARCHAR2(128)", "Lender grup şirketi", tag="counterparty"),
                  "DEAL_STATUS": _col("VARCHAR2(16)", "BIDDING / WON / LOST (statüsüz ürünlerde NULL)", tag="other"),
                  "REPORTING_STATUS": _col("VARCHAR2(16)", "Türetilmiş: BIDDING / PENDING / REALIZED / UNREALIZED (WON + value date geçmişse REALIZED)", tag="other"),
                  "OFFER_DT": _col("DATE", "Teklif tarihi", tag="trade_time"),
                  "CURRENCY": _col("VARCHAR2(8)", "İşlem para birimi", tag="currency"),
                  "FUNDING_AMT": _col("NUMBER", "Tutar (işlem ccy)", aggregatable=True),
                  "USD_EQV": _col("NUMBER", "USD karşılığı — hacim toplamları bundan", aggregatable=True),
                  "VALUE_DT": _col("DATE", "Valör", tag="value_time"),
                  "MATURITY_DT": _col("DATE", "Final maturity", tag="maturity"),
                  "TENOR_DAYS": _col("NUMBER", "Türetilmiş vade (gün) = MATURITY - VALUE; Amortized'da WAL için FI_OFFER_SCHEDULE kullan", aggregatable=True),
                  "REPAYMENT_SCHEDULE": _col("VARCHAR2(16)", "BULLET / AMORTIZED", tag="other"),
                  "COVERAGE_FLG": _col("VARCHAR2(4)", "Coverage var mı (YES/NO)", tag="other"),
                  "COVERAGE_PROVIDER": _col("VARCHAR2(64)", "QNB/EBRD/IFC/ADB/ECA/OTHER", tag="other"),
                  "RATE_TYPE": _col("VARCHAR2(16)", "FIXED / FLOATING", tag="other"),
                  "FIXED_RATE_BPS": _col("NUMBER", "Sabit faiz (bps)", aggregatable=True),
                  "FLOAT_BASE_RATE": _col("VARCHAR2(32)", "Değişken baz oran (SOFR_3M, ...)", tag="other"),
                  "FIXING_DT": _col("DATE", "Floating fixing tarihi (varsayılan value date - 2 takvim günü)", filterable=False),
                  "FLOAT_SPREAD_BPS": _col("NUMBER", "Lender spread'i (bps)", aggregatable=True),
                  "COVERAGE_RATE_BPS": _col("NUMBER", "Coverage maliyeti (bps)", aggregatable=True),
                  "ALL_IN_RATE_BPS": _col("NUMBER", "Toplam maliyet (bps) — karşılaştırmalar hacim ağırlıklı olmalı (Σr·USD/ΣUSD)", aggregatable=True),
                  "ALL_IN_FIXED_USD_RATE": _col("NUMBER", "USD sabit eşdeğer toplam maliyet", aggregatable=True),
                  "BUSINESS_SEGMENT": _col("VARCHAR2(32)", "Underlying işin segmenti", tag="segment"),
                  "SUSTAINABILITY_FLG": _col("VARCHAR2(4)", "ESG işlemi mi (YES/NO)", tag="other"),
                  "ESG_TYPE": _col("VARCHAR2(16)", "UOP / SLL / TRANSITION", tag="other"),
                  "ESG_ELIGIBILITY": _col("VARCHAR2(32)", "UoP alt sınıfı (GREEN/SOCIAL/BLUE/...)", tag="other"),
              })

    _core("FI_OFFER_EVENTS",
          "FI masası APPEND-ONLY event geçmişi: her giriş / düzenleme / statü "
          "değişikliği zaman damgalı TAM snapshot satırıdır (EVENT_SEQ artar). "
          "Onay akışı analizi (PENDING süresi, red oranı) ve teklif→kazanım "
          "hunisinin zaman boyutu buradan; güncel durum için "
          "V_FI_OFFER_CURRENT kullan.",
          "EVENT_TS", {
              "EVENT_ID": _col("NUMBER", "Global event kimliği", filterable=False),
              "OFFER_ID": _col("VARCHAR2(24)", "Teklif kimliği", filterable=False),
              "DEAL_ID": _col("VARCHAR2(24)", "Üst işlem kimliği", tag="deal_id"),
              "EVENT_SEQ": _col("NUMBER", "Teklif içi sıra (1 = ilk giriş)", filterable=False),
              "EVENT_TYPE": _col("VARCHAR2(16)", "ENTRY / EDIT / STATUS_CHANGE", tag="other"),
              "EVENT_TS": _col("DATE", "Event zamanı", tag="as_of_time", role="time_axis"),
              "EVENT_USER": _col("VARCHAR2(16)", "Giren kullanıcı sicili", tag="user_id"),
              "APPROVAL_STATUS": _col("VARCHAR2(16)", "PENDING / APPROVED / REJECTED", tag="other"),
              "APPROVED_BY": _col("VARCHAR2(16)", "Onaylayan/reddeden sicil", tag="user_id"),
              "APPROVED_TS": _col("DATE", "Onay/red zamanı", filterable=False),
              "DEAL_STATUS": _col("VARCHAR2(16)", "Snapshot'taki deal statüsü", tag="other"),
              "LENDER_BANK": _col("VARCHAR2(128)", "Lender", tag="counterparty"),
              "CURRENCY": _col("VARCHAR2(8)", "Para birimi", tag="currency"),
              "USD_EQV": _col("NUMBER", "USD karşılığı", aggregatable=True),
              "ALL_IN_RATE_BPS": _col("NUMBER", "Toplam maliyet (bps)", aggregatable=True),
          })

    _core("FI_OFFER_SCHEDULE",
          "Amortized işlemlerin ödeme planı — event snapshot'ına bağlıdır. "
          "Güncel planı almak için V_FI_OFFER_CURRENT.EVENT_ID ile join'le "
          "(dashboard'daki itfa takvimi bloğu böyle yapar).",
          "PAY_DT", {
              "EVENT_ID": _col("NUMBER", "Bağlı event (güncel plan = current view'ın EVENT_ID'si)", filterable=False),
              "OFFER_ID": _col("VARCHAR2(24)", "Teklif kimliği", filterable=False),
              "SORT_NO": _col("NUMBER", "Plan satır sırası", filterable=False),
              "PAY_DT": _col("DATE", "Ödeme tarihi", tag="value_time", role="time_axis"),
              "PRINCIPAL_AMT": _col("NUMBER", "Anapara (işlem ccy)", aggregatable=True),
              "INTEREST_AMT": _col("NUMBER", "Faiz (işlem ccy)", aggregatable=True),
          })
    return docs


def publish_docs(dc, docs: list[dict]) -> None:
    from presentations.table_docs.schema import TableDoc
    from presentations.table_docs.store import S3TableDocStore

    store = S3TableDocStore(dc)
    for raw in docs:
        doc = TableDoc.model_validate(raw)
        store.save(doc)
        print(f"   ✓ doküman S3'e yazıldı: {doc.schema_name}.{doc.table}")


# ─────────────────────────────────────────────────────────────────────────
# Doldurma + kalıcılaştırma (deposits_dashboards ile aynı akış)
# ─────────────────────────────────────────────────────────────────────────
def iter_leaf_blocks(manifest):
    for sec in manifest["blocks"]:
        for child in sec.get("children", []):
            yield child


def fill_block_configs(runner: Runner, manifest) -> list[str]:
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
                "id": blk["id"], "version": 1,
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
            print(f"   ✓ {blk['id']}: {len(rows)} satır")
        except Exception as exc:
            errors.append(f"{blk['id']}: {exc}")
            print(f"   ✗ {blk['id']}: {exc}")
    return errors


def build_scope(runner: Runner, dc, manifest, sicil, tables) -> None:
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
                            estimate_source="fi_desk_dashboards import"),
        ))
    scope = ScopeContract(
        presentation_id=manifest["id"], version=1, created_by=sicil,
        created_at=datetime.now(timezone.utc), basket=items,
    )
    version = S3ScopeStore(dc).save(scope)
    manifest["scope_ref"] = {"presentation_id": manifest["id"],
                             "scope_version": version}
    print(f"   ✓ scope v{version} kaydedildi ({len(items)} tablo, lazy)")


SNAPSHOT_DESC = "fi_desk_dashboards otomatik yayını"


def publish_snapshot(dc, manifest, sicil, experts: list[str]) -> None:
    """Masa yayını: manifest'i dondurup uzman(lar)a bağlı snapshot olarak yaz.

    Uzman sayfası bound snapshot'ları cross-owner listeler
    (prisma_home.routes → find_snapshots_bound_to) — erişim uzmanın
    access_scope.read departmanlarından gelir. Aynı job'un önceki otomatik
    snapshot'ları (aynı pid + owner + açıklama) silinir; elle alınmış
    snapshot'lara dokunulmaz."""
    from presentations.store import S3SnapshotStore

    store = S3SnapshotStore(dc)
    for meta in store.list_all_meta():
        if (meta.get("presentation_id") == manifest["id"]
                and meta.get("owner_id") == sicil
                and meta.get("description") == SNAPSHOT_DESC):
            store.delete(meta["snapshot_id"])
            print(f"   - eski snapshot silindi: {meta['snapshot_id']}")
    meta = store.save(manifest, owner_id=sicil,
                      description=SNAPSHOT_DESC, bound_experts=experts)
    print(f"   ✓ snapshot yazıldı: {meta['snapshot_id']} → uzman(lar): "
          f"{', '.join(experts)}")


def persist_manifest(dc, sicil, manifest) -> None:
    from presentations.manifest import validate_manifest

    errs = validate_manifest(manifest)
    if errs:
        raise RuntimeError(f"{manifest['id']} manifest doğrulaması: {errs}")
    key = S3_MANIFEST_KEY.format(sicil=sicil, pid=manifest["id"])
    body = json.dumps(manifest, ensure_ascii=False, indent=2,
                      default=str).encode("utf-8")
    dc._upload_bytes(key, body, content_type="application/json")
    print(f"   ✓ manifest yazıldı: s3://{key} "
          f"(v{manifest['version']}, {sum(1 for _ in iter_leaf_blocks(manifest))} blok, "
          f"{len(manifest['filters'])} filtre)")


# ─────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    # Bekçi: eski repo kopyasıyla koşulmasın (Desktop\prisma vakası).
    import presentations
    from presentations.manifest import LEAF_BLOCK_TYPES
    print(f"presentations paketi: {presentations.__file__}")
    if "scatter_chart" not in LEAF_BLOCK_TYPES:
        raise SystemExit("ESKİ presentations paketi yüklendi — script'i güncel "
                         "repo kökünden çalıştır.")

    from DataClient import DataClient
    dc = DataClient()
    con = dc.get_connection()
    all_errors: list[str] = []
    try:
        sch = (SCHEMA or con.username).upper()
        runner = Runner(dc, con)
        print(f"Bağlantı OK — şema: {sch}, sunum sahibi: {SICIL}")

        if PUBLISH_DOCS:
            print("── Tablo dokümanları")
            # View, CREATE VIEW yetkisi olmayan kurulumda hiç yaratılmamıştır
            # → dokümanı da yayınlanmaz (varlığı sorguyla saptanır).
            try:
                runner.rowcount(f"{sch}.V_FI_OFFER_CURRENT")
                include_view = True
            except Exception:
                include_view = False
                print("   ! V_FI_OFFER_CURRENT yok — view dokümanı atlandı "
                      "(current mantığı FI_OFFER_EVENTS dokümanında tarifli)")
            publish_docs(dc, build_table_docs(runner, sch,
                                              include_view=include_view))

        if PUBLISH_DASHBOARD:
            print("── p_fi_desk dashboard'u")
            manifest, tables = build_dashboard(runner, sch)
            manifest["owner_id"] = SICIL
            experts = _as_list(EXPERTS)
            manifest["bound_experts"] = experts
            if not SKIP_FILL:
                all_errors += fill_block_configs(runner, manifest)
            try:
                build_scope(runner, dc, manifest, SICIL, tables)
            except Exception as exc:
                print(f"   ✗ scope kaydedilemedi: {exc} — manifest scope'suz yazılıyor")
                all_errors.append(f"scope: {exc}")
            persist_manifest(dc, SICIL, manifest)
            if PUBLISH_SNAPSHOT and experts:
                try:
                    publish_snapshot(dc, manifest, SICIL, experts)
                except Exception as exc:
                    print(f"   ✗ snapshot yazılamadı: {exc}")
                    all_errors.append(f"snapshot: {exc}")
    finally:
        dc.drop_connection(con)

    if all_errors:
        print(f"Tamamlandı — {len(all_errors)} hata: {'; '.join(all_errors[:5])}")
        return 1
    print("Bitti — dokümanlar + p_fi_desk S3'te. Sunum listesinden açabilirsin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

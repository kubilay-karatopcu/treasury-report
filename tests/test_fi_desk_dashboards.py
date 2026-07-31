# -*- coding: utf-8 -*-
"""jobs/fi_desk_dashboards.py — p_fi_desk manifest'i + blok SQL'leri.

Dev DuckDB store'u (jobs DDL çevirisi) üzerinde:
  * manifest üretimi + presentations.validate_manifest doğrulaması,
  * filtre/değişken semantic_tag'lerinin V0 allow-list'ine uyumu,
  * TÜM blok SQL'lerinin üretim yoluyla koşması — variables resolver +
    bind expander (Phase 6.5) + oracle_duck lehçe çevirisi (üretimde
    duck_cache açık olduğundan aynı çeviri prod'da da koşar),
  * config doldurmanın (apply_data_to_config) beklenen şekilleri üretmesi.

Ağır presentations bağımlılıkları yoksa (pydantic/sqlparse) test atlanır.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pydantic")
pytest.importorskip("sqlparse")
pd = pytest.importorskip("pandas")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import json  # noqa: E402

from fi_desk import db, service  # noqa: E402

MATRIX = json.loads((REPO / "fi_desk" / "field_matrix.json").read_text("utf-8"))


def _load_job():
    spec = importlib.util.spec_from_file_location(
        "fi_desk_dashboards_job", REPO / "jobs" / "fi_desk_dashboards.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_BIND_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b")


class DevRunner:
    """Job'un Runner arayüzü — Oracle yerine fi_desk dev DuckDB'si.

    Blok SQL'leri üretimdeki gibi oracle_duck çevirisinden geçirilir; bu
    sayede TO_CHAR/NVL/ROWNUM kullanımlarının çevirmen kapsamında kaldığı
    da test edilmiş olur (duck_cache yolu — CLAUDE.md).
    """

    def query(self, sql: str, params: dict | None = None):
        from presentations.sql.oracle_duck import oracle_sql_to_duckdb

        s = oracle_sql_to_duckdb(sql)
        s = _BIND_RE.sub(r"$\1", s)
        with db._duck_lock:
            return db._get_duck().execute(s, params or {}).fetchdf()

    def distinct(self, table: str, col: str) -> list[str]:
        df = self.query(f"SELECT DISTINCT {col} AS V FROM {table} "
                        f"WHERE {col} IS NOT NULL ORDER BY 1")
        return [str(v) for v in df["V"].tolist() if str(v).strip() != ""]

    def rowcount(self, table: str) -> int:
        return int(self.query(f"SELECT COUNT(*) AS N FROM {table}").iloc[0]["N"])


class _StubDC:
    pass


@pytest.fixture()
def dev_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_dev_db_path", lambda: tmp_path / "fi.duckdb")
    db.reset_dev_store()
    db.init(_StubDC(), "IGNORED")
    yield db
    db.reset_dev_store()


def _seed_approved(value_dt, product="TRADE_LOAN", lender="HSBC",
                   status="BIDDING", amortized=False, spread="185"):
    lists: dict[str, list[dict]] = {}
    for row in db.query("SELECT LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1 "
                        "FROM FI_LU_LIST WHERE ACTIVE_FLG = 1 ORDER BY SORT_NO"):
        lists.setdefault(row["LIST_NAME"], []).append(row)
    banks = db.query("SELECT * FROM FI_LU_BANK WHERE ACTIVE_FLG = 1")
    fields = {
        "DEAL_STATUS": status, "LENDER_BANK": lender,
        "OFFER_DT": value_dt - timedelta(days=20), "CURRENCY": "USD",
        "FUNDING_AMT": "50000000", "USD_EQV": "50000000",
        "VALUE_DT": value_dt, "MATURITY_DT": value_dt + timedelta(days=730),
        "REPAYMENT_SCHEDULE": "AMORTIZED" if amortized else "BULLET",
        "COVERAGE_FLG": "NO", "RATE_TYPE": "FLOATING",
        "FLOAT_BASE_RATE": "SOFR_3M", "FLOAT_SPREAD_BPS": spread,
        "TRADE_TXN_AMT": "52000000", "TRADE_TXN_CCY": "USD",
        "SHIPMENT_DT": value_dt - timedelta(days=30),
        "TRADE_PAYMENT_DT": value_dt - timedelta(days=5),
        "IMPORTER": "IMP_SAMPLE_1", "EXPORTER": "EXP_SAMPLE_1",
        "BUSINESS_SEGMENT": "CORPORATE", "REFERENCE_NO": "TL-DASH",
        "GOODS_DESC": "Makine", "SUSTAINABILITY_FLG": "YES",
        "ESG_TYPE": "UOP", "ESG_ELIGIBILITY": "GREEN",
    }
    schedule = ([{"pay_dt": value_dt + timedelta(days=365),
                  "principal_amt": "25000000", "interest_amt": "900000"},
                 {"pay_dt": value_dt + timedelta(days=730),
                  "principal_amt": "25000000", "interest_amt": "450000"}]
                if amortized else [])
    clean = service.validate_entry(MATRIX, {"lists": lists, "banks": banks}, {
        "deal": {"borrower_bank": "QNB", "product_type": product},
        "fields": fields, "schedule": schedule})
    next_id = db.query("SELECT COALESCE(MAX(EVENT_ID), 0) + 1 AS N "
                       "FROM FI_OFFER_EVENTS")[0]["N"]
    stmts, ids = service.build_entry_statements(
        clean, "A16438", db.qualified, int(next_id))
    db.execute(stmts)
    db.execute([("UPDATE FI_OFFER_EVENTS SET APPROVAL_STATUS = 'APPROVED', "
                 "APPROVED_BY = 'A16438', APPROVED_TS = :ts "
                 "WHERE EVENT_ID = :e",
                 {"ts": datetime.now(), "e": ids["event_id"]})])
    return ids


def test_manifest_valid_and_tags_allowed(dev_db):
    from presentations.manifest import validate_manifest
    from presentations.variables.semantic_tags import SEMANTIC_TAGS_V0

    mod = _load_job()
    runner = DevRunner()
    # Veri yok → ccy/lender domain'leri lookup'lardan (kurulum günü senaryosu)
    manifest, tables = mod.build_dashboard(runner, "main")
    manifest["owner_id"] = "A16438"
    manifest["bound_experts"] = ["fi"]  # masa yayını uzman bağıyla (job akışı)

    assert validate_manifest(manifest) == []
    assert len(manifest["pages"]) == 3
    assert [f["id"] for f in manifest["filters"]] == \
        ["f_urun", "f_rapor", "f_ccy", "f_lender"]
    for f in manifest["filters"]:
        assert f["semantic_tag"] in SEMANTIC_TAGS_V0
        assert f["allowed_values"], f["id"]  # lookup fallback boş bırakmaz
    assert set(manifest["filters"][0]["allowed_values"]) == \
        set(MATRIX["products"].keys())
    for blk in mod.iter_leaf_blocks(manifest):
        for v in blk["variables"]:
            assert v["semantic_tag"] in SEMANTIC_TAGS_V0
        assert set(blk["variable_bindings"]) == {"urun", "rapor", "ccy", "lender"}


def test_blocks_fill_from_dev_data(dev_db):
    mod = _load_job()
    runner = DevRunner()
    now = datetime.now()
    _seed_approved(now - timedelta(days=10))                      # BIDDING
    ids = _seed_approved(now - timedelta(days=40), lender="Deutsche Bank",
                         status="WON", amortized=True, spread="210")
    # WON + geçmiş value date → REALIZED satırı

    manifest, tables = mod.build_dashboard(runner, "main")
    manifest["owner_id"] = "A16438"
    errors = mod.fill_block_configs(runner, manifest)
    assert errors == [], errors

    blocks = {b["id"]: b for b in mod.iter_leaf_blocks(manifest)}
    funnel = blocks["b_fi_funnel"]["config"]
    assert set(funnel["categories"]) == {"BIDDING", "REALIZED"}
    assert {s["name"] for s in funnel["series"]} == {"ADET", "USD_MIO"}
    kinds = {s["name"]: s.get("kind") for s in funnel["series"]}
    assert kinds["ADET"] == "bar" and kinds["USD_MIO"] == "line"

    urun = blocks["b_fi_urun_hacim"]["config"]
    assert urun["categories"] == ["TRADE_LOAN"]
    assert urun.get("stacked") is True

    lender = blocks["b_fi_lender"]["config"]
    assert set(lender["categories"]) == {"HSBC", "Deutsche Bank"}

    table = blocks["b_fi_son_kayitlar"]["config"]
    assert len(table["rows"]) == 2

    scatter = blocks["b_fi_maliyet_harita"]["config"]
    assert len(scatter["points"]) == 2
    p = {pt["name"]: pt for pt in scatter["points"]}
    assert p["HSBC"]["x"] == 730 and p["HSBC"]["y"] == 185

    itfa = blocks["b_fi_itfa"]["config"]
    assert len(itfa["categories"]) == 2  # 2 itfa yılı (amortized deal)
    assert sum(itfa["series"][0]["values"]) == pytest.approx(50.0)

    esg = blocks["b_fi_esg"]["config"]
    assert esg["labels"] == ["YES"]

    won_lost = blocks["b_fi_won_lost"]["config"]
    series = {s["name"]: s for s in won_lost["series"]}
    assert series["WON_BPS"]["values"] == [210]


def test_table_docs_shapes(dev_db):
    from presentations.table_docs.schema import TableDoc

    mod = _load_job()
    docs = mod.build_table_docs(DevRunner(), "MAIN")  # TableDoc şeması büyük harf ister
    assert [d["table"] for d in docs] == \
        ["FI_DEALS", "V_FI_OFFER_CURRENT", "FI_OFFER_EVENTS",
         "FI_OFFER_SCHEDULE"]
    # View yetkisi olmayan kurulum: view dokümanı yayınlanmaz
    no_view = mod.build_table_docs(DevRunner(), "MAIN", include_view=False)
    assert "V_FI_OFFER_CURRENT" not in [d["table"] for d in no_view]
    for raw in docs:
        doc = TableDoc.model_validate(raw)   # şema uyumu (pydantic)
        assert doc.description
    view_cols = docs[1]["columns"]
    assert view_cols["USD_EQV"]["aggregatable"] is True
    assert view_cols["PRODUCT_TYPE"]["suggested_semantic_tag"] == "product_group"
    assert view_cols["PRODUCT_TYPE"]["suggested_variable"] == "product_type"
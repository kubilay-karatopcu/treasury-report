# -*- coding: utf-8 -*-
"""fi_desk uçtan uca akış — dev DuckDB backend'iyle gerçek SQL yolu.

jobs/fi_desk_schema.py DDL'i DuckDB lehçesine çevrilip kurulur (db.py'nin
DEV yolu; prod'da Oracle'a giden SQL'lerin aynısı koşar), lookup seed'i
yüklenir, service çıktısı execute edilir ve V_FI_OFFER_CURRENT'ın onay
semantiği doğrulanır: PENDING görünmez, APPROVED en-son-event görünür,
REPORTING_STATUS/TENOR_DAYS türetimi çalışır.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fi_desk import db, service  # noqa: E402

MATRIX = json.loads((REPO / "fi_desk" / "field_matrix.json").read_text("utf-8"))


class _StubDC:  # get_connection YOK → db katmanı dev DuckDB'ye düşer
    pass


@pytest.fixture()
def dev_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_dev_db_path", lambda: tmp_path / "fi.duckdb")
    db.reset_dev_store()
    db.init(_StubDC(), "IGNORED")
    yield db
    db.reset_dev_store()


def _lookups():
    lists: dict[str, list[dict]] = {}
    for row in db.query(
            "SELECT LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1 "
            "FROM FI_LU_LIST WHERE ACTIVE_FLG = 1 ORDER BY SORT_NO"):
        lists.setdefault(row["LIST_NAME"], []).append(row)
    banks = db.query("SELECT * FROM FI_LU_BANK WHERE ACTIVE_FLG = 1")
    return {"lists": lists, "banks": banks}


def _submit_entry(value_dt: datetime, deal_status: str = "BIDDING") -> dict:
    lookups = _lookups()
    payload = {
        "deal": {"borrower_bank": "QNB", "product_type": "TRADE_LOAN",
                 "deal_label": "akış testi"},
        "fields": {
            "DEAL_STATUS": deal_status, "LENDER_BANK": "HSBC",
            "OFFER_DT": value_dt - timedelta(days=20),
            "CURRENCY": "USD", "FUNDING_AMT": "50000000",
            "USD_EQV": "50000000", "VALUE_DT": value_dt,
            "MATURITY_DT": value_dt + timedelta(days=365),
            "REPAYMENT_SCHEDULE": "AMORTIZED", "COVERAGE_FLG": "NO",
            "RATE_TYPE": "FLOATING", "FLOAT_BASE_RATE": "SOFR_3M",
            "FLOAT_SPREAD_BPS": "185",
            "TRADE_TXN_AMT": "52000000",
            "SHIPMENT_DT": value_dt - timedelta(days=30),
            "TRADE_PAYMENT_DT": value_dt - timedelta(days=5),
            "IMPORTER": "IMP_SAMPLE_1", "EXPORTER": "EXP_SAMPLE_1",
            "BUSINESS_SEGMENT": "CORPORATE", "REFERENCE_NO": "TL-FLOW",
            "GOODS_DESC": "Makine", "SUSTAINABILITY_FLG": "NO",
        },
        "schedule": [
            {"pay_dt": value_dt + timedelta(days=180),
             "principal_amt": "25000000", "interest_amt": "900000"},
            {"pay_dt": value_dt + timedelta(days=365),
             "principal_amt": "25000000", "interest_amt": "450000"},
        ],
    }
    clean = service.validate_entry(MATRIX, lookups, payload)
    next_id = db.query(
        "SELECT COALESCE(MAX(EVENT_ID), 0) + 1 AS NEXT_ID "
        "FROM FI_OFFER_EVENTS")[0]["NEXT_ID"]
    stmts, ids = service.build_entry_statements(
        clean, "A16438", db.qualified, int(next_id))
    db.execute(stmts)
    return ids


def _approve(event_id: int) -> None:
    db.execute([(
        "UPDATE FI_OFFER_EVENTS SET APPROVAL_STATUS = 'APPROVED', "
        "APPROVED_BY = :by, APPROVED_TS = :ts WHERE EVENT_ID = :eid",
        {"by": "A16438", "ts": datetime.now(), "eid": event_id},
    )])


def test_prod_schema_resolved_from_connection():
    """FI_DESK_SCHEMA boşsa şema bağlantı kullanıcısından çözülür
    (2026-07-31: sabit A16438 varsayılanı farklı kullanıcıda ORA-00942
    üretmişti); doluysa verilen değer kazanır."""
    class _FakeCon:
        username = "a63837py"

    class _FakeProdDC:  # get_connection VAR → prod yolu
        def get_connection(self):
            return _FakeCon()

        def drop_connection(self, con):
            pass

    db.init(_FakeProdDC(), "")
    assert not db.is_dev()
    assert db.qualified("FI_LU_LIST") == "A63837PY.FI_LU_LIST"
    assert "A63837PY.FI_OFFER_EVENTS" in db.current_relation()

    db.init(_FakeProdDC(), "BASKA_SEMA")
    assert db.qualified("FI_LU_LIST") == "BASKA_SEMA.FI_LU_LIST"


def test_schema_and_lookup_seed(dev_db):
    assert db.is_dev()
    lists = db.query("SELECT COUNT(*) AS N FROM FI_LU_LIST")[0]["N"]
    banks = db.query("SELECT COUNT(*) AS N FROM FI_LU_BANK")[0]["N"]
    assert lists > 30 and banks >= 8
    self_banks = db.query("SELECT BANK_NM FROM FI_LU_BANK WHERE IS_SELF = 1")
    assert [b["BANK_NM"] for b in self_banks] == ["QNB"]


def test_entry_pending_then_approved_flow(dev_db):
    ids = _submit_entry(datetime.now() - timedelta(days=10))

    # Kayıtlar yazıldı, ödeme planı event'e bağlı
    ev = db.query("SELECT * FROM FI_OFFER_EVENTS WHERE EVENT_ID = :e",
                  {"e": ids["event_id"]})[0]
    assert ev["APPROVAL_STATUS"] == "PENDING"
    assert ev["EVENT_TYPE"] == "ENTRY" and ev["EVENT_SEQ"] == 1
    assert ev["COVERAGE_RATE_BPS"] == 0  # Coverage=NO otomatiği
    sched = db.query("SELECT * FROM FI_OFFER_SCHEDULE WHERE EVENT_ID = :e "
                     "ORDER BY SORT_NO", {"e": ids["event_id"]})
    assert len(sched) == 2

    # PENDING event current view'da GÖRÜNMEZ
    assert db.query("SELECT * FROM V_FI_OFFER_CURRENT WHERE OFFER_ID = :o",
                    {"o": ids["offer_id"]}) == []

    # Onaylanınca görünür; türetimler doğru
    _approve(ids["event_id"])
    cur = db.query("SELECT * FROM V_FI_OFFER_CURRENT WHERE OFFER_ID = :o",
                   {"o": ids["offer_id"]})[0]
    assert cur["REPORTING_STATUS"] == "BIDDING"
    assert cur["PRODUCT_TYPE"] == "TRADE_LOAN"
    assert int(cur["TENOR_DAYS"]) == 365
    assert cur["ALL_IN_RATE_BPS"] == pytest.approx(185)


def test_view_shows_latest_approved_only(dev_db):
    ids = _submit_entry(datetime.now() - timedelta(days=10))
    _approve(ids["event_id"])

    # 2. event: statü WON'a çekilir (EDIT snapshot'ı), önce PENDING
    ev = db.query("SELECT * FROM FI_OFFER_EVENTS WHERE EVENT_ID = :e",
                  {"e": ids["event_id"]})[0]
    cols = service.EVENT_DATA_COLS
    data = {c.lower(): ev[c] for c in cols}
    data["deal_status"] = "WON"
    next_id = ids["event_id"] + 1
    binds = ", ".join(f":{c.lower()}" for c in cols)
    db.execute([(
        f"INSERT INTO FI_OFFER_EVENTS (EVENT_ID, OFFER_ID, DEAL_ID, EVENT_SEQ,"
        f" EVENT_TYPE, EVENT_TS, EVENT_USER, APPROVAL_STATUS, {', '.join(cols)})"
        f" VALUES (:eid, :oid, :did, 2, 'STATUS_CHANGE', :ts, 'A16438',"
        f" 'PENDING', {binds})",
        dict(data, eid=next_id, oid=ids["offer_id"], did=ids["deal_id"],
             ts=datetime.now()),
    )])

    # Bekleyen statü değişikliği current'ı DEĞİŞTİRMEZ
    cur = db.query("SELECT * FROM V_FI_OFFER_CURRENT WHERE OFFER_ID = :o",
                   {"o": ids["offer_id"]})[0]
    assert cur["REPORTING_STATUS"] == "BIDDING" and cur["EVENT_SEQ"] == 1

    # Onaylanınca en son onaylı event kazanır; WON + geçmiş value date →
    # REALIZED (value date otomatiği, karar #6)
    _approve(next_id)
    cur = db.query("SELECT * FROM V_FI_OFFER_CURRENT WHERE OFFER_ID = :o",
                   {"o": ids["offer_id"]})[0]
    assert cur["EVENT_SEQ"] == 2
    assert cur["REPORTING_STATUS"] == "REALIZED"


def test_current_relation_matches_view(dev_db):
    """İnline current (db.current_relation — view yetkisi olmayan prod yolu)
    view ile satır satır aynı sonucu verir."""
    ids = _submit_entry(datetime.now() - timedelta(days=10))
    _approve(ids["event_id"])
    inline = db.query(f"SELECT * FROM {db.current_relation()} "
                      "ORDER BY OFFER_ID")
    via_view = db.query("SELECT * FROM V_FI_OFFER_CURRENT ORDER BY OFFER_ID")
    assert len(inline) == 1
    assert inline[0].keys() == via_view[0].keys()
    assert inline == via_view


def test_won_future_value_date_is_pending(dev_db):
    ids = _submit_entry(datetime.now() + timedelta(days=30), deal_status="WON")
    _approve(ids["event_id"])
    cur = db.query("SELECT * FROM V_FI_OFFER_CURRENT WHERE OFFER_ID = :o",
                   {"o": ids["offer_id"]})[0]
    assert cur["REPORTING_STATUS"] == "PENDING"

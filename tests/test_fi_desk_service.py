# -*- coding: utf-8 -*-
"""fi_desk.service — matris tabanlı doğrulama + event üretimi testleri.

Saf Python (Flask/DB yok): lookups fixture'ı db katmanının döndürdüğü
şekli taklit eder. Uçtan uca SQL yolu test_fi_desk_db_flow.py'de.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fi_desk import service  # noqa: E402

MATRIX = json.loads((REPO / "fi_desk" / "field_matrix.json").read_text("utf-8"))

LOOKUPS = {
    "lists": {
        "CURRENCY": [{"VALUE_CD": "USD", "LABEL": "USD", "PARENT_CD": None, "ATTR1": None}],
        "REPAYMENT": [
            {"VALUE_CD": "BULLET", "LABEL": "Bullet", "PARENT_CD": None, "ATTR1": None},
            {"VALUE_CD": "AMORTIZED", "LABEL": "Amortized", "PARENT_CD": None, "ATTR1": None},
        ],
        "COVERAGE_PROVIDER": [{"VALUE_CD": "ECA", "LABEL": "ECA", "PARENT_CD": None, "ATTR1": None}],
        "BASE_RATE": [{"VALUE_CD": "SOFR_3M", "LABEL": "3M SOFR", "PARENT_CD": None, "ATTR1": None}],
        "BUSINESS_SEGMENT": [{"VALUE_CD": "CORPORATE", "LABEL": "Corporate", "PARENT_CD": None, "ATTR1": None}],
        "ESG_TYPE": [{"VALUE_CD": "UOP", "LABEL": "UoP", "PARENT_CD": None, "ATTR1": None}],
        "ESG_ELIGIBILITY": [{"VALUE_CD": "GREEN", "LABEL": "Green", "PARENT_CD": "UOP", "ATTR1": None}],
        "COUNTRY": [
            {"VALUE_CD": "UK", "LABEL": "United Kingdom", "PARENT_CD": None, "ATTR1": "Europe"},
            {"VALUE_CD": "TURKIYE", "LABEL": "Türkiye", "PARENT_CD": None, "ATTR1": "Türkiye"},
        ],
        "IMPORTER": [{"VALUE_CD": "IMP1", "LABEL": "İthalatçı", "PARENT_CD": None, "ATTR1": None}],
        "EXPORTER": [{"VALUE_CD": "EXP1", "LABEL": "İhracatçı", "PARENT_CD": None, "ATTR1": None}],
    },
    "banks": [
        {"BANK_NM": "QNB", "COUNTRY": "TURKIYE", "REGION": "Türkiye",
         "GROUP_COMPANY": "QNB Group", "GROUP_COMPANY_COUNTRY": "QATAR",
         "GROUP_COMPANY_REGION": "Middle East", "IS_SELF": 1},
        {"BANK_NM": "HSBC", "COUNTRY": "UK", "REGION": "Europe",
         "GROUP_COMPANY": "HSBC Holdings", "GROUP_COMPANY_COUNTRY": "UK",
         "GROUP_COMPANY_REGION": "Europe", "IS_SELF": 0},
    ],
}


def trade_loan_payload(**overrides):
    """Geçerli bir Trade Loan girişi; overrides fields üstüne yazar."""
    fields = {
        "DEAL_STATUS": "BIDDING",
        "LENDER_BANK": "HSBC",
        # LENDER_COUNTRY/REGION/GROUP_* bilerek boş → otomatik dolmalı
        "OFFER_DT": "2026-07-01",
        "CURRENCY": "USD",
        "FUNDING_AMT": "50000000",
        "USD_EQV": "50000000",
        "VALUE_DT": "2026-07-20",
        "MATURITY_DT": "2027-07-20",
        "REPAYMENT_SCHEDULE": "BULLET",
        "COVERAGE_FLG": "YES",
        "COVERAGE_PROVIDER": "ECA",
        "RATE_TYPE": "FLOATING",
        "FLOAT_BASE_RATE": "SOFR_3M",
        "FLOAT_SPREAD_BPS": "185",
        "COVERAGE_RATE_BPS": "40",
        "TRADE_TXN_AMT": "52000000",
        "SHIPMENT_DT": "2026-06-15",
        "TRADE_PAYMENT_DT": "2026-07-15",
        "IMPORTER": "IMP1",
        "EXPORTER": "EXP1",
        "BUSINESS_SEGMENT": "CORPORATE",
        "REFERENCE_NO": "TL-1",
        "GOODS_DESC": "Makine",
        "SUSTAINABILITY_FLG": "NO",
    }
    fields.update(overrides)
    return {"deal": {"borrower_bank": "QNB", "product_type": "TRADE_LOAN",
                     "deal_label": "test"},
            "fields": fields, "schedule": []}


def errfields(exc):
    return {e["field"] for e in exc.value.errors}


def test_valid_trade_loan_with_autos():
    clean = service.validate_entry(MATRIX, LOOKUPS, trade_loan_payload())
    f = clean["fields"]
    # Otomatikler: bankadan ülke + group, ülkeden region, all-in toplamı
    assert f["LENDER_COUNTRY"] == "UK"
    assert f["LENDER_REGION"] == "Europe"
    assert f["GROUP_COMPANY"] == "HSBC Holdings"
    assert f["ALL_IN_RATE_BPS"] == pytest.approx(185 + 40)
    assert isinstance(f["OFFER_DT"], datetime)
    assert f["FUNDING_AMT"] == pytest.approx(50_000_000)


def test_missing_required_field():
    p = trade_loan_payload(CURRENCY="")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert "CURRENCY" in errfields(exc)


def test_rate_type_conditionals():
    # FIXED seçilince floating alanları temizlenir (hata değil, uyarı)
    p = trade_loan_payload(RATE_TYPE="FIXED", FIXED_RATE_BPS="300")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["FLOAT_BASE_RATE"] is None
    assert clean["fields"]["FLOAT_SPREAD_BPS"] is None
    assert clean["fields"]["FIXED_RATE_BPS"] == pytest.approx(300)
    assert clean["fields"]["ALL_IN_RATE_BPS"] == pytest.approx(300 + 40)
    # FLOATING'te spread boşsa hata
    p = trade_loan_payload(FLOAT_SPREAD_BPS="")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert "FLOAT_SPREAD_BPS" in errfields(exc)


def test_coverage_no_zeroes_rate_and_skips_provider():
    p = trade_loan_payload(COVERAGE_FLG="NO", COVERAGE_PROVIDER="",
                           COVERAGE_RATE_BPS="")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["COVERAGE_PROVIDER"] is None
    assert clean["fields"]["COVERAGE_RATE_BPS"] == 0.0
    assert clean["fields"]["ALL_IN_RATE_BPS"] == pytest.approx(185)


def test_off_product_field_ignored_with_warning():
    p = trade_loan_payload()
    p["deal"]["product_type"] = "SYNDICATED_LOAN"
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["TRADE_TXN_AMT"] is None
    assert any("TRADE_TXN_AMT" in w for w in clean["warnings"])


def test_amortized_requires_schedule():
    p = trade_loan_payload(REPAYMENT_SCHEDULE="AMORTIZED")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert "PAYMENT_PLAN" in errfields(exc)

    p["schedule"] = [{"pay_dt": "2027-01-20", "principal_amt": "25000000",
                      "interest_amt": "1000000"},
                     {"pay_dt": "2027-07-20", "principal_amt": "25000000",
                      "interest_amt": None}]
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert len(clean["schedule"]) == 2
    assert clean["schedule"][0]["principal_amt"] == pytest.approx(25_000_000)
    assert clean["schedule"][1]["interest_amt"] is None


def test_maturity_before_value_rejected():
    p = trade_loan_payload(MATURITY_DT="2026-07-01")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert "MATURITY_DT" in errfields(exc)


def test_unknown_bank_and_list_value_rejected():
    p = trade_loan_payload(LENDER_BANK="Olmayan Banka", CURRENCY="XXX")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert {"LENDER_BANK", "CURRENCY"} <= errfields(exc)


def test_esg_conditional():
    p = trade_loan_payload(SUSTAINABILITY_FLG="YES", ESG_TYPE="UOP",
                           ESG_ELIGIBILITY="GREEN")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["ESG_TYPE"] == "UOP"
    p = trade_loan_payload(SUSTAINABILITY_FLG="YES")
    with pytest.raises(service.ValidationError) as exc:
        service.validate_entry(MATRIX, LOOKUPS, p)
    assert {"ESG_TYPE", "ESG_ELIGIBILITY"} <= errfields(exc)


def test_fixing_date_auto_default_and_override():
    """FIXING_DT: floating'de varsayılan value date - 2 takvim günü;
    elle girilirse korunur; FIXED'te temizlenir (2026-07-31 kolonu)."""
    clean = service.validate_entry(MATRIX, LOOKUPS, trade_loan_payload())
    assert clean["fields"]["FIXING_DT"] == datetime(2026, 7, 18)  # 20 - 2

    p = trade_loan_payload(FIXING_DT="2026-07-15")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["FIXING_DT"] == datetime(2026, 7, 15)

    p = trade_loan_payload(RATE_TYPE="FIXED", FIXED_RATE_BPS="300",
                           FIXING_DT="2026-07-15")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["FIXING_DT"] is None


def test_number_parsing_flexible():
    """Binlik virgül, ondalık virgül ve bilimsel gösterim kabul edilir."""
    p = trade_loan_payload(FUNDING_AMT="50,000,000", USD_EQV="5e7",
                           FLOAT_SPREAD_BPS="185,5")
    clean = service.validate_entry(MATRIX, LOOKUPS, p)
    assert clean["fields"]["FUNDING_AMT"] == pytest.approx(50_000_000)
    assert clean["fields"]["USD_EQV"] == pytest.approx(50_000_000)
    assert clean["fields"]["FLOAT_SPREAD_BPS"] == pytest.approx(185.5)


def test_statusless_product_minimal_entry():
    """Fiduciary: deal status / coverage / ESG yok — minimal set yeterli."""
    payload = {
        "deal": {"borrower_bank": "QNB", "product_type": "FIDUCIARY"},
        "fields": {
            "LENDER_BANK": "HSBC", "OFFER_DT": "2026-07-01",
            "CURRENCY": "USD", "FUNDING_AMT": "1000000", "USD_EQV": "1000000",
            "VALUE_DT": "2026-07-10", "MATURITY_DT": "2026-12-10",
            "REPAYMENT_SCHEDULE": "BULLET", "RATE_TYPE": "FIXED",
            "FIXED_RATE_BPS": "500",
        },
        "schedule": [],
    }
    clean = service.validate_entry(MATRIX, LOOKUPS, payload)
    assert clean["fields"]["DEAL_STATUS"] is None
    assert clean["fields"]["SUSTAINABILITY_FLG"] is None


def test_build_statements_shape():
    clean = service.validate_entry(MATRIX, LOOKUPS, trade_loan_payload())
    stmts, ids = service.build_entry_statements(
        clean, "A16438", lambda t: f"S.{t}", next_event_id=7,
        now=datetime(2026, 7, 30, 12, 0))
    assert len(stmts) == 3  # deal + offer + event (plan yok)
    assert ids["deal_id"].startswith("FID-")
    assert ids["offer_id"].startswith("FIO-")
    assert ids["event_id"] == 7
    ev_sql, ev_params = stmts[2]
    assert "S.FI_OFFER_EVENTS" in ev_sql
    assert "'PENDING'" in ev_sql
    assert ev_params["etype"] == "ENTRY" and ev_params["seq"] == 1
    # Tüm veri kolonları bind'lenmiş
    for col in service.EVENT_DATA_COLS:
        assert col.lower() in ev_params

    # Mevcut deal'a teklif: deal INSERT atlanır
    clean["deal"]["deal_id"] = "FID-EXISTING"
    stmts2, ids2 = service.build_entry_statements(
        clean, "A16438", lambda t: t, next_event_id=8, deal_exists=True)
    assert len(stmts2) == 2
    assert ids2["deal_id"] == "FID-EXISTING"


def test_event_data_cols_match_matrix():
    event_fields = [k for k, v in MATRIX["fields"].items()
                    if v["storage"] == "event"]
    assert sorted(service.EVENT_DATA_COLS) == sorted(event_fields)

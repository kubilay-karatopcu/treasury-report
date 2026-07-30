# -*- coding: utf-8 -*-
"""FI masası alan matrisi (fi_desk/field_matrix.json) ↔ Oracle DDL
(jobs/fi_desk_schema.py) senkron ve bütünlük testleri.

jobs script'i oracledb'ye bağımlı olduğu için İMPORT EDİLMEZ; DDL metin
olarak ayrıştırılır (deposits_pipeline'ın parantez-virgül sözleşmesi:
tip parantezlerinde virgül yok).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MATRIX = json.loads((REPO / "fi_desk" / "field_matrix.json").read_text("utf-8"))
SCHEMA_SRC = (REPO / "jobs" / "fi_desk_schema.py").read_text("utf-8")

EXPECTED_PRODUCTS = [
    "TRADE_LOAN", "POSTFINANCING", "STRUCTURED_LC", "BILATERAL_LOAN",
    "MURABAHA", "SYNDICATED_LOAN", "CLUB_LOAN", "MTN_PPS", "MTN_DFIS",
    "DPR", "MULTILATERAL", "EUROBOND", "SUBORDINATED_BONDS", "FIDUCIARY",
]


def _ddl_columns(table: str) -> list[str]:
    """jobs/fi_desk_schema.py içindeki DDL gövdesinden kolon adlarını çıkar."""
    m = re.search(rf'"{table}":\s*"""CREATE TABLE \{{t\}} \((.*?)\)"""',
                  SCHEMA_SRC, re.S)
    assert m, f"{table} DDL'i bulunamadı"
    body = m.group(1)
    return [seg.strip().split()[0] for seg in body.split(",") if seg.strip()]


EVENT_COLS = _ddl_columns("FI_OFFER_EVENTS")
EVENT_META = {"EVENT_ID", "OFFER_ID", "DEAL_ID", "EVENT_SEQ", "EVENT_TYPE",
              "EVENT_TS", "EVENT_USER", "APPROVAL_STATUS", "APPROVED_BY",
              "APPROVED_TS", "REJECT_REASON"}
DEAL_COLS = _ddl_columns("FI_DEALS")


def test_products_complete():
    assert list(MATRIX["products"]) == EXPECTED_PRODUCTS
    labels = [p["label"] for p in MATRIX["products"].values()]
    assert len(set(labels)) == len(labels)


def test_every_product_covers_every_input_field():
    input_fields = {k for k, v in MATRIX["fields"].items()
                    if v["storage"] in ("deal", "event", "subtable")}
    for key, prod in MATRIX["products"].items():
        assert set(prod["fields"]) == input_fields, key
        assert set(prod["fields"].values()) <= {"R", "O", "-"}, key
        # Ürün ve borrower her üründe zorunlu
        assert prod["fields"]["PRODUCT_TYPE"] == "R"
        assert prod["fields"]["BORROWER_BANK"] == "R"


def test_event_fields_match_ddl():
    """storage=event her alan DDL'de var; DDL'deki her veri kolonu matriste var."""
    event_fields = {k for k, v in MATRIX["fields"].items()
                    if v["storage"] == "event"}
    ddl_data = set(EVENT_COLS) - EVENT_META
    assert event_fields == ddl_data, (
        f"matriste olup DDL'de olmayan: {sorted(event_fields - ddl_data)}; "
        f"DDL'de olup matriste olmayan: {sorted(ddl_data - event_fields)}")


def test_deal_fields_in_deal_ddl():
    for k, v in MATRIX["fields"].items():
        if v["storage"] == "deal":
            assert k in DEAL_COLS, k


def test_rules_reference_known_fields():
    fields = MATRIX["fields"]
    for rule in MATRIX["rules"]:
        assert rule["field"] in fields, rule
        cond = rule.get("required_if")
        if cond:
            assert cond["field"] in fields, rule
            src = fields[cond["field"]]
            opts = src.get("options")
            if opts:
                assert cond["equals"] in opts, rule
        auto = rule.get("auto", {})
        if "source_field" in auto:
            assert auto["source_field"] in fields, rule


def test_conditional_fields_are_scoped_where_parent_exists():
    """Koşullu alan bir üründe varsa tetikleyen alan da o üründe olmalı."""
    cond_pairs = [(r["field"], r["required_if"]["field"])
                  for r in MATRIX["rules"] if "required_if" in r]
    for key, prod in MATRIX["products"].items():
        f = prod["fields"]
        for child, parent in cond_pairs:
            if f.get(child) in ("R", "O"):
                assert f.get(parent) in ("R", "O"), (key, child, parent)


def test_esg_cascade_and_lists_seeded():
    """Matristeki her liste adı seed'te en az bir satırla var (yer tutucu da olsa)."""
    seeded = set(re.findall(r'\("([A-Z_]+)", "[^"]+", "[^"]*"', SCHEMA_SRC))
    for k, v in MATRIX["fields"].items():
        if v.get("list"):
            assert v["list"] in seeded, (k, v["list"])
    # ESG kaskadı: eligibility parent'ı ESG_TYPE
    assert MATRIX["fields"]["ESG_ELIGIBILITY"]["parent_field"] == "ESG_TYPE"


def test_view_reporting_status_rules_present():
    """Statü makinesi view'da: LOST→UNREALIZED, BIDDING→BIDDING, value date kuralı."""
    view = re.search(r'VIEW_SQL = """(.*?)"""', SCHEMA_SRC, re.S).group(1)
    assert "'LOST' THEN 'UNREALIZED'" in view
    assert "'BIDDING' THEN 'BIDDING'" in view
    assert "REPORTING_STATUS" in view
    assert "TENOR_DAYS" in view
    assert "APPROVAL_STATUS = 'APPROVED'" in view


def test_seed_event_defaults_match_ddl():
    """seed'in _event_defaults kolon listesi DDL veri kolonlarıyla birebir."""
    m = re.search(r"def _event_defaults.*?cols = \[(.*?)\]", SCHEMA_SRC, re.S)
    assert m, "_event_defaults bulunamadı"
    seed_cols = set(re.findall(r'"([A-Z0-9_]+)"', m.group(1)))
    assert seed_cols == set(EVENT_COLS) - EVENT_META


@pytest.mark.parametrize("product,expect", [
    # Excel matrisinden temsili doğrulamalar
    ("TRADE_LOAN", {"TRADE_TXN_AMT": "R", "COVERAGE_FLG": "R",
                    "ALL_IN_FIXED_USD_RATE": "O", "DEAL_STATUS": "R"}),
    ("BILATERAL_LOAN", {"TRADE_TXN_AMT": "O", "COVERAGE_FLG": "R"}),
    ("SYNDICATED_LOAN", {"TRADE_TXN_AMT": "-", "COVERAGE_FLG": "R"}),
    ("MTN_PPS", {"COVERAGE_FLG": "-", "GROUP_COMPANY": "-",
                 "ALL_IN_FIXED_USD_RATE": "R", "DEAL_STATUS": "R"}),
    ("EUROBOND", {"DEAL_STATUS": "-", "LENDER_COUNTRY": "-",
                  "ALL_IN_FIXED_USD_RATE": "R", "SUSTAINABILITY_FLG": "R"}),
    ("FIDUCIARY", {"DEAL_STATUS": "-", "SUSTAINABILITY_FLG": "-",
                   "FLOAT_BASE_RATE": "-", "ALL_IN_RATE_BPS": "-",
                   "NOTES": "O"}),
])
def test_matrix_spot_checks(product, expect):
    fields = MATRIX["products"][product]["fields"]
    for field, req in expect.items():
        assert fields[field] == req, (product, field, fields[field], req)

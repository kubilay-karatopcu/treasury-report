# -*- coding: utf-8 -*-
"""jobs/fi_desk_lookup_import.py — 'Dropdown Listeler.xlsx' ayrıştırma testleri.

DB yazımı ofis işi; burada excel-şekilli satır listeleriyle parse
fonksiyonları sınanır (kolon adı normalizasyonu, grup şirketi ülke/bölge
taşıması, uniq ülke listesi, lehtar dedup + label fallback).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "fi_desk_lookup_import_job",
        REPO / "jobs" / "fi_desk_lookup_import.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # DataClient/openpyxl importları lazy
    return mod


BANK_ROWS = [
    ["Bank", "REGION", "COUNTRY", "MAIN GROUP"],
    ["HSBC London", "Europe", "United Kingdom", "HSBC Holdings"],
    ["HSBC Holdings", "Europe", "United Kingdom", None],
    ["Emirates NBD", "Middle East", "UAE", "Emirates NBD Group"],
    ["ICBC", "Asia", "China", None],
    [None, None, None, None],  # boş satır yok sayılır
]

LEHTAR_ROWS = [
    ["LİSTEDEKİ İSİM", "LEHTAR ADI"],
    ["ACME", "Acme Makine Sanayi A.Ş."],
    ["ACME", "Mükerrer — yok sayılır"],
    ["GLOBEX", None],  # lehtar adı yoksa kısa ad label olur
]


def test_parse_bank_sheet():
    mod = _load()
    banks, countries = mod.parse_bank_sheet(BANK_ROWS)
    by = {b["BANK_NM"]: b for b in banks}
    assert set(by) == {"HSBC London", "HSBC Holdings", "Emirates NBD", "ICBC"}
    assert by["HSBC London"]["COUNTRY"] == "United Kingdom"
    assert by["HSBC London"]["GROUP_COMPANY"] == "HSBC Holdings"
    # Grup şirketi listede satır olarak var → ülke/bölgesi taşınır
    assert by["HSBC London"]["GROUP_COMPANY_COUNTRY"] == "United Kingdom"
    assert by["HSBC London"]["GROUP_COMPANY_REGION"] == "Europe"
    # Grup şirketi listede yoksa boş kalır
    assert by["Emirates NBD"]["GROUP_COMPANY_COUNTRY"] is None
    # Uniq ülke → region (COUNTRY listesi, ATTR1=region)
    assert ("United Kingdom", "Europe") in countries
    assert len([c for c, _ in countries]) == len({c for c, _ in countries})


def test_parse_bank_sheet_flexible_headers():
    mod = _load()
    rows = [["banka", "bölge", "ülke", "grup"],
            ["QNB", "Türkiye", "Türkiye", None]]
    banks, countries = mod.parse_bank_sheet(rows)
    assert banks[0]["BANK_NM"] == "QNB"
    assert countries == [("Türkiye", "Türkiye")]


def test_parse_lehtar_sheet():
    mod = _load()
    out = mod.parse_lehtar_sheet(LEHTAR_ROWS)
    assert out == [("ACME", "Acme Makine Sanayi A.Ş."),
                   ("GLOBEX", "GLOBEX")]

"""Sayfa hiyerarşisi (manifest.pages) — doğrulama + sayfa-kapsamlı apply.

Page > Başlıklar: pages[] üst sekmeler; section_header.page bağlar,
filters[].page filtre barını sayfaya kısıtlar; apply-filters `block_ids`
ile yalnız aktif sayfanın bloklarını çözer (Oracle turu yarıya iner).
"""
from __future__ import annotations

import pytest

from presentations.manifest import validate_manifest, ALLOWED_PATCH_PREFIXES


def _manifest(**over):
    m = {
        "id": "p1", "version": 1,
        "meta": {"title": "T"},
        "pages": [{"id": "pg_mon", "title": "Monthly"},
                  {"id": "pg_dly", "title": "Daily"}],
        "filters": [
            {"id": "f_donem_ay", "semantic_tag": "as_of_time",
             "type": "date_range", "label": "Aylık", "page": "pg_mon"},
            {"id": "f_seg", "semantic_tag": "segment",
             "type": "enum_multi", "label": "Segment",
             "allowed_values": ["BIREYSEL", "OZEL"]},
        ],
        "blocks": [
            {"id": "sec_m", "type": "section_header", "title": "M",
             "page": "pg_mon", "config": {}, "children": []},
            {"id": "sec_g", "type": "section_header", "title": "Global",
             "config": {}, "children": []},
        ],
    }
    m.update(over)
    return m


def test_valid_pages_manifest():
    assert validate_manifest(_manifest()) == []


def test_pages_patch_prefix_allowed():
    assert any(p.startswith("/pages") for p in ALLOWED_PATCH_PREFIXES)


def test_duplicate_page_id_rejected():
    m = _manifest(pages=[{"id": "pg_x", "title": "A"},
                         {"id": "pg_x", "title": "B"}],
                  blocks=[], filters=[])
    errs = validate_manifest(m)
    assert any("duplicate id" in e for e in errs)


def test_page_without_title_rejected():
    m = _manifest(pages=[{"id": "pg_x"}], blocks=[], filters=[])
    errs = validate_manifest(m)
    assert any("missing 'title'" in e for e in errs)


def test_section_unknown_page_rejected():
    m = _manifest()
    m["blocks"][0]["page"] = "pg_yok"
    errs = validate_manifest(m)
    assert any("does not match any pages" in e for e in errs)


def test_no_pages_backwards_compatible():
    m = _manifest()
    del m["pages"]
    for sec in m["blocks"]:
        sec.pop("page", None)
    m["filters"] = [f for f in m["filters"] if "page" not in f]
    assert validate_manifest(m) == []


def test_filter_page_field_accepted():
    from presentations.dashboards.schema import DashboardFilter
    f = DashboardFilter.model_validate(
        {"id": "f_donem_ay", "semantic_tag": "as_of_time",
         "type": "date_range", "label": "Aylık", "page": "pg_mon"})
    assert f.page == "pg_mon"


def test_importer_manifests_carry_pages():
    """cost/balance/tenor Monthly/Daily sayfalarına bölünür; dönem filtreleri
    kendi sayfasına bağlanır. rollings/newbiz sayfasız kalır."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from jobs import deposits_dashboards as dd

    class Stub:
        def distinct(self, table, col):
            return {"DIM_SEGMENT": ["B"], "DIM_PRODUCT": ["V"],
                    "DIM_SUBPRODUCT": ["V"], "DIM_CUSTOMER": ["G"],
                    "DIM_AUM": ["AUM_0_100K"], "DIM_BUCKET": ["0-30"],
                    "CCY_CODE": ["TRY"], "SUB_SEGMENT": ["Mass"],
                    "CUST_TP": ["G"], "TENOR_GRP": ["02_4-31"]}[col]

        def dates(self, table, col):
            if col == "MONTH":
                return ["2026-05-01", "2026-06-01"]
            return ["2026-06-26", "2026-06-30"]

        def minmax_date(self, table, col):
            d = self.dates(table, col)
            return d[0], d[-1]

        def query(self, sql, params=None):
            import pandas as pd
            return pd.DataFrame({"SEGMENT": ["Tüzel"]})

    for pid in ("p_dep_cost", "p_dep_balance", "p_dep_tenor"):
        m, _ = dd.BUILDERS[pid](Stub(), "S")
        assert [p["id"] for p in m["pages"]] == ["pg_mon", "pg_dly"], pid
        by_id = {s["id"]: s for s in m["blocks"]}
        assert by_id["sec_monthly"]["page"] == "pg_mon"
        assert by_id["sec_daily"]["page"] == "pg_dly"
        f = {x["id"]: x for x in m["filters"]}
        assert f["f_donem_ay"]["page"] == "pg_mon"
        assert f["f_donem_gun"]["page"] == "pg_dly"
        assert "page" not in f.get("f_segment", {})   # enum filtreler global
        assert validate_manifest(m) == []

    for pid in ("p_dep_rollings", "p_dep_newbiz"):
        m, _ = dd.BUILDERS[pid](Stub(), "S")
        assert "pages" not in m

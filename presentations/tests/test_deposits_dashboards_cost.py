"""Cost Analysis dashboard importer'ı — kaynak motorla birebirlik testi.

jobs/deposits_dashboards.build_cost'un ürettiği SQL'ler, kaynak dashboard'un
DepositDetailEngine.build_waterfalls'undaki (NIM_calculation app.py
L1324-1470) Bennet ayrıştırmasıyla sayı sayı aynı sonucu vermeli. Oracle
yerine DuckDB'de koşar (NVL→COALESCE gibi mekanik shim'lerle); birim/işaret/
sıralama hataları (%↔bps, as-of varsayılanı, tek-taraflı bubble filtresi)
bu testin yakaladığı gerçek regresyonlardır.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs import deposits_dashboards as dd  # noqa: E402

DIMS = ["SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]   # kaynak varsayılanı
DIM_COL = {"PRODUCT": "DIM_PRODUCT", "SUBPRODUCT": "DIM_SUBPRODUCT",
           "CUSTOMER_TYPE": "DIM_CUSTOMER", "AUM": "DIM_AUM",
           "SEGMENT": "DIM_SEGMENT"}
ORDERED = ["PRODUCT", "SUBPRODUCT", "CUSTOMER_TYPE", "AUM", "SEGMENT"]
T = "PRISMA"
W = "1=1"


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(7)
    rows = []
    subs = {"Vadeli": ["Vadeli"], "Kasa": ["Kasa"], "O/N": ["KGH", "BTH"]}
    for mon in ["2026-05-01", "2026-06-01"]:
        for p, sps in subs.items():
            for sp in sps:
                for c in ["G", "T"]:
                    for a in ["AUM_0_100K", "AUM_100K_500K", None]:
                        for s in ["BIREYSEL", "OZEL"]:
                            if rng.random() < 0.15:
                                continue   # bazı gruplar tek dönemde
                            bal = float(rng.uniform(1e6, 9e8))
                            rate = float(rng.uniform(0.30, 0.55))
                            rows.append((pd.Timestamp(mon), p, sp, c, a, s,
                                         bal, bal * rate))
    df = pd.DataFrame(rows, columns=["MONTH", "DIM_PRODUCT", "DIM_SUBPRODUCT",
                                     "DIM_CUSTOMER", "DIM_AUM", "DIM_SEGMENT",
                                     "BALANCE", "WR_SUM"])
    con = duckdb.connect()
    con.register(T, df)
    return df, con


def _engine(df, d0, d1, dims):
    """Kaynak DepositDetailEngine.build_waterfalls'un pandas portu."""
    def snap(d):
        g = df[df["MONTH"] == pd.Timestamp(d)].copy()
        cols = [DIM_COL[x] for x in ORDERED if x in dims]
        g["_lbl"] = g[cols].apply(
            lambda r: "_".join(str(v) for v in r if pd.notna(v) and str(v)),
            axis=1)
        agg = g.groupby("_lbl")[["BALANCE", "WR_SUM"]].sum()
        agg["r"] = np.where(agg["BALANCE"] != 0,
                            agg["WR_SUM"] / agg["BALANCE"], 0.0)
        return agg

    a0, a1 = snap(d0), snap(d1)
    tot0, tot1 = a0["BALANCE"].sum(), a1["BALANCE"].sum()
    m = (a0[["BALANCE", "r"]].rename(columns={"BALANCE": "b0", "r": "r0"})
         .join(a1[["BALANCE", "r"]].rename(columns={"BALANCE": "b1", "r": "r1"}),
               how="outer").fillna(0.0))
    m["dw"] = m["b1"] / tot1 - m["b0"] / tot0
    m["mix"] = m["dw"] * (m["r0"] + m["r1"]) / 2 * 100
    m["price"] = ((m["b0"] / tot0 + m["b1"] / tot1) / 2
                  * (m["r1"] - m["r0"]) * 100)
    start = a0["WR_SUM"].sum() / tot0 * 100
    end = a1["WR_SUM"].sum() / tot1 * 100
    m["mixdrv"] = m["dw"] * ((m["r0"] + m["r1"]) / 2 * 100
                             - (start + end) / 2)
    return start, end, m


def _run(con, sql):
    """Üretim çevirisi (oracle_duck) + koş — testler apply-filters'ın tablo
    önbelleği yolunun kullandığı ÇEVİRİCİYİ doğrular. Kolonlar Oracle gibi
    UPPERCASE'e çevrilir."""
    from presentations.sql.oracle_duck import oracle_sql_to_duckdb
    lit = ", ".join(f"'{d}'" for d in DIMS)
    sql = sql.replace("IN (:gruplama)", f"IN ({lit})")
    sql = oracle_sql_to_duckdb(sql)
    sql = re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"$\1", sql)
    out = con.execute(sql, {"donem_ay_from": "2026-05-01",
                            "donem_ay_to": "2026-06-01"}).fetchdf()
    out.columns = [c.upper() for c in out.columns]
    return out


def test_wf1_bennet_totals(data):
    df, con = data
    start, end, m = _engine(df, "2026-05-01", "2026-06-01", DIMS)
    wf1 = _run(con, dd.ca_sql_wf1(T, "MONTH", W, "donem_ay"))
    got = dict(zip(wf1["STEP"], wf1["DELTA_PCT"]))
    assert abs(got["Start Rate"] - start) < 0.006
    assert abs(got["Mix / Interaction"] - m["mix"].sum()) < 0.006
    assert abs(got["Pricing (rate, detailed)"] - m["price"].sum()) < 0.006
    assert abs(got["End Rate"] - end) < 0.006
    # Köprü kimliği: Start + Mix + Pricing = End
    assert abs(got["Start Rate"] + got["Mix / Interaction"]
               + got["Pricing (rate, detailed)"] - got["End Rate"]) < 0.012
    assert list(wf1["IS_TOTAL"]) == [1, 0, 0, 1]


def test_wf2_pricing_drivers_top7(data):
    df, con = data
    start, end, m = _engine(df, "2026-05-01", "2026-06-01", DIMS)
    wf2 = _run(con, dd.ca_sql_wf2(T, "MONTH", W, "donem_ay"))
    top = m.reindex(m["price"].abs().sort_values(ascending=False).index).head(7)
    assert set(wf2["STEP"]) - {"After Mix", "Other Items", "End Rate"} \
        == set(top.index)
    assert abs(wf2.iloc[0]["DELTA_PCT"] - (start + m["mix"].sum())) < 0.012
    for lbl in top.index:
        got = float(wf2[wf2["STEP"] == lbl]["DELTA_PCT"].iloc[0])
        assert abs(got - m.loc[lbl, "price"]) < 0.006
    other = m.loc[~m.index.isin(top.index), "price"].sum()
    got_o = float(wf2[wf2["STEP"] == "Other Items"]["DELTA_PCT"].iloc[0])
    assert abs(got_o - other) < 0.006


def test_wf4_mix_drivers(data):
    df, con = data
    start, end, m = _engine(df, "2026-05-01", "2026-06-01", DIMS)
    wf4 = _run(con, dd.ca_sql_wf4(T, "MONTH", W, "donem_ay"))
    top = m.reindex(m["mixdrv"].abs().sort_values(ascending=False).index).head(7)
    for lbl in top.index:
        got = float(wf4[wf4["STEP"] == lbl]["DELTA_PCT"].iloc[0])
        assert abs(got - m.loc[lbl, "mixdrv"]) < 0.006
    am = float(wf4[wf4["STEP"] == "After Mix"]["DELTA_PCT"].iloc[0])
    assert abs(am - (start + m["mix"].sum())) < 0.012   # Σmixdrv = Σmix kimliği


def test_bubbles_units_and_cap(data):
    df, con = data
    _, _, m = _engine(df, "2026-05-01", "2026-06-01", DIMS)
    bb = _run(con, dd.ca_sql_bubble(T, "MONTH", W, "donem_ay", "bal"))
    br = _run(con, dd.ca_sql_bubble(T, "MONTH", W, "donem_ay", "rate"))
    assert len(bb) <= 40 and len(br) <= 40
    lbl = bb.iloc[0]["AD"]
    # x = ΔBakiye ₺M (kaynak _build_bubble_charts; ₺Mr DEĞİL)
    assert abs(bb.iloc[0]["X_DEGER"]
               - (m.loc[lbl, "b1"] - m.loc[lbl, "b0"]) / 1e6) < 0.05
    assert abs(bb.iloc[0]["FAIZ_T1_PCT"] - m.loc[lbl, "r1"] * 100) < 0.05
    assert abs(bb.iloc[0]["BOYUT_M"]
               - (abs(m.loc[lbl, "b0"]) + abs(m.loc[lbl, "b1"])) / 2 / 1e6) < 0.05
    lblr = br.iloc[0]["AD"]
    assert abs(br.iloc[0]["X_DEGER"]
               - (m.loc[lblr, "r1"] - m.loc[lblr, "r0"]) * 10000) < 0.15
    # 5. kolon = WAvg t₁ (%): scatter sözleşmesiyle yatay referans çizgisi olur
    wavg_col = next(c for c in bb.columns if c.startswith("WAVG"))
    tot_b1 = m["b1"].sum()
    wavg_want = (m["b1"] * m["r1"]).sum() / tot_b1 * 100
    assert abs(float(bb.iloc[0][wavg_col]) - wavg_want) < 0.006
    assert bb[wavg_col].nunique() == 1   # her satırda aynı değer


def test_scatter_query_ref_line_apply():
    """execute_block_sqls scatter sözleşmesi: 5. kolon → source='query' yatay
    çizgi; elle eklenen çizgiler korunur; kolon kalkınca query çizgisi düşer."""
    from presentations.nodes.execute_block_sqls import apply_data_to_config

    blk = {"id": "b1", "type": "scatter_chart",
           "config": {"points": [],
                      "ref_lines": [{"axis": "x", "value": 5, "label": "elle"}]}}
    ds5 = {"columns": ["AD", "X", "Y", "BOYUT", "WAvg (%)"],
           "rows": [["a", 1, 2, 3, 45.67], ["b", 2, 3, 4, 45.67]]}
    apply_data_to_config(blk, ds5)
    lines = blk["config"]["ref_lines"]
    assert {"axis": "x", "value": 5, "label": "elle"} in lines
    q = [l for l in lines if l.get("source") == "query"]
    assert len(q) == 1 and q[0]["value"] == 45.67 and q[0]["axis"] == "y"
    assert q[0]["label"] == "WAvg (%)"

    # Tekrar koşum çizgiyi BİRİKTİRMEZ, günceller
    apply_data_to_config(blk, {"columns": ds5["columns"],
                               "rows": [["a", 1, 2, 3, 44.0]]})
    q = [l for l in blk["config"]["ref_lines"] if l.get("source") == "query"]
    assert len(q) == 1 and q[0]["value"] == 44.0

    # 5. kolon kalkarsa query çizgisi düşer, elle eklenen kalır
    apply_data_to_config(blk, {"columns": ["AD", "X", "Y"],
                               "rows": [["a", 1, 2]]})
    assert blk["config"]["ref_lines"] == [{"axis": "x", "value": 5, "label": "elle"}]


def test_heatmap_delta_and_level(data):
    df, con = data
    hm_d = _run(con, dd.ca_sql_heatmap(T, "MONTH", W, "donem_ay", "delta"))
    hm_l = _run(con, dd.ca_sql_heatmap(T, "MONTH", W, "donem_ay", "level"))

    def cell(d, s, a):
        g = df[(df["MONTH"] == pd.Timestamp(d)) & (df["DIM_SEGMENT"] == s)]
        g = g[g["DIM_AUM"].fillna("-") == a]
        b = g["BALANCE"].sum()
        return g["WR_SUM"].sum() / b if b else 0.0

    r0 = cell("2026-05-01", "BIREYSEL", "AUM_0_100K")
    r1 = cell("2026-06-01", "BIREYSEL", "AUM_0_100K")
    pick = (hm_d["SEGMENT"] == "BIREYSEL") & (hm_d["AUM_BANDI"] == "AUM_0_100K")
    # Heatmap Δ bilinçli olarak bps kalır (hücre değerleri küçük; kaynak da bps)
    assert abs(float(hm_d[pick]["DELTA_BPS"].iloc[0]) - (r1 - r0) * 10000) < 0.15
    pick_l = (hm_l["SEGMENT"] == "BIREYSEL") & (hm_l["AUM_BANDI"] == "AUM_0_100K")
    assert abs(float(hm_l[pick_l]["FAIZ_PCT"].iloc[0]) - r1 * 100) < 0.05
    # NULL AUM satırları '-' hücresine düşer, filtreden kaybolmaz
    assert "-" in set(hm_d["AUM_BANDI"])


def test_build_cost_structure():
    class Stub:
        def distinct(self, table, col):
            return {"DIM_SEGMENT": ["BIREYSEL"], "DIM_PRODUCT": ["Vadeli"],
                    "DIM_SUBPRODUCT": ["Vadeli"], "DIM_CUSTOMER": ["G"],
                    "DIM_AUM": ["AUM_0_100K"]}[col]

        def dates(self, table, col):
            if col == "MONTH":
                return ["2026-04-01", "2026-05-01", "2026-06-01"]
            return ["2026-06-26", "2026-06-29", "2026-06-30"]

    manifest, tables = dd.build_cost(Stub(), "S")
    from presentations.manifest import validate_manifest
    assert validate_manifest(manifest) == []

    leaves = list(dd.iter_leaf_blocks(manifest))
    assert len(leaves) == 18            # 2 sekme × 9 leaf

    sec = manifest["blocks"][0]
    car = sec["children"][0]            # waterfall carousel'i: wf1 + 2 canvas
    assert car["type"] == "carousel" and len(car["children"]) == 3
    assert car["children"][0]["type"] == "waterfall_chart"
    assert [c["type"] for c in car["children"][1]["children"]] \
        == ["waterfall_chart", "bar_chart"]
    hm_car = sec["children"][3]         # Δbps ↔ t₁ seviye slaytları
    assert hm_car["type"] == "carousel" and len(hm_car["children"]) == 2

    f = {x["id"]: x for x in manifest["filters"]}
    # t₀/t₁ varsayılanı: son iki ay; günlükte önceki takvim Perşembesi
    assert f["f_donem_ay"]["default"] == {"from": "2026-05-01",
                                          "to": "2026-06-01"}
    assert f["f_donem_gun"]["default"] == {"from": "2026-06-26",
                                           "to": "2026-06-30"}
    assert f["f_gruplama"]["default"] == ["SUBPRODUCT", "CUSTOMER_TYPE",
                                          "AUM", "SEGMENT"]

    # Her leaf sistemin kendi validator/binder'ından geçmeli
    from datetime import datetime, timezone
    from presentations.blocks.schema import Block
    from presentations.sql.validator import validate_sql
    from presentations.variables.resolver import resolve_variables
    from presentations.sql.binder import expand_binds
    for blk in leaves:
        res = validate_sql(
            blk["query"],
            declared_variables=[v["name"] for v in blk["variables"]],
            range_variables=[v["name"] for v in blk["variables"]
                             if v["type"] in ("date_range", "number_range")])
        assert res.ok, f"{blk['id']}: {res.errors}"
        stand_in = Block.model_validate({
            "id": blk["id"], "version": 1, "title": blk["title"],
            "team": "in_presentation", "owner": "t",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "query": blk["query"], "variables": blk["variables"],
            "visualization": {"type": "table", "config": {}}})
        bound = expand_binds(stand_in, resolve_variables(stand_in))
        if "gruplama" in {v["name"] for v in blk["variables"]}:
            assert ":gruplama_0" in bound.sql

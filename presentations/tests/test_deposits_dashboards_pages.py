"""Balance / Tenor / NewBiz importer SQL'leri — kaynak motor semantiği testi.

test_deposits_dashboards_cost.py'ın devamı: kalan sayfaların SQL'lerini
DuckDB'de koşup kaynak formüllerle (₺M bridge Top-8, kırılım CASE'i, WAT,
bileşik→basit geri çevrim, outstanding Δ, bant sıralaması) karşılaştırır.
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


def _duck(sql, con, params):
    sql = re.sub(r"\bNVL\b", "COALESCE", sql)
    sql = sql.replace(" FROM DUAL", "")
    sql = re.sub(r"\)\s+WHERE ROWNUM <= (\d+)", r") LIMIT \1", sql)
    # Oracle band_order_expr → DuckDB
    sql = re.sub(
        r"TO_NUMBER\(REPLACE\(REGEXP_SUBSTR\((.+?), '\\d\+\(\[.,\]\\d\+\)\?'\), ',', '\.'\)\)",
        r"TRY_CAST(replace(regexp_extract(\1, '\\d+([.,]\\d+)?'), ',', '.') AS DOUBLE)",
        sql)
    sql = re.sub(
        r"UPPER\(REGEXP_SUBSTR\((.+?), '\\d\+\(\[.,\]\\d\+\)\?\\s\*\(\[KMB\]\)', 1, 1, 'i', 2\)\)",
        r"UPPER(regexp_extract(\1, '(?i)\\d+([.,]\\d+)?\\s*([KMB])', 2))",
        sql)
    sql = sql.replace("RATIO_TO_REPORT(SUM(TRY_BALANCE)) OVER (PARTITION BY ROLL_DATE)",
                      "SUM(TRY_BALANCE) / SUM(SUM(TRY_BALANCE)) OVER (PARTITION BY ROLL_DATE)")
    sql = re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"$\1", sql)
    # DuckDB kullanılmayan named-param kabul etmez; tarih string'leri DATE'e
    # çevir (Oracle'da binder date objesi bağlar — testte de aynı tip olsun).
    used = {k: v for k, v in params.items() if f"${k}" in sql}
    for k, v in used.items():
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            used[k] = pd.Timestamp(v).date()
    out = con.execute(sql, used).fetchdf()
    out.columns = [c.upper() for c in out.columns]
    return out


# ── Balance ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def bal_data():
    rng = np.random.default_rng(11)
    rows = []
    for mon in ["2026-05-01", "2026-06-01"]:
        for p in ["Vadeli", "Kasa", "O/N", "KMH", "Repo", "Altın",
                  "DTH", "Kıymet", "Fon", "Bono", "Sigorta"]:
            for s in ["BIREYSEL", "OZEL"]:
                for a in ["AUM_0_100K", "AUM_1M_5M", "AUM_500K_1M", None]:
                    if rng.random() < 0.1:
                        continue
                    bal = float(rng.uniform(1e6, 9e8))
                    rows.append((pd.Timestamp(mon), p, p, "G", a, s,
                                 bal, bal * 0.4, float(rng.integers(5, 900))))
    df = pd.DataFrame(rows, columns=["MONTH", "DIM_PRODUCT", "DIM_SUBPRODUCT",
                                     "DIM_CUSTOMER", "DIM_AUM", "DIM_SEGMENT",
                                     "BALANCE", "WR_SUM", "CUST_COUNT"])
    con = duckdb.connect()
    con.register("BAL", df)
    return df, con


BAL_PARAMS = {"donem_ay_from": "2026-05-01", "donem_ay_to": "2026-06-01",
              "kirilim": "PRODUCT", "kirilim2": "AUM"}


def test_balance_bridge_top8(bal_data):
    df, con = bal_data
    grp = dd.dim_case_expr("kirilim")
    out = _duck(dd.ba_sql_bridge("BAL", "MONTH", "1=1", "donem_ay", grp),
                con, BAL_PARAMS)
    g = df.groupby([df["MONTH"], "DIM_PRODUCT"])["BALANCE"].sum().unstack(0).fillna(0.0)
    delta = (g[pd.Timestamp("2026-06-01")] - g[pd.Timestamp("2026-05-01")]) / 1e6
    top8 = delta.reindex(delta.abs().sort_values(ascending=False).index).head(8)
    # Üye kümesi + değerleri (tam-sayı ₺M — kaynak Math.round eşleniği)
    members = set(out["STEP"]) - {"Start", "Other", "End"}
    assert members == set(top8.index)
    for lbl, want in top8.items():
        got = float(out[out["STEP"] == lbl]["DELTA_M"].iloc[0])
        assert abs(got - want) < 0.51, (lbl, got, want)
    start = float(out[out["STEP"] == "Start"]["DELTA_M"].iloc[0])
    end = float(out[out["STEP"] == "End"]["DELTA_M"].iloc[0])
    assert abs(start - df[df["MONTH"] == pd.Timestamp("2026-05-01")]["BALANCE"].sum() / 1e6) < 0.51
    # Köprü kimliği: Start + Σüye + Other = End (tam-sayı yuvarlama payıyla)
    rel = out[~out["STEP"].isin(["Start", "End"])]["DELTA_M"].sum()
    assert abs(start + rel - end) < len(out)  # her satır ±0.5 yuvarlanır
    assert list(out["IS_TOTAL"])[:1] + list(out["IS_TOTAL"])[-1:] == [1, 1]


def test_balance_heatmap_delta_and_band_order(bal_data):
    df, con = bal_data
    g1, g2 = dd.dim_case_expr("kirilim"), dd.dim_case_expr("kirilim2")
    hm = _duck(dd.ba_sql_heatmap("BAL", "MONTH", "1=1", "donem_ay",
                                 g1, g2, "bal", "delta"), con,
               {**BAL_PARAMS, "kirilim": "SEGMENT"})
    pick = (hm["SATIR"] == "BIREYSEL") & (hm["KOLON"] == "AUM_1M_5M")
    sub = df[(df["DIM_SEGMENT"] == "BIREYSEL") & (df["DIM_AUM"] == "AUM_1M_5M")]
    want = (sub[sub["MONTH"] == pd.Timestamp("2026-06-01")]["BALANCE"].sum()
            - sub[sub["MONTH"] == pd.Timestamp("2026-05-01")]["BALANCE"].sum()) / 1e6
    assert abs(float(hm[pick]["DELTA_M"].iloc[0]) - want) < 0.06
    # AUM kolonları K/M çarpanlı alt sınıra göre: 0_100K < 500K_1M < 1M_5M < '-'
    cols = hm[hm["SATIR"] == "BIREYSEL"]["KOLON"].tolist()
    known = [c for c in cols if c != "-"]
    assert known == ["AUM_0_100K", "AUM_500K_1M", "AUM_1M_5M"], cols
    # NULL AUM '-' hücresine düşer, kaybolmaz
    assert "-" in cols


def test_balance_mix_share(bal_data):
    df, con = bal_data
    grp = dd.dim_case_expr("kirilim")
    mix = _duck(dd.ba_sql_mix("BAL", "MONTH", "1=1", "donem_ay", grp),
                con, BAL_PARAMS)
    m1 = df[df["MONTH"] == pd.Timestamp("2026-06-01")]
    share = (m1.groupby("DIM_PRODUCT")["BALANCE"].sum()
             / m1["BALANCE"].sum() * 100)
    top = mix.iloc[0]
    assert abs(float(top["PAY T1 (%)"]) - share[top["GRUP"]]) < 0.02
    # t₁ bakiyesine göre azalan (kaynak sıralaması)
    b1 = m1.groupby("DIM_PRODUCT")["BALANCE"].sum()
    assert list(mix["GRUP"]) == list(b1.sort_values(ascending=False).index)


# ── Tenor: bucket Bennet waterfall (ca_core grp paramı) ─────────────────────

def test_tenor_bucket_waterfall(bal_data):
    _, con = bal_data
    rng = np.random.default_rng(3)
    rows = []
    for mon in ["2026-05-01", "2026-06-01"]:
        for bk in ["0-30", "31-91", "92-181", "182+"]:
            bal = float(rng.uniform(1e8, 9e8))
            rows.append((pd.Timestamp(mon), "tenor", bk, bal, bal * 0.42,
                         bal * float(rng.uniform(20, 200))))
    t = pd.DataFrame(rows, columns=["MONTH", "TENOR_MODE", "DIM_BUCKET",
                                    "BALANCE", "WR_SUM", "WT_SUM"])
    con.register("TEN", t)
    sql = dd.ca_sql_wf1("TEN", "MONTH", "TENOR_MODE = :mod", "donem_ay",
                        grp="NVL(DIM_BUCKET, '-')")
    out = _duck(sql, con, {"donem_ay_from": "2026-05-01",
                           "donem_ay_to": "2026-06-01", "mod": "tenor"})
    got = dict(zip(out["STEP"], out["DELTA_PCT"]))
    # Köprü kimliği + start/end ağırlıklı ortalama
    t0 = t[t["MONTH"] == pd.Timestamp("2026-05-01")]
    t1 = t[t["MONTH"] == pd.Timestamp("2026-06-01")]
    start = t0["WR_SUM"].sum() / t0["BALANCE"].sum() * 100
    end = t1["WR_SUM"].sum() / t1["BALANCE"].sum() * 100
    assert abs(got["Start Rate"] - start) < 0.006
    assert abs(got["End Rate"] - end) < 0.006
    assert abs(got["Start Rate"] + got["Mix / Interaction"]
               + got["Pricing (rate, detailed)"] - got["End Rate"]) < 0.012
    # top_n=999 → Other Items yok (kova sayısı az)
    wf2 = _duck(dd.ca_sql_wf2("TEN", "MONTH", "TENOR_MODE = :mod", "donem_ay",
                              grp="NVL(DIM_BUCKET, '-')", top_n=999),
                con, {"donem_ay_from": "2026-05-01",
                      "donem_ay_to": "2026-06-01", "mod": "tenor"})
    assert "Other Items" not in set(wf2["STEP"])
    assert set(wf2["STEP"]) >= {"0-30", "31-91", "92-181", "182+"}


# ── NewBiz: bileşik→basit geri çevrim + outstanding Δ ───────────────────────

def test_np_bubble_math(bal_data):
    _, con = bal_data
    # Tek hücre, iki pencere: elle doğrulanabilir sayılar
    flow = pd.DataFrame({
        "DAT": [pd.Timestamp("2026-06-23"), pd.Timestamp("2026-06-30")],
        "CCY_CODE": ["TRY", "TRY"], "CUST_TP": ["G", "G"],
        "RELATED_PC": ["Şube", "Şube"], "AUM_BAND": ["1M-2M", "1M-2M"],
        "TENOR_GRP": ["02_4-31", "02_4-31"], "SUB_SEGMENT": ["Mass", "Mass"],
        "NP_HACIM": [100.0, 200.0], "YENI_PARA": [0.0, 0.0],
        "OS_BAKIYE": [0.0, 0.0],
        # bileşik %48 sabit, vade 30 gün → WC=comp·hacim, WT=30·hacim
        "WC_SUM": [48.0 * 100, 50.0 * 200], "WT_SUM": [30.0 * 100, 30.0 * 200],
    })
    out = pd.DataFrame({
        "DAT": [pd.Timestamp("2026-06-23"), pd.Timestamp("2026-06-30")],
        "CHANNEL": ["Şube", "Şube"], "CUST_TP": ["G", "G"],
        "AUM_COMMON": ["1M-5M", "1M-5M"], "TENOR_COMMON": ["4-31", "4-31"],
        "BAL_SUM": [1000.0, 1300.0], "WR_SUM": [0.0, 0.0],
    })
    con.register("NPF", flow)
    con.register("NPO", out)
    params = {"donem_from": "2026-06-23", "donem_to": "2026-06-30", "frek": "D"}

    bb = _duck(dd.np_sql_bubble("NPF", "NPO", "1=1", "bal"), con, params)
    assert len(bb) == 1
    r = bb.iloc[0]
    # Balance X = outstanding Δ (1300−1000), new-prod hacmi DEĞİL
    assert abs(r["X_DEGER"] - 300.0) < 0.01
    # y = t₁ basit faizi: comp %50, vade 30 → ((1.5)^(30/365)−1)·(365/30)·100
    want_simple = (pow(1.5, 30 / 365) - 1) * (365 / 30) * 100
    assert abs(r["FAIZ_T1_PCT"] - want_simple) < 0.01, (r["FAIZ_T1_PCT"], want_simple)
    assert abs(r["HACIM_M"] - 150.0) < 0.11          # (100+200)/2
    wavg_col = next(c for c in bb.columns if c.startswith("WAVG"))
    assert abs(r[wavg_col] - round(want_simple, 2)) < 0.01

    br = _duck(dd.np_sql_bubble("NPF", "NPO", "1=1", "rate"), con, params)
    s0 = (pow(1.48, 30 / 365) - 1) * (365 / 30) * 100
    assert abs(br.iloc[0]["X_DEGER"] - (want_simple - s0) * 100) < 0.6  # bps

    hm = _duck(dd.np_sql_heatmap("NPF", "1=1", "delta"), con, params)
    assert abs(float(hm.iloc[0]["DELTA_BPS"]) - (50.0 - 48.0) * 100) < 0.01


def test_band_sort_key():
    labels = ["AUM_1M_5M", "AUM_0_100K", "AUM_500K_1M", "AUM_100K_500K", "Bilinmiyor"]
    assert sorted(labels, key=dd._band_sort_key) == [
        "AUM_0_100K", "AUM_100K_500K", "AUM_500K_1M", "AUM_1M_5M", "Bilinmiyor"]
    assert dd._band_sort_key("0-30") == 0
    assert dd._band_sort_key("200M+") == 200e6

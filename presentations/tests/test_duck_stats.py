# -*- coding: utf-8 -*-
"""_compute_stats / summarize_views — geniş tablo regresyonu.

Ofis bulgusu (2026-07-03): argmax satırın TAMAMINI taşıyordu → sayısal_kolon ×
toplam_kolon KARESEL şişme. 181 kolonlu (150 sayısal) tabloda data_summary tek
başına ~600KB → prompt 253k token → provider HTTP 400. Manifest bomboşken
(468 char) ilk mesajda patlıyordu.
"""
import duckdb
import numpy as np
import pandas as pd
import pytest

from presentations.duck import register_dataframe, summarize_views
from presentations.llm import _data_summary_section


@pytest.fixture
def wide_view():
    rng = np.random.default_rng(7)
    cols = {"CREATE_TM": pd.date_range("2026-06-01", periods=500, freq="min")}
    for i in range(150):
        cols[f"RATE_BANK_{i:03d}"] = np.round(rng.uniform(30, 50, 500), 4)
    for i in range(30):
        cols[f"DIM_{i:02d}"] = rng.choice(["AAA", "BBB", "CCC"], 500)
    conn = duckdb.connect()
    register_dataframe(conn, "ds", pd.DataFrame(cols))
    yield conn
    conn.close()


def test_argmax_carries_dims_only_not_whole_row(wide_view):
    summary = summarize_views(wide_view, ["ds"])
    stats = summary["ds"]["stats"]
    assert stats, "sayısal kolonlar için stats üretilmeli"
    for col, s in stats.items():
        # argmax: en fazla _ARGMAX_MAX_DIMS boyut kolonu; 181 kolonluk satırın
        # tamamı ASLA (karesel şişmenin kendisi buydu).
        assert len(s["argmax"]) <= 6
        # boyut kolonları sayısal olmayanlardan seçilir
        for k in s["argmax"]:
            assert not k.startswith("RATE_BANK_")


def test_wide_table_data_summary_stays_bounded(wide_view):
    summary = summarize_views(wide_view, ["ds"])
    section = _data_summary_section(summary)
    # Fix öncesi bu senaryo ~600.000 char üretiyordu. Doğrusal büyümede üst
    # sınır bol paylı 60k — karesel geri gelirse bu test patlar.
    assert len(section) < 60_000, f"data_summary yeniden şişmiş: {len(section):,} char"


def test_min_max_avg_still_present(wide_view):
    summary = summarize_views(wide_view, ["ds"])
    s = summary["ds"]["stats"]["RATE_BANK_000"]
    assert s["min"] is not None and s["max"] is not None and s["avg"] is not None
    assert 30 <= s["min"] <= s["max"] <= 50


def test_all_numeric_table_argmax_empty_but_stats_ok():
    """Boyut kolonu hiç yoksa argmax boş kalır (satır kopyalanmaz), min/max/avg durur."""
    conn = duckdb.connect()
    register_dataframe(conn, "nums", pd.DataFrame({
        "A": [1.0, 2.0, 3.0], "B": [9.0, 8.0, 7.0],
    }))
    try:
        summary = summarize_views(conn, ["nums"])
        s = summary["nums"]["stats"]
        assert s["A"]["argmax"] == {}
        assert s["A"]["max"] == 3.0
    finally:
        conn.close()

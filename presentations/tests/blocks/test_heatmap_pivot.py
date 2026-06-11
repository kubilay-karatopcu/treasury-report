"""apply_data_to_config heatmap mapping.

Regression: a natural heatmap query — ``SELECT a, b, SUM(x) GROUP BY a, b`` —
showed "Grafik için veri yok" because (1) the mapper wrote ``categories`` while
the heatmap renderer reads ``config.x_axis ?? config.categories`` and the seed
``x_axis: []`` (not nullish) shadowed it, and (2) the long format wasn't pivoted
into the x_axis + per-row series matrix.
"""
from __future__ import annotations

from presentations.nodes.execute_block_sqls import apply_data_to_config


def _heatmap_block():
    return {"id": "h", "type": "heatmap", "config": {"x_axis": [], "series": []}}


def test_long_format_pivots_into_matrix():
    block = _heatmap_block()
    ds = {
        "columns": ["CURRENCY", "TENOR", "net_gap"],
        "rows": [
            ["CHF", "1D", 63.0], ["CHF", "1W", 114.0], ["CHF", "1M", 6.0],
            ["USD", "1D", 50.0], ["USD", "1M", 20.0],          # USD missing 1W
        ],
    }
    apply_data_to_config(block, ds)
    cfg = block["config"]
    # Schema key is x_axis (NOT categories) + unique TENOR in first-seen order.
    assert cfg["x_axis"] == ["1D", "1W", "1M"], cfg
    assert not cfg.get("categories")            # populated axis is x_axis, not categories
    assert [s["name"] for s in cfg["series"]] == ["CHF", "USD"]
    by = {s["name"]: s["values"] for s in cfg["series"]}
    assert by["CHF"] == [63.0, 114.0, 6.0]
    assert by["USD"] == [50.0, 0, 20.0]         # missing (USD, 1W) cell → 0


def test_wide_format_still_works():
    # 2nd column numeric → wide: x_axis = col0, series = the numeric columns.
    block = _heatmap_block()
    ds = {"columns": ["CURRENCY", "jan", "feb"],
          "rows": [["CHF", 1.0, 2.0], ["USD", 3.0, 4.0]]}
    apply_data_to_config(block, ds)
    cfg = block["config"]
    assert cfg["x_axis"] == ["CHF", "USD"]
    assert [s["name"] for s in cfg["series"]] == ["jan", "feb"]


def test_empty_result_clears_x_axis():
    block = {"id": "h", "type": "heatmap",
             "config": {"x_axis": ["old"], "series": [{"name": "s", "values": [1]}]}}
    apply_data_to_config(block, {"columns": [], "rows": []})
    assert block["config"]["x_axis"] == []
    assert block["config"]["series"][0]["values"] == []

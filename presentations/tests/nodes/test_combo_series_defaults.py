"""_build_combo_series — rol (kind/axis) varsayılanları ve korunumu.

Kullanıcı raporu: combo grafik varsayılanları "çubuk/çubuk + sol/sol"
geliyordu. Politika: ilk seri çubuk/sol, sonrakiler çizgi/sağ — farklı tip
VE farklı y-ekseni. Frontend karşılığı editor/lib/store.js
comboSeriesDefaults; iki taraf senkron kalmalı.
"""
from presentations.nodes.execute_block_sqls import _build_combo_series


COLS = ["AY", "HACIM", "ORAN"]
ROWS = [["Oca", 10, 1.5], ["Şub", 20, 2.5]]


def test_fresh_series_get_distinct_kinds_and_axes():
    series = _build_combo_series(COLS, ROWS, None)
    assert [s["kind"] for s in series] == ["bar", "line"]
    assert [s["axis"] for s in series] == ["left", "right"]
    assert series[0]["values"] == [10.0, 20.0]
    assert series[1]["values"] == [1.5, 2.5]


def test_third_series_defaults_to_line_right():
    cols = COLS + ["MARJ"]
    rows = [r + [0.1] for r in ROWS]
    series = _build_combo_series(cols, rows, None)
    assert series[2]["kind"] == "line"
    assert series[2]["axis"] == "right"


def test_existing_roles_preserved_by_index():
    existing = [
        {"name": "Hacim (özel)", "kind": "line", "axis": "right"},
        {"name": "Oran", "kind": "bar", "axis": "left"},
    ]
    series = _build_combo_series(COLS, ROWS, existing)
    assert series[0]["name"] == "Hacim (özel)"
    assert series[0]["kind"] == "line"
    assert series[0]["axis"] == "right"
    assert series[1]["kind"] == "bar"
    assert series[1]["axis"] == "left"


def test_line_chart_series_without_roles_get_defaults():
    # line→combo geçişinden gelen seriler kind/axis taşımaz — varsayılan
    # politika uygulanır, isim korunur.
    existing = [{"name": "Seri 1", "values": [1, 2]}, {"name": "Seri 2", "values": [3, 4]}]
    series = _build_combo_series(COLS, ROWS, existing)
    assert series[0] == {"name": "Seri 1", "values": [10.0, 20.0], "kind": "bar", "axis": "left"}
    assert series[1] == {"name": "Seri 2", "values": [1.5, 2.5], "kind": "line", "axis": "right"}


# ── Yeni tipler: waterfall_chart / scatter_chart veri eşlemesi ──────────────

def test_waterfall_mapping():
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    blk = {"id": "w1", "type": "waterfall_chart", "config": {}}
    ds = {"columns": ["STEP", "DELTA", "IS_TOTAL"],
          "rows": [["Başlangıç", 42.0, 1], ["Vadeli", 1.5, 0],
                   ["Kasa", -0.7, None], ["Bitiş", 42.8, 1]]}
    apply_data_to_config(blk, ds)
    c = blk["config"]
    assert c["categories"] == ["Başlangıç", "Vadeli", "Kasa", "Bitiş"]
    assert c["values"] == [42.0, 1.5, -0.7, 42.8]
    assert c["totals"] == [True, False, False, True]


def test_waterfall_without_total_flag():
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    blk = {"id": "w2", "type": "waterfall_chart", "config": {}}
    apply_data_to_config(blk, {"columns": ["S", "D"], "rows": [["a", 1.0]]})
    assert blk["config"]["totals"] == [False]


def test_scatter_mapping_with_size():
    from presentations.nodes.execute_block_sqls import apply_data_to_config
    blk = {"id": "s1", "type": "scatter_chart", "config": {}}
    ds = {"columns": ["NAME", "X", "Y", "SIZE"],
          "rows": [["Vadeli", 12.5, 44.1, 900.0], ["Kasa", -3.0, 40.0, 120.0]]}
    apply_data_to_config(blk, ds)
    assert blk["config"]["points"] == [
        {"name": "Vadeli", "x": 12.5, "y": 44.1, "size": 900.0},
        {"name": "Kasa", "x": -3.0, "y": 40.0, "size": 120.0},
    ]


def test_new_types_manifest_validation():
    from presentations.manifest import validate_manifest
    m = {"id": "p", "version": 1, "meta": {"title": "t"}, "blocks": [
        {"id": "sec", "type": "section_header", "title": "S", "config": {},
         "children": [
            {"id": "w1", "type": "waterfall_chart", "title": "W", "locked": False,
             "config": {"categories": ["a", "b"], "values": [1, 2],
                        "totals": [True, False]}},
            {"id": "s1", "type": "scatter_chart", "title": "S", "locked": False,
             "config": {"points": [{"name": "n", "x": 1, "y": 2.5}]}},
         ]},
    ]}
    assert validate_manifest(m) == []
    # uzunluk uyumsuzluğu yakalanır
    m["blocks"][0]["children"][0]["config"]["values"] = [1]
    assert any("values length" in e for e in validate_manifest(m))

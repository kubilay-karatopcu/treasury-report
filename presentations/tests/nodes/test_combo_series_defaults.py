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

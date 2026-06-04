"""Madde 7 — 'N kopya oluştur' güvenlik ağı.

`_ensure_unique_block_ids` LLM çıktısındaki `add` patch'lerinde çakışan blok
id'lerini benzersizleştirir (Qwen GGUF aynı id'yi N kez üretebiliyor).
"""
from __future__ import annotations

from types import SimpleNamespace

from presentations.nodes.generate_patch import _ensure_unique_block_ids


def _state(manifest):
    return SimpleNamespace(manifest=manifest)


def test_duplicate_adds_get_unique_ids():
    manifest = {"blocks": [{"id": "s0", "type": "section_header", "children": [
        {"id": "b_kpi", "type": "kpi"},
    ]}]}
    # LLM 3 kopya isterken aynı id'yi 3 kez kullandı (çakışma senaryosu).
    patches = [
        {"op": "add", "path": "/blocks/0/children/-", "value": {"id": "b_kpi", "type": "kpi"}},
        {"op": "add", "path": "/blocks/0/children/-", "value": {"id": "b_kpi", "type": "kpi"}},
        {"op": "add", "path": "/blocks/0/children/-", "value": {"id": "b_kpi", "type": "kpi"}},
    ]
    out = _ensure_unique_block_ids(_state(manifest), patches)
    ids = [p["value"]["id"] for p in out]
    assert len(set(ids)) == 3                 # üçü de farklı
    assert "b_kpi" not in ids                 # mevcut blokla da çakışmıyor
    assert all(i.startswith("b_kpi") for i in ids)


def test_unique_add_id_left_untouched():
    manifest = {"blocks": [{"id": "s0", "type": "section_header", "children": []}]}
    patches = [{"op": "add", "path": "/blocks/0/children/-",
                "value": {"id": "fresh_one", "type": "kpi"}}]
    out = _ensure_unique_block_ids(_state(manifest), patches)
    assert out[0]["value"]["id"] == "fresh_one"


def test_duplicate_container_reids_descendants():
    manifest = {"blocks": [{"id": "s0", "type": "section_header", "children": [
        {"id": "cr", "type": "carousel", "children": [{"id": "sl1", "type": "kpi"}]},
    ]}]}
    # Carousel kopyası — hem top hem child id çakışıyor.
    patches = [{"op": "add", "path": "/blocks/0/children/-", "value": {
        "id": "cr", "type": "carousel", "children": [{"id": "sl1", "type": "kpi"}],
    }}]
    out = _ensure_unique_block_ids(_state(manifest), patches)
    v = out[0]["value"]
    assert v["id"] != "cr"
    assert v["children"][0]["id"] != "sl1"


def test_replace_and_filters_untouched():
    manifest = {"blocks": [{"id": "b1", "type": "kpi"}], "filters": []}
    patches = [
        # Yerinde düzenleme — id korunmalı.
        {"op": "replace", "path": "/blocks/0", "value": {"id": "b1", "type": "kpi"}},
        # Filtre (blok değil) — dokunulmamalı.
        {"op": "add", "path": "/filters/-", "value": {"id": "f_seg", "type": "enum_multi"}},
    ]
    out = _ensure_unique_block_ids(_state(manifest), patches)
    assert out[0]["value"]["id"] == "b1"
    assert out[1]["value"]["id"] == "f_seg"

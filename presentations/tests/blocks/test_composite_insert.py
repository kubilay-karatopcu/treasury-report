"""Madde 1 — composite (carousel/canvas) library insert helpers.

`_freshen_ids` must assign a fresh, unique id to every node (and descendant)
in a cloned container subtree so re-inserting a saved carousel can't collide
with ids already in the target manifest.
"""
from __future__ import annotations

from presentations.routes_blocks import _freshen_ids


def _collect_ids(nodes):
    out = []
    for n in nodes:
        out.append(n["id"])
        out.extend(_collect_ids(n.get("children") or []))
    return out


def test_freshen_ids_unique_recursive():
    nodes = [
        {"id": "a", "type": "kpi"},
        {"id": "cr", "type": "carousel", "children": [
            {"id": "s1", "type": "kpi"},
            {"id": "s2", "type": "bar_chart"},
        ]},
    ]
    _freshen_ids(nodes)
    ids = _collect_ids(nodes)
    assert len(ids) == 4
    assert len(set(ids)) == 4                      # all unique
    assert all(i.startswith("b_") for i in ids)    # fresh server-side ids
    # None of the original ids survive (no collision with manifest).
    assert not ({"a", "cr", "s1", "s2"} & set(ids))


def test_freshen_ids_handles_nested_containers():
    nodes = [{"id": "outer", "type": "canvas", "children": [
        {"id": "inner", "type": "carousel", "children": [{"id": "leaf", "type": "kpi"}]},
    ]}]
    _freshen_ids(nodes)
    ids = _collect_ids(nodes)
    assert len(ids) == len(set(ids)) == 3

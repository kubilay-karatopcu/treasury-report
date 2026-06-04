"""Canvas container block validation + traversal (madde 2).

Canvas is a generic level-2 container (like carousel) that holds leaf blocks in
a 12-column grid. These tests lock the manifest-level contract: where canvas may
sit, what it may hold, and that traversal/path helpers see its children.
"""
from __future__ import annotations

from presentations.manifest import (
    CHILD_CONTAINER_TYPES,
    find_block_by_id,
    iter_all_blocks,
    validate_block,
    validate_manifest,
)
from presentations.patch import apply_patches


def _kpi(bid: str, **extra) -> dict:
    return {
        "id": bid, "type": "kpi", "locked": False, "title": "K",
        "config": {"value": 1, "unit": "", "delta": 0, "delta_label": "", "period": ""},
        **extra,
    }


def _canvas(bid: str, children) -> dict:
    return {"id": bid, "type": "canvas", "locked": False, "title": "C",
            "config": {}, "children": children}


def _carousel(bid: str, children) -> dict:
    return {"id": bid, "type": "carousel", "locked": False, "title": "Cr",
            "config": {}, "children": children}


def test_canvas_is_a_child_container():
    assert "canvas" in CHILD_CONTAINER_TYPES


def test_canvas_with_leaf_children_is_valid():
    block = _canvas("cv_1", [_kpi("k1"), _kpi("k2", width="1/2")])
    assert validate_block(block, allow_container=True) == []


def test_empty_canvas_is_valid():
    # Unlike carousel (>=1 slide), an empty canvas is a valid starting state.
    assert validate_block(_canvas("cv_e", []), allow_container=True) == []


def test_canvas_not_allowed_at_top_level_or_outside_section_children():
    errors = validate_block(_canvas("cv_x", [_kpi("k1")]), allow_container=False)
    assert any("sadece section.children" in e for e in errors)


def test_canvas_rejects_nested_container_child():
    nested = _canvas("cv_outer", [_canvas("cv_inner", [_kpi("k1")])])
    errors = validate_block(nested, allow_container=True)
    assert any("canvas" in e for e in errors)


def test_canvas_child_may_carry_width():
    block = _canvas("cv_w", [_kpi("k1", width="1/3"), _kpi("k2", width="2/3")])
    assert validate_block(block, allow_container=True) == []


def test_manifest_with_canvas_section_validates():
    manifest = {
        "meta": {"title": "T"},
        "blocks": [{
            "id": "sec_1", "type": "section_header", "title": "S",
            "locked": False, "config": {},
            "children": [_canvas("cv_1", [_kpi("k1"), _kpi("k2")])],
        }],
    }
    assert validate_manifest(manifest) == []


def test_traversal_sees_canvas_children():
    manifest = {
        "meta": {"title": "T"},
        "blocks": [{
            "id": "sec_1", "type": "section_header", "title": "S",
            "locked": False, "config": {},
            "children": [_canvas("cv_1", [_kpi("k1"), _kpi("k2")])],
        }],
    }
    ids = {b.get("id") for b in iter_all_blocks(manifest)}
    assert {"sec_1", "cv_1", "k1", "k2"} <= ids

    block, path = find_block_by_id(manifest, "k2")
    assert block["id"] == "k2"
    assert path == "/blocks/0/children/0/children/1"


# ── Madde 3 — cross-parent move contract (frontend emits these patches) ──────

def _section(sid, children):
    return {"id": sid, "type": "section_header", "title": "S", "locked": False,
            "config": {}, "children": children}


def test_move_section_child_into_canvas_yields_valid_manifest():
    # A leaf sits directly under a section next to an (empty) canvas. The
    # moveBlockBetweenParents action computes the post-move section locally and
    # emits a single `replace /blocks/{si}` patch (both source and target are in
    # section 0). The replaced section must validate and place k1 in the canvas.
    manifest = {
        "meta": {"title": "T"},
        "blocks": [_section("sec_1", [_canvas("cv_1", []), _kpi("k1", width="1/2")])],
    }
    new_section = _section("sec_1", [_canvas("cv_1", [_kpi("k1", width="1/2")])])
    patches = [{"op": "replace", "path": "/blocks/0", "value": new_section}]
    after = apply_patches(manifest, patches)
    assert validate_manifest(after) == []
    # k1 now lives inside the canvas, and only there.
    _, path = find_block_by_id(after, "k1")
    assert path == "/blocks/0/children/0/children/0"
    assert len(after["blocks"][0]["children"]) == 1  # section now holds only the canvas


def test_eject_canvas_child_to_section_yields_valid_manifest():
    manifest = {
        "meta": {"title": "T"},
        "blocks": [_section("sec_1", [_canvas("cv_1", [_kpi("k1"), _kpi("k2")])])],
    }
    # Move k1 out of the canvas to the section (appended after the canvas).
    new_section = _section("sec_1", [_canvas("cv_1", [_kpi("k2")]), _kpi("k1")])
    patches = [{"op": "replace", "path": "/blocks/0", "value": new_section}]
    after = apply_patches(manifest, patches)
    assert validate_manifest(after) == []
    _, path = find_block_by_id(after, "k1")
    assert path == "/blocks/0/children/1"  # appended after the canvas
    assert len(after["blocks"][0]["children"][0]["children"]) == 1  # k2 remains in canvas


def test_cross_section_move_replaces_both_sections():
    # Dragging a block from section A into a canvas in section B emits TWO replace
    # patches (one per affected top-level section). Both must validate.
    manifest = {
        "meta": {"title": "T"},
        "blocks": [
            _section("sec_a", [_kpi("k1")]),
            _section("sec_b", [_canvas("cv_b", [])]),
        ],
    }
    patches = [
        {"op": "replace", "path": "/blocks/0", "value": _section("sec_a", [])},
        {"op": "replace", "path": "/blocks/1",
         "value": _section("sec_b", [_canvas("cv_b", [_kpi("k1")])])},
    ]
    after = apply_patches(manifest, patches)
    assert validate_manifest(after) == []
    _, path = find_block_by_id(after, "k1")
    assert path == "/blocks/1/children/0/children/0"


def test_dragging_last_slide_out_dissolves_empty_carousel():
    # A carousel with a single slide. Dragging that slide out to the section
    # would empty the carousel — invalid (carousel needs >=1 slide). The move
    # action dissolves the now-empty carousel, so the emitted section is valid.
    manifest = {
        "meta": {"title": "T"},
        "blocks": [_section("sec_1", [_carousel("cr_1", [_kpi("k1")]), _kpi("k2")])],
    }
    # After: carousel gone, k1 lives directly in the section.
    new_section = _section("sec_1", [_kpi("k2"), _kpi("k1")])
    patches = [{"op": "replace", "path": "/blocks/0", "value": new_section}]
    after = apply_patches(manifest, patches)
    assert validate_manifest(after) == []
    assert find_block_by_id(after, "cr_1")[0] is None   # carousel dissolved
    _, path = find_block_by_id(after, "k1")
    assert path == "/blocks/0/children/1"

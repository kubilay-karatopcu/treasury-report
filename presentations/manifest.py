from __future__ import annotations
from typing import TypedDict

# ── TypedDicts ────────────────────────────────────────────────────────────────

class MetaSchema(TypedDict):
    title: str
    eyebrow: str
    date: str
    author_label: str


class BasketItem(TypedDict):
    table: str
    columns: list[str]
    row_filter: str | None


class KpiConfig(TypedDict):
    value: float
    unit: str
    delta: float
    delta_label: str
    period: str


class BarSeries(TypedDict):
    name: str
    values: list[float]


class BarChartConfig(TypedDict):
    categories: list[str]
    series: list[BarSeries]


class LineSeries(TypedDict):
    name: str
    values: list[float]


class LineChartConfig(TypedDict):
    x_axis: list[str]
    series: list[LineSeries]


class NarrativeConfig(TypedDict):
    text: str


# Block types — section_header is a CONTAINER (has children), all others are
# leaf blocks that live INSIDE a section's children[].
LEAF_BLOCK_TYPES = frozenset({
    "kpi", "narrative",
    "bar_chart", "line_chart", "area_chart",
    "pie_chart", "heatmap", "radial_bar",
    "data_table",       # AG Grid table block
})

CONTAINER_BLOCK_TYPES = frozenset({"section_header"})

BLOCK_TYPES = LEAF_BLOCK_TYPES | CONTAINER_BLOCK_TYPES

IMMUTABLE_BLOCK_FIELDS = frozenset({"id", "type", "locked"})

ALLOWED_PATCH_PREFIXES = ("/blocks/", "/meta/")

# Block width — Phase 6.
WIDTH_VALUES = frozenset({"full", "1/2", "1/3", "2/3"})
NO_WIDTH_TYPES = frozenset({"section_header"})

# Style option values.
CURVE_VALUES = frozenset({"smooth", "straight", "stepline"})
LEGEND_POSITIONS = frozenset({"top", "right", "bottom", "left"})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_scalar_label(v) -> bool:
    return isinstance(v, (str, int, float, bool))


def _validate_label_array(arr, field_name: str, bid: str) -> list[str]:
    errors: list[str] = []
    for i, v in enumerate(arr):
        if not _is_scalar_label(v):
            errors.append(
                f"Block {bid!r}: {field_name}[{i}] must be a string or number "
                f"(got {type(v).__name__}: {v!r})"
            )
    return errors


def _validate_chart_length(block: dict) -> list[str]:
    errors: list[str] = []
    config = block.get("config", {})
    btype = block.get("type")
    bid = block.get("id", "?")

    if btype == "bar_chart":
        cats = config.get("categories", [])
        errors.extend(_validate_label_array(cats, "categories", bid))
        for i, s in enumerate(config.get("series", [])):
            vals = s.get("values", [])
            if len(vals) != len(cats):
                errors.append(
                    f"Block {bid!r}: series[{i}].values length ({len(vals)}) "
                    f"!= categories length ({len(cats)})"
                )

    elif btype in ("line_chart", "area_chart"):
        x = config.get("x_axis", [])
        errors.extend(_validate_label_array(x, "x_axis", bid))
        for i, s in enumerate(config.get("series", [])):
            vals = s.get("values", [])
            if len(vals) != len(x):
                errors.append(
                    f"Block {bid!r}: series[{i}].values length ({len(vals)}) "
                    f"!= x_axis length ({len(x)})"
                )

    elif btype == "pie_chart":
        labels = config.get("labels", [])
        values = config.get("values", [])
        errors.extend(_validate_label_array(labels, "labels", bid))
        if len(labels) != len(values):
            errors.append(
                f"Block {bid!r}: labels length ({len(labels)}) "
                f"!= values length ({len(values)})"
            )

    elif btype == "heatmap":
        x = config.get("x_axis", [])
        errors.extend(_validate_label_array(x, "x_axis", bid))
        for i, s in enumerate(config.get("series", [])):
            vals = s.get("values", [])
            if len(vals) != len(x):
                errors.append(
                    f"Block {bid!r}: series[{i}].values length ({len(vals)}) "
                    f"!= x_axis length ({len(x)})"
                )

    return errors


def _validate_chart_style(block: dict) -> list[str]:
    errors: list[str] = []
    config = block.get("config", {})
    btype = block.get("type")
    bid = block.get("id", "?")

    if btype in ("line_chart", "area_chart"):
        if "curve" in config and config["curve"] not in CURVE_VALUES:
            errors.append(
                f"Block {bid!r}: curve {config['curve']!r} invalid "
                f"(allowed: {sorted(CURVE_VALUES)})"
            )
        if "stroke_width" in config and not isinstance(config["stroke_width"], (int, float)):
            errors.append(f"Block {bid!r}: stroke_width must be numeric")
        if "show_markers" in config and not isinstance(config["show_markers"], bool):
            errors.append(f"Block {bid!r}: show_markers must be bool")

    if btype == "area_chart":
        fo = config.get("fill_opacity")
        if fo is not None and not (isinstance(fo, (int, float)) and 0 <= fo <= 1):
            errors.append(f"Block {bid!r}: fill_opacity must be a number in [0,1]")

    if btype == "bar_chart":
        if "show_data_labels" in config and not isinstance(config["show_data_labels"], bool):
            errors.append(f"Block {bid!r}: show_data_labels must be bool")
        if "border_radius" in config and not isinstance(config["border_radius"], (int, float)):
            errors.append(f"Block {bid!r}: border_radius must be numeric")

    if btype == "pie_chart":
        lp = config.get("legend_position")
        if lp is not None and lp not in LEGEND_POSITIONS:
            errors.append(
                f"Block {bid!r}: legend_position {lp!r} invalid "
                f"(allowed: {sorted(LEGEND_POSITIONS)})"
            )
        if "show_data_labels" in config and not isinstance(config["show_data_labels"], bool):
            errors.append(f"Block {bid!r}: show_data_labels must be bool")

    if btype == "heatmap":
        if "show_values" in config and not isinstance(config["show_values"], bool):
            errors.append(f"Block {bid!r}: show_values must be bool")

    return errors


# ── Block validators ─────────────────────────────────────────────────────────

def validate_block(block: dict, *, allow_section: bool = False) -> list[str]:
    """Validate a single block.

    `allow_section`: True when validating a top-level block (sections are only
    allowed at the top level). False when validating a child of a section
    (children must be leaves).
    """
    errors: list[str] = []
    btype = block.get("type")
    bid = block.get("id", "?")

    if btype not in BLOCK_TYPES:
        errors.append(f"Block {bid!r}: unknown type {btype!r}")
        return errors

    if btype in CONTAINER_BLOCK_TYPES and not allow_section:
        errors.append(
            f"Block {bid!r}: section_header cannot be nested inside another section"
        )
        return errors

    width = block.get("width")
    if width is not None:
        if btype in NO_WIDTH_TYPES:
            errors.append(f"Block {bid!r}: {btype} must not declare a width")
        elif width not in WIDTH_VALUES:
            errors.append(
                f"Block {bid!r}: width {width!r} is invalid (allowed: {sorted(WIDTH_VALUES)})"
            )

    config = block.get("config", {})

    if btype == "kpi":
        for field in ("value", "unit", "delta", "delta_label", "period"):
            if field not in config:
                errors.append(f"Block {bid!r}: kpi config missing {field!r}")
        if "value" in config and not isinstance(config["value"], (int, float)):
            errors.append(f"Block {bid!r}: kpi.value must be numeric")

    elif btype in ("bar_chart", "line_chart", "area_chart", "heatmap"):
        errors.extend(_validate_chart_length(block))
        errors.extend(_validate_chart_style(block))

    elif btype == "pie_chart":
        if "labels" not in config:
            errors.append(f"Block {bid!r}: pie_chart config missing 'labels'")
        if "values" not in config:
            errors.append(f"Block {bid!r}: pie_chart config missing 'values'")
        errors.extend(_validate_chart_length(block))
        errors.extend(_validate_chart_style(block))

    elif btype == "radial_bar":
        if "value" not in config:
            errors.append(f"Block {bid!r}: radial_bar config missing 'value'")
        if "value" in config and not isinstance(config["value"], (int, float)):
            errors.append(f"Block {bid!r}: radial_bar.value must be numeric")
        if "max" in config and not isinstance(config["max"], (int, float)):
            errors.append(f"Block {bid!r}: radial_bar.max must be numeric")

    elif btype == "narrative":
        if "text" not in config:
            errors.append(f"Block {bid!r}: narrative config missing 'text'")

    elif btype == "data_table":
        cols = config.get("columns")
        rows = config.get("rows")
        if not isinstance(cols, list) or not cols:
            errors.append(f"Block {bid!r}: data_table config missing or empty 'columns'")
        else:
            for i, c in enumerate(cols):
                if not isinstance(c, dict) or not c.get("field"):
                    errors.append(f"Block {bid!r}: columns[{i}] must be {{field, header?}}")
        if not isinstance(rows, list):
            errors.append(f"Block {bid!r}: data_table config missing 'rows' (must be list)")

    elif btype == "section_header":
        # Children validation handled here so section_header errors propagate.
        children = block.get("children", [])
        if not isinstance(children, list):
            errors.append(f"Block {bid!r}: section_header.children must be a list")
        else:
            for i, child in enumerate(children):
                child_errors = validate_block(child, allow_section=False)
                errors.extend(f"section[{bid!r}].children[{i}]: {e}" for e in child_errors)

    return errors


def validate_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []

    if "meta" not in manifest:
        errors.append("Missing 'meta' key")
    if "blocks" not in manifest:
        errors.append("Missing 'blocks' key")
        return errors

    blocks = manifest.get("blocks", [])
    for i, block in enumerate(blocks):
        if block.get("type") != "section_header":
            errors.append(
                f"Top-level blocks[{i}] must be a section_header (got {block.get('type')!r})"
            )
            continue
        errors.extend(validate_block(block, allow_section=True))

    return errors


# ── Traversal helpers (used by other modules) ────────────────────────────────

def iter_all_blocks(manifest: dict):
    """Yield every block (sections + their children) flat. Useful for
    operations that need to see every leaf, like locked-block check or
    LLM context summary."""
    for section in manifest.get("blocks", []):
        yield section
        for child in section.get("children", []) or []:
            yield child


def find_block_by_id(manifest: dict, block_id: str):
    """Locate a block anywhere in the manifest. Returns (block, path) or
    (None, None). `path` is the JSON-pointer-style path you can target with
    a patch (e.g. '/blocks/0/children/2')."""
    for si, section in enumerate(manifest.get("blocks", [])):
        if section.get("id") == block_id:
            return section, f"/blocks/{si}"
        for ci, child in enumerate(section.get("children", []) or []):
            if child.get("id") == block_id:
                return child, f"/blocks/{si}/children/{ci}"
    return None, None

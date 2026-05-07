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


BLOCK_TYPES = frozenset({
    "section_header", "kpi", "narrative",
    "bar_chart",          # cols / stacked / horizontal via config flags
    "line_chart",
    "area_chart",         # filled line
    "pie_chart",          # also handles donut via config.donut=true
    "heatmap",            # 2D matrix
    "radial_bar",         # gauge-style single value w/ optional max
})

# Fields that neither LLM patches nor any automated process may touch.
# Users can still toggle `locked` via the direct store action.
IMMUTABLE_BLOCK_FIELDS = frozenset({"id", "type", "locked"})

# Top-level paths that patches are permitted to target.
ALLOWED_PATCH_PREFIXES = ("/blocks/", "/meta/")

# Block width — Phase 6. Optional; default "full" preserves the original
# single-column layout. Frontend uses a 12-column CSS grid; widths map to spans.
WIDTH_VALUES = frozenset({"full", "1/2", "1/3", "2/3"})

# section_header is always full-width; it acts as a row divider.
NO_WIDTH_TYPES = frozenset({"section_header"})

# Allowed values for style options on chart blocks.
CURVE_VALUES = frozenset({"smooth", "straight", "stepline"})
LEGEND_POSITIONS = frozenset({"top", "right", "bottom", "left"})


# ── Block validators ──────────────────────────────────────────────────────────

def _is_scalar_label(v) -> bool:
    """Acceptable axis-label types: string, int, float, bool. Anything else
    (dict, list) means the LLM produced a structured value where a label was
    expected — caller will reject."""
    return isinstance(v, (str, int, float, bool))


def _validate_label_array(arr, field_name: str, bid: str) -> list[str]:
    """All entries must be scalars (str/number). Reject if any entry is a
    dict/list — common LLM mistake."""
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
    """Per-chart-type style options (all optional)."""
    errors: list[str] = []
    config = block.get("config", {})
    btype = block.get("type")
    bid = block.get("id", "?")

    # line_chart / area_chart
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

    # bar_chart
    if btype == "bar_chart":
        if "show_data_labels" in config and not isinstance(config["show_data_labels"], bool):
            errors.append(f"Block {bid!r}: show_data_labels must be bool")
        if "border_radius" in config and not isinstance(config["border_radius"], (int, float)):
            errors.append(f"Block {bid!r}: border_radius must be numeric")

    # pie_chart
    if btype == "pie_chart":
        lp = config.get("legend_position")
        if lp is not None and lp not in LEGEND_POSITIONS:
            errors.append(
                f"Block {bid!r}: legend_position {lp!r} invalid "
                f"(allowed: {sorted(LEGEND_POSITIONS)})"
            )
        if "show_data_labels" in config and not isinstance(config["show_data_labels"], bool):
            errors.append(f"Block {bid!r}: show_data_labels must be bool")

    # heatmap
    if btype == "heatmap":
        if "show_values" in config and not isinstance(config["show_values"], bool):
            errors.append(f"Block {bid!r}: show_values must be bool")

    return errors


def validate_block(block: dict) -> list[str]:
    errors: list[str] = []
    btype = block.get("type")
    bid = block.get("id", "?")

    if btype not in BLOCK_TYPES:
        errors.append(f"Block {bid!r}: unknown type {btype!r}")
        return errors

    # width validation (Phase 6) — optional field
    width = block.get("width")
    if width is not None:
        if btype in NO_WIDTH_TYPES:
            errors.append(f"Block {bid!r}: {btype} must not declare a width (always full)")
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

    # section_header: config is {} — nothing to validate

    return errors


def validate_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []

    if "meta" not in manifest:
        errors.append("Missing 'meta' key")
    if "blocks" not in manifest:
        errors.append("Missing 'blocks' key")
        return errors

    for block in manifest.get("blocks", []):
        errors.extend(validate_block(block))

    return errors

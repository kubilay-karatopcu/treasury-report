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


class ComboSeries(TypedDict):
    name: str
    values: list[float]
    kind: str   # "bar" | "line"
    axis: str   # "left" | "right"


class ComboChartConfig(TypedDict):
    # Combo (dual-axis) chart — single query, column-split. categories = col 0,
    # series = cols 1..N (same binding as bar_chart). Each series additionally
    # carries a user-set kind (bar/line) + axis (left/right).
    categories: list[str]
    series: list[ComboSeries]
    left_axis_title: str
    right_axis_title: str


class NarrativeConfig(TypedDict):
    text: str


# Block types — section_header is a CONTAINER (has children), all others are
# leaf blocks that live INSIDE a section's children[].
LEAF_BLOCK_TYPES = frozenset({
    "kpi", "narrative",
    "bar_chart", "line_chart", "area_chart",
    "pie_chart", "heatmap", "radial_bar",
    "combo_chart",      # dual-axis bar+line (single query, column-split)
    "data_table",       # AG Grid table block
})

# section_header — yalnızca top-level; children: leaf VEYA container (carousel/canvas)
# carousel       — yalnızca section.children içinde; children: leaf ONLY (>=1 slide)
# canvas         — yalnızca section.children içinde; children: leaf ONLY (0+; 12-kol grid)
#                  (madde 2 — genel container; leaf-only, nested container ileriki iş)
CHILD_CONTAINER_TYPES = frozenset({"carousel", "canvas"})
CONTAINER_BLOCK_TYPES = frozenset({"section_header"}) | CHILD_CONTAINER_TYPES

BLOCK_TYPES = LEAF_BLOCK_TYPES | CONTAINER_BLOCK_TYPES

IMMUTABLE_BLOCK_FIELDS = frozenset({"id", "type", "locked"})

ALLOWED_PATCH_PREFIXES = ("/blocks/", "/meta/", "/filters", "/filter_state", "/user_concepts")

# Block width — Phase 6.
WIDTH_VALUES = frozenset({"full", "1/2", "1/3", "2/3"})
NO_WIDTH_TYPES = frozenset({"section_header"})

# Style option values.
CURVE_VALUES = frozenset({"smooth", "straight", "stepline"})
LEGEND_POSITIONS = frozenset({"top", "right", "bottom", "left"})
# Combo chart per-series role + axis side.
COMBO_SERIES_KINDS = frozenset({"bar", "line"})
AXIS_SIDES = frozenset({"left", "right"})
DATA_SOURCE_TYPES = frozenset({
"kpi", "bar_chart", "line_chart", "area_chart", "pie_chart",
"heatmap", "radial_bar", "combo_chart", "data_table",
})

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

    elif btype == "combo_chart":
        cats = config.get("categories", [])
        errors.extend(_validate_label_array(cats, "categories", bid))
        for i, s in enumerate(config.get("series", [])):
            vals = s.get("values", [])
            if len(vals) != len(cats):
                errors.append(
                    f"Block {bid!r}: series[{i}].values length ({len(vals)}) "
                    f"!= categories length ({len(cats)})"
                )
            kind = s.get("kind", "bar")
            if kind not in COMBO_SERIES_KINDS:
                errors.append(
                    f"Block {bid!r}: series[{i}].kind {kind!r} invalid "
                    f"(allowed: {sorted(COMBO_SERIES_KINDS)})"
                )
            axis = s.get("axis", "left")
            if axis not in AXIS_SIDES:
                errors.append(
                    f"Block {bid!r}: series[{i}].axis {axis!r} invalid "
                    f"(allowed: {sorted(AXIS_SIDES)})"
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

def validate_block(
    block: dict, *, allow_section: bool = False,
    allow_containers: frozenset[str] = frozenset(),
) -> list[str]:
    """Validate a single block.

    `allow_section`: True when validating a top-level block (section_header
    is only allowed at the top level).
    `allow_containers`: which CHILD container types (carousel/canvas) may appear
    at this position. Nesting rules:
      - section.children  → {carousel, canvas}  (containers next to leaves)
      - carousel.children → {canvas}            (a slide may be a canvas layout)
      - canvas.children   → {}                  (leaves only — no deeper nesting)
    This bounds depth at section > carousel > canvas > leaf (no carousel-in-
    carousel / canvas-in-canvas)."""
    errors: list[str] = []
    btype = block.get("type")
    bid = block.get("id", "?")

    if btype not in BLOCK_TYPES:
        errors.append(f"Block {bid!r}: unknown type {btype!r}")
        return errors

    # ── Phase 6.5 query + variables (shape-loose) ──────────────────────────
    # All data-bound blocks may carry `query` (raw SQL with :binds) and
    # `variables` (Phase 6.5 schema). Strict variable validation runs in
    # presentations.blocks.schema.Variable when a block is saved as a
    # template; here we only enforce the gross shape so the renderer
    # doesn't crash on malformed leaves.
    q = block.get("query")
    if q is not None and not isinstance(q, str):
        errors.append(f"Block {bid!r}: block.query must be a string")
    vs = block.get("variables")
    if vs is not None:
        if not isinstance(vs, list):
            errors.append(f"Block {bid!r}: block.variables must be a list")
        else:
            for i, v in enumerate(vs):
                if not isinstance(v, dict):
                    errors.append(
                        f"Block {bid!r}: variables[{i}] must be an object"
                    )
                    continue
                if not v.get("name"):
                    errors.append(
                        f"Block {bid!r}: variables[{i}] missing 'name'"
                    )

    if btype == "section_header" and not allow_section:
        errors.append(
            f"Block {bid!r}: section_header sadece üst seviyede olabilir"
        )
        return errors
    if btype in CHILD_CONTAINER_TYPES and btype not in allow_containers:
        errors.append(
            f"Block {bid!r}: {btype} bu konumda olamaz "
            f"(izinli container'lar: {sorted(allow_containers) or 'yok'})"
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

    elif btype in ("bar_chart", "line_chart", "area_chart", "heatmap", "combo_chart"):
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
        # Children: leaf VEYA container (carousel/canvas).
        children = block.get("children", [])
        if not isinstance(children, list):
            errors.append(f"Block {bid!r}: section_header.children must be a list")
        else:
            for i, child in enumerate(children):
                child_errors = validate_block(
                    child, allow_section=False,
                    allow_containers=CHILD_CONTAINER_TYPES,   # carousel | canvas
                )
                errors.extend(f"section[{bid!r}].children[{i}]: {e}" for e in child_errors)

    elif btype == "carousel":
        # Children: leaf VEYA canvas (bir slide çok-bloklu tuval olabilir).
        children = block.get("children", [])
        if not isinstance(children, list):
            errors.append(f"Block {bid!r}: carousel.children must be a list")
        elif len(children) < 1:
            errors.append(f"Block {bid!r}: carousel en az 1 slide içermeli")
        else:
            for i, child in enumerate(children):
                child_errors = validate_block(
                    child, allow_section=False,
                    allow_containers=frozenset({"canvas"}),   # slide = leaf | canvas
                )
                errors.extend(f"carousel[{bid!r}].children[{i}]: {e}" for e in child_errors)
        # `active_idx` opsiyonel, varsa integer ve range içinde olmalı
        ai = block.get("active_idx")
        if ai is not None:
            if not isinstance(ai, int):
                errors.append(f"Block {bid!r}: carousel.active_idx must be integer")
            elif isinstance(children, list) and not (0 <= ai < len(children)):
                errors.append(f"Block {bid!r}: carousel.active_idx {ai} out of range")

    elif btype == "canvas":
        # Genel container (madde 2). Children: SADECE leaf — bir 12-kolon CSS
        # grid'de yan yana dizilir (her child'ın `width`'i kolon span'ini verir).
        # Boş canvas geçerli (kullanıcı blok ekleyene kadar boş-state gösterilir);
        # carousel'in aksine min-slide kuralı yok. Nested container ileriki iş.
        children = block.get("children", [])
        if not isinstance(children, list):
            errors.append(f"Block {bid!r}: canvas.children must be a list")
        else:
            for i, child in enumerate(children):
                child_errors = validate_block(
                    child, allow_section=False,
                    allow_containers=frozenset(),   # leaf-only (no deeper nesting)
                )
                errors.extend(f"canvas[{bid!r}].children[{i}]: {e}" for e in child_errors)
    
    if "data_source" in block:
        errors.extend(_validate_data_source(block))
            
    return errors


def validate_manifest(manifest: dict) -> list[str]:
    """Validate a presentation/dashboard manifest.

    Optional top-level fields (all backwards-compatible — absence preserves
    pre-existing behaviour):

    - ``filters`` (Phase 6.5.c): list of dashboard filters.
    - ``scope_ref`` (Phase 8.a): ``{presentation_id, scope_version}`` pointing
      at a scope contract (``s3://.../scope_v<N>.yaml``). When present, Sunum
      surfaces the scope's interactive filters and enforces its pinned filters
      (see ``presentations/scope`` + ``nodes/validate_patch.py``). When absent,
      the dashboard behaves exactly as in Phase 6.5/7: all filters interactive,
      all tables cached.
    """
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

    # ── Phase 6.5.c: top-level filters[] (optional) ───────────────────────
    filters = manifest.get("filters")
    if filters is not None:
        if not isinstance(filters, list):
            errors.append("manifest.filters must be a list")
        else:
            from presentations.dashboards.schema import DashboardFilter
            seen_ids: set[str] = set()
            for i, f in enumerate(filters):
                if not isinstance(f, dict):
                    errors.append(f"filters[{i}] must be an object")
                    continue
                try:
                    df = DashboardFilter.model_validate(f)
                except Exception as exc:
                    errors.append(f"filters[{i}]: {exc}")
                    continue
                if df.id in seen_ids:
                    errors.append(f"filters[{i}]: duplicate id {df.id!r}")
                seen_ids.add(df.id)

    # ── Phase 8.a: optional scope_ref ─────────────────────────────────────
    scope_ref = manifest.get("scope_ref")
    if scope_ref is not None:
        from presentations.scope.schema import ScopeRef
        try:
            ScopeRef.model_validate(scope_ref)
        except Exception as exc:
            errors.append(f"scope_ref: {exc}")

    # ── Phase 10B: optional bound_experts ─────────────────────────────────
    # Migration always defaults to [] so this field is present on every
    # manifest after load; the typecheck still has to work for raw input
    # from the API (snapshot save body, direct PATCH, etc.).
    bound = manifest.get("bound_experts")
    if bound is not None:
        if not isinstance(bound, list):
            errors.append("manifest.bound_experts must be a list of expert IDs")
        else:
            for i, x in enumerate(bound):
                if not isinstance(x, str):
                    errors.append(f"bound_experts[{i}] must be a string")
                    continue
            # Existence check is done against the live ExpertStore when one
            # is reachable via current_app.config. Keep the import lazy so
            # this module stays importable in tests that build a bare app.
            try:
                from flask import current_app
                store = current_app.config.get("EXPERT_STORE") if current_app else None
            except RuntimeError:
                store = None
            if store is not None:
                for i, eid in enumerate(bound):
                    if isinstance(eid, str) and not store.exists(eid):
                        errors.append(
                            f"bound_experts[{i}]: unknown expert id {eid!r}"
                        )

    return errors


# ── Traversal helpers (used by other modules) ────────────────────────────────

def iter_all_blocks(manifest: dict):
    """Yield every block (sections + their children + carousel slides) flat.
    Useful for operations that need to see every leaf, like locked-block check
    or LLM context summary."""
    def _walk(blocks):
        for b in blocks or []:
            yield b
            # Container'ların (section/carousel/canvas) çocuklarına recursively in
            if b.get("type") in CONTAINER_BLOCK_TYPES:
                yield from _walk(b.get("children", []) or [])

    yield from _walk(manifest.get("blocks", []))


def find_block_by_id(manifest: dict, block_id: str):
    """Locate a block anywhere in the manifest (any nesting depth). Returns
    (block, path) or (None, None). `path` is the JSON-pointer-style path you can
    target with a patch — e.g. '/blocks/0/children/2' (leaf), or
    '/blocks/0/children/1/children/0/children/2' for a leaf inside a canvas that
    is itself a carousel slide (section > carousel > canvas > leaf)."""
    def _walk(blocks, base):
        for i, b in enumerate(blocks or []):
            path = f"{base}/{i}"
            if b.get("id") == block_id:
                return b, path
            if b.get("type") in CONTAINER_BLOCK_TYPES:
                hit = _walk(b.get("children", []) or [], f"{path}/children")
                if hit[0] is not None:
                    return hit
        return None, None

    return _walk(manifest.get("blocks", []), "/blocks")


def _validate_data_source(block: dict) -> list[str]:
    """Block.data_source schema doğrulaması. Tüm alanlar opsiyonel-tipli ama
    eğer varsa doğru tipte olmalı; ek olarak bu alanı section_header / narrative
    block tipleri taşıyamaz.
    """
    errors: list[str] = []
    bid = block.get("id", "?")
    btype = block.get("type")
    ds = block.get("data_source")
 
    if not isinstance(ds, dict):
        errors.append(f"Block {bid!r}: data_source must be an object")
        return errors
 
    if btype not in DATA_SOURCE_TYPES:
        errors.append(
            f"Block {bid!r}: {btype} cannot carry a data_source "
            f"(allowed for: {sorted(DATA_SOURCE_TYPES)})"
        )
        return errors
 
    if "sql" not in ds or not isinstance(ds["sql"], str) or not ds["sql"].strip():
        errors.append(f"Block {bid!r}: data_source.sql must be a non-empty string")
 
    # Other fields are produced server-side; we sanity-check types only, no
    # strict requirement on presence (so older manifests upgrade gracefully).
    if "row_count" in ds and not isinstance(ds["row_count"], int):
        errors.append(f"Block {bid!r}: data_source.row_count must be an integer")
    if "truncated" in ds and not isinstance(ds["truncated"], bool):
        errors.append(f"Block {bid!r}: data_source.truncated must be a boolean")
    if "columns" in ds and not isinstance(ds["columns"], list):
        errors.append(f"Block {bid!r}: data_source.columns must be a list")
    if "preview_rows" in ds and not isinstance(ds["preview_rows"], list):
        errors.append(f"Block {bid!r}: data_source.preview_rows must be a list")

    return errors

    
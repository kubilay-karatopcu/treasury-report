"""
Run LLM-produced SQL (Oracle OR DuckDB) for any block touched by this batch.

Routing:
- SQL with `upload__...` references  → DuckDB-only path (Excel from S3)
- Other SQL                          → Oracle via DataClient

apply_data_to_config still runs after every execute, on both paths.
section_header.children recursion preserved (Adım 5.1).
"""
from __future__ import annotations

import logging

from flask import current_app

from presentations.aggregation_gate import GateError
from presentations.duck import execute_block_sql, build_upload_lookup
from presentations.manifest import DATA_SOURCE_TYPES, find_block_by_id

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Config mapping — public so refresh endpoint can reuse it
# ════════════════════════════════════════════════════════════════════════════

_DATA_KEYS_BY_TYPE = {
    "kpi":        {"value"},
    "radial_bar": {"value"},
    "bar_chart":  {"categories", "series"},
    "line_chart": {"x_axis", "series"},
    "area_chart": {"x_axis", "series"},
    "heatmap":    {"x_axis", "series"},
    "combo_chart": {"categories", "series"},
    "pie_chart":  {"labels", "values"},
    "data_table": {"columns", "rows"},
}


def apply_data_to_config(block: dict, data_source: dict) -> None:
    """Map SQL result (data_source.rows + columns) into block.config in place.
    Style fields are preserved untouched."""
    btype = block.get("type")
    if btype not in _DATA_KEYS_BY_TYPE:
        return

    config = block.setdefault("config", {})
    rows = data_source.get("rows") or []
    columns = data_source.get("columns") or []

    # No columns AND no rows means the query returned an empty result set
    # (or was short-circuited as "empty"). We MUST still clear the chart
    # config — otherwise the canvas keeps rendering the previous result
    # while the SQL panel says "0 satır", which is a confusing mismatch.
    if not columns:
        if not rows:
            _clear_config_for_type(btype, config)
        return

    if btype in ("kpi", "radial_bar"):
        target_col_idx = _pick_numeric_col_idx(columns, rows)
        if target_col_idx is not None and rows:
            value = rows[0][target_col_idx]
            if isinstance(value, (int, float)) and value == value:
                config["value"] = value
        return

    if btype == "pie_chart":
        if len(columns) < 2 or not rows:
            return
        config["labels"] = [_safe_label(r[0]) for r in rows]
        config["values"] = [_safe_number(r[1]) for r in rows]
        return

    if btype == "bar_chart":
        if not rows:
            config["categories"] = []
            _zero_out_series(config)
            return
        config["categories"] = [_safe_label(r[0]) for r in rows]
        config["series"] = _build_series(columns, rows, config.get("series"))
        return

    if btype == "heatmap":
        # Heatmap schema uses ``x_axis`` (NOT ``categories``) — the renderer reads
        # `config.x_axis ?? config.categories`, and a seed `x_axis: []` (not
        # nullish) shadows any `categories` we'd set, so writing `categories`
        # here made the chart show "veri yok".
        if not rows:
            config["x_axis"] = []
            _zero_out_series(config)
            return
        # Long format — `(rowDim, colDim, value)`: the natural way to write a
        # heatmap query (`SELECT a, b, SUM(x) GROUP BY a, b`). Pivot it into the
        # x_axis + per-row series matrix. Detected by: exactly 3 columns where
        # the 2nd column is non-numeric (a dimension, not a value).
        if len(columns) == 3 and not _col_is_numeric(rows, 1):
            x_axis, series = _heatmap_pivot(rows)
            config["x_axis"] = x_axis
            config["series"] = series
        else:
            # Wide format — col0 = row label, cols 1..N = numeric matrix columns.
            config["x_axis"] = [_safe_label(r[0]) for r in rows]
            config["series"] = _build_series(columns, rows, config.get("series"))
        return

    if btype in ("line_chart", "area_chart"):
        if not rows:
            config["x_axis"] = []
            _zero_out_series(config)
            return
        config["x_axis"] = [_safe_label(r[0]) for r in rows]
        config["series"] = _build_series(columns, rows, config.get("series"))
        return

    if btype == "combo_chart":
        # Single query, column-split: col 0 → categories, cols 1..N → series.
        # Each series keeps its user-set kind (bar/line) + axis (left/right).
        if not rows:
            config["categories"] = []
            _zero_out_series(config)
            return
        config["categories"] = [_safe_label(r[0]) for r in rows]
        config["series"] = _build_combo_series(columns, rows, config.get("series"))
        return

    if btype == "data_table":
        config["columns"] = [{"field": c, "header": c} for c in columns]
        config["rows"] = [
            {columns[i]: cell for i, cell in enumerate(row)}
            for row in rows
        ]
        return


def _clear_config_for_type(btype: str, config: dict) -> None:
    """Reset the data-bearing fields of ``config`` for ``btype`` so the
    renderer falls through to its "no data" placeholder. Style fields
    (colors, titles, labels) are left intact so the next successful run
    restores the user's customizations."""
    if btype == "kpi":
        config["value"] = 0
    elif btype == "radial_bar":
        config["value"] = 0
    elif btype == "pie_chart":
        config["labels"] = []
        config["values"] = []
    elif btype in ("bar_chart", "combo_chart"):
        config["categories"] = []
        _zero_out_series(config)
    elif btype in ("line_chart", "area_chart", "heatmap"):
        config["x_axis"] = []
        _zero_out_series(config)
    elif btype == "data_table":
        config["columns"] = []
        config["rows"] = []


def _build_series(columns, rows, existing_series):
    series = []
    existing = existing_series if isinstance(existing_series, list) else []
    for col_idx in range(1, len(columns)):
        col_name = columns[col_idx]
        values = [_safe_number(row[col_idx]) for row in rows]
        name = col_name
        if col_idx - 1 < len(existing):
            existing_name = existing[col_idx - 1].get("name")
            if existing_name:
                name = existing_name
        series.append({"name": name, "values": values})
    return series


def _build_combo_series(columns, rows, existing_series):
    """Like _build_series, but each series also carries kind (bar/line) + axis
    (left/right). Roles are preserved by index across re-runs; new series get a
    sensible default (first series = bar/right, the rest = line/left)."""
    existing = existing_series if isinstance(existing_series, list) else []
    series = []
    for col_idx in range(1, len(columns)):
        s_idx = col_idx - 1
        name = columns[col_idx]
        values = [_safe_number(row[col_idx]) for row in rows]
        kind = "bar" if s_idx == 0 else "line"
        axis = "right" if s_idx == 0 else "left"
        if s_idx < len(existing) and isinstance(existing[s_idx], dict):
            ex = existing[s_idx]
            if ex.get("name"):
                name = ex["name"]
            if ex.get("kind") in ("bar", "line"):
                kind = ex["kind"]
            if ex.get("axis") in ("left", "right"):
                axis = ex["axis"]
        series.append({"name": name, "values": values, "kind": kind, "axis": axis})
    return series


def _zero_out_series(config):
    series = config.get("series")
    if isinstance(series, list):
        for s in series:
            if isinstance(s, dict):
                s["values"] = []


def _col_is_numeric(rows, idx):
    """True if every non-null value in column ``idx`` is a real number (not bool).
    All-null counts as numeric (no dimension signal)."""
    for r in rows:
        v = r[idx] if idx < len(r) else None
        if v is None:
            continue
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            return False
    return True


def _heatmap_pivot(rows):
    """Long format ``[(rowDim, colDim, value), …]`` → ``(x_axis, series)`` for a
    heatmap. col0 → series (the y-rows), col1 → x_axis (the shared x columns),
    col2 → cell value. First-seen order is preserved for both axes; missing
    cells default to 0."""
    x_axis, x_seen = [], set()
    row_keys, row_seen = [], set()
    cell = {}
    for r in rows:
        rk = _safe_label(r[0])
        ck = _safe_label(r[1])
        if rk not in row_seen:
            row_seen.add(rk)
            row_keys.append(rk)
        if ck not in x_seen:
            x_seen.add(ck)
            x_axis.append(ck)
        cell[(rk, ck)] = _safe_number(r[2])
    series = [
        {"name": rk, "values": [cell.get((rk, ck), 0) for ck in x_axis]}
        for rk in row_keys
    ]
    return x_axis, series


def _pick_numeric_col_idx(columns, rows):
    if not rows:
        return 0 if columns else None
    first_row = rows[0]
    for i, c in enumerate(columns):
        if str(c).lower() == "value":
            return i
    for i, v in enumerate(first_row):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return i
    return 0


def _safe_label(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _safe_number(v):
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        if v != v:
            return 0
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


# ════════════════════════════════════════════════════════════════════════════
# Per-block executor
# ════════════════════════════════════════════════════════════════════════════

def _execute_one_block(block_value, dc, conn, state, errors, patch_idx, op,
                       upload_lookup, s3_get):
    block_id = block_value.get("id")
    btype = block_value.get("type")

    if btype not in DATA_SOURCE_TYPES:
        _strip_data_source(block_value)
        return

    ds = block_value.get("data_source")
    if not isinstance(ds, dict):
        # Veri-bağımlı blok için data_source ZORUNLU. LLM'i retry'a zorla.
        errors.append(
            f"patch[{patch_idx}]: '{btype}' tipinde block '{block_id}' için "
            f"`data_source.original_sql` zorunlu — SQL eklemeden bu bloğa "
            f"veri getirilemez. Patch'i `data_source/original_sql` ve `config` "
            f"alanlarıyla birlikte yeniden gönder."
        )
        return

    new_sql = ds.get("original_sql") or ds.get("sql")
    if not isinstance(new_sql, str) or not new_sql.strip():
        # SQL boş → veri-bağımlı blok dolmaz. Retry tetikleyici hata.
        errors.append(
            f"patch[{patch_idx}]: block '{block_id}' için "
            f"`data_source.original_sql` boş — config'i doldurmak için "
            f"SQL üret. ÖRN: kpi → `SELECT SUM(X)/1e9 AS value FROM T`; "
            f"bar_chart → `SELECT cat, val FROM T GROUP BY cat ORDER BY val DESC`."
        )
        return

    # Cache: same SQL on an existing block → skip Oracle/DuckDB execute.
    # But ONLY if the existing data_source has actual rows — seed manifests
    # (and freshly-edited blocks) often have `original_sql` set but no rows
    # yet, in which case we must run the SQL to populate config.
    if op == "replace" and block_id:
        existing_block, _ = find_block_by_id(state.manifest, block_id)
        if existing_block is not None:
            existing_ds = existing_block.get("data_source")
            if (
                isinstance(existing_ds, dict)
                and _sql_equal(existing_ds.get("original_sql"), new_sql)
                and isinstance(existing_ds.get("rows"), list)
                and existing_ds.get("columns")
            ):
                block_value["data_source"] = dict(existing_ds)
                apply_data_to_config(block_value, existing_ds)
                log.info("execute_block_sqls: cache HIT for %s", block_id)
                return

    try:
        # Hold the per-session DuckDB lock across execute — the connection is
        # shared with concurrent HTTP requests on this presentation and is not
        # thread-safe.
        with state.session.duck_conn():
            new_ds = execute_block_sql(
                dc, conn, block_id, new_sql,
                upload_lookup=upload_lookup,
                s3_get=s3_get,
            )
    except GateError as exc:
        errors.append(f"patch[{patch_idx}]: block '{block_id}' SQL reddedildi → {exc}")
        return
    except Exception as exc:
        msg = str(exc).strip().splitlines()[0][:240]
        errors.append(f"patch[{patch_idx}]: block '{block_id}' SQL hatası → {msg}")
        log.exception("execute_block_sqls: failure on block %s", block_id)
        return

    block_value["data_source"] = new_ds
    apply_data_to_config(block_value, new_ds)


def _sql_equal(a, b) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return a.strip().rstrip(";").strip() == b.strip().rstrip(";").strip()


def _strip_data_source(value):
    if isinstance(value, dict) and "data_source" in value:
        value.pop("data_source", None)


def _strip_value_tree(value):
    """Recursively drop data_source from a whole-block value and all descendants
    (any nesting depth) — used on the no-session / no-DataClient fallback."""
    if not isinstance(value, dict):
        return
    _strip_data_source(value)
    for child in (value.get("children") or []):
        _strip_value_tree(child)


def _execute_block_subtree(node, dc, conn, state, errors, patch_idx,
                           upload_lookup, s3_get, handled_block_ids):
    """Recursively run SQL for every data-bound leaf in a whole-block value of
    any nesting depth (section > carousel > canvas > leaf). Containers carry no
    SQL of their own → their data_source is stripped; leaves are executed."""
    if not isinstance(node, dict):
        return
    if node.get("type") in DATA_SOURCE_TYPES:
        _execute_one_block(node, dc, conn, state, errors, patch_idx, "add",
                           upload_lookup, s3_get)
        if node.get("id"):
            handled_block_ids.add(node["id"])
        return
    _strip_data_source(node)
    if isinstance(node.get("children"), list):
        for child in node["children"]:
            _execute_block_subtree(child, dc, conn, state, errors, patch_idx,
                                   upload_lookup, s3_get, handled_block_ids)


# ════════════════════════════════════════════════════════════════════════════
# Main node
# ════════════════════════════════════════════════════════════════════════════

def execute_block_sqls(state):
    if not state.pending_patches:
        return state

    if state.session is None:
        for p in state.pending_patches:
            _strip_value_tree(p.get("value"))
        return state

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        log.warning("execute_block_sqls: no DATA_CLIENT in config, skipping")
        for p in state.pending_patches:
            _strip_value_tree(p.get("value"))
        return state

    # Excel routing prerequisites — built from the manifest, plus an S3 reader
    # injected at app startup via app.config["S3_GET"].
    upload_lookup = build_upload_lookup(state.manifest or {})
    s3_get = current_app.config.get("S3_GET")
    if upload_lookup and s3_get is None:
        log.warning(
            "execute_block_sqls: manifest has uploads but S3_GET not configured; "
            "Excel-backed blocks will error out at execute time"
        )

    conn = state.session.get_duck_conn()
    errors: list[str] = []

    # Track block IDs that were already handled by the dict-value branch so we
    # don't double-execute when a sub-path patch also targets the same block.
    handled_block_ids: set = set()

    # Synthetic patches appended at the end so the frontend ALSO receives the
    # SQL-driven config / data_source updates (otherwise only the original
    # LLM patch is sent and the UI keeps stale config values).
    extra_patches: list[dict] = []

    for i, patch in enumerate(state.pending_patches):
        op = patch.get("op")
        if op not in ("add", "replace"):
            continue
        value = patch.get("value")

        # ── Case A: whole-block value (add or full replace) ──
        if isinstance(value, dict):
            btype = value.get("type")
            if btype in DATA_SOURCE_TYPES:
                _execute_one_block(value, dc, conn, state, errors, i, op,
                                   upload_lookup, s3_get)
                if value.get("id"):
                    handled_block_ids.add(value["id"])
            else:
                # Container (section / carousel / canvas) — kendi SQL'i yok;
                # alt ağaçtaki her data-bound leaf'i recursively çalıştır
                # (herhangi derinlik: section > carousel > canvas > leaf).
                _execute_block_subtree(value, dc, conn, state, errors, i,
                                       upload_lookup, s3_get, handled_block_ids)
            continue

        # ── Case B: sub-path replace (e.g. /blocks/X/.../data_source/original_sql
        #            or /blocks/X/.../config/colors) ──
        # If the path targets ANYTHING inside a data-bound block, we need to
        # re-execute that block's SQL after the patches apply, otherwise the
        # config (categories/values) goes stale.
        target_block = _resolve_block_from_path(state.manifest, patch.get("path", ""))
        if target_block is not None and target_block.get("id") not in handled_block_ids:
            bid = target_block.get("id")
            btype = target_block.get("type")
            if btype in DATA_SOURCE_TYPES:
                # Simulate the patch on a shallow copy so we get the updated SQL
                # *before* apply_patch runs.
                simulated = _simulate_subpath_patch(target_block, patch)
                if simulated is not None:
                    _execute_one_block(simulated, dc, conn, state, errors, i, "replace",
                                       upload_lookup, s3_get)
                    # Copy executed fields back into the actual target_block in
                    # state.manifest so apply_patch doesn't overwrite them.
                    if "data_source" in simulated:
                        target_block["data_source"] = simulated["data_source"]
                    if "config" in simulated:
                        target_block["config"] = simulated["config"]
                    handled_block_ids.add(bid)

                    # Emit synthetic patches so the frontend receives the new
                    # config + data_source (otherwise it only sees the LLM's
                    # narrow patch and config never re-renders).
                    block_ptr = _block_pointer_from_path(patch.get("path", ""))
                    if block_ptr and "config" in simulated:
                        extra_patches.append({
                            "op": "replace",
                            "path": block_ptr + "/config",
                            "value": simulated["config"],
                        })
                    if block_ptr and "data_source" in simulated:
                        extra_patches.append({
                            "op": "replace",
                            "path": block_ptr + "/data_source",
                            "value": simulated["data_source"],
                        })

    if extra_patches:
        # Extra patch'leri eklemeden önce final apply'ı dry-run et — paths
        # geçersizse kullanıcıya retry için anlamlı hata ver, çökme.
        from presentations.patch import apply_patches
        candidate = list(state.pending_patches) + extra_patches
        try:
            apply_patches(state.manifest, candidate)
            state.pending_patches.extend(extra_patches)
        except Exception as exc:
            log.warning("execute_block_sqls: extra_patches dry-run failed: %s", exc)
            errors.append(
                f"execute sonrası eklenen patch'ler invalid: {exc!r}. "
                "Genelde önceki patch'ler manifest'i kaydırdığı için path'ler artık geçersiz."
            )

    if errors:
        state.validation_errors = (state.validation_errors or []) + errors

    return state


def _block_pointer_from_path(path: str) -> str | None:
    """Strip everything after the block identity from a JSON pointer.

      /blocks/0/children/1/data_source/original_sql              → /blocks/0/children/1
      /blocks/0/children/1/children/0/data_source/original_sql   → /blocks/0/children/1/children/0
      /blocks/2/data_source/original_sql                          → /blocks/2
      /blocks/3                                                   → /blocks/3
    """
    if not isinstance(path, str) or not path.startswith("/blocks/"):
        return None
    parts = path.lstrip("/").split("/")   # ['blocks','0','children','1',...]
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    # Longest prefix of the form /blocks/<i>(/children/<j>)* — any depth.
    ptr = ["blocks", parts[1]]
    k = 2
    while k + 1 < len(parts) and parts[k] == "children" and parts[k + 1].isdigit():
        ptr.extend(("children", parts[k + 1]))
        k += 2
    return "/" + "/".join(ptr)


def _resolve_block_from_path(manifest, path: str):
    """Walk a JSON-Pointer-style path and return the deepest fully-addressed
    block (the one with a `type` field). Returns None if the path doesn't land
    in a block subtree. Handles any nesting depth: section > carousel > canvas >
    leaf."""
    if not manifest or not isinstance(path, str) or not path.startswith("/blocks/"):
        return None
    parts = path.lstrip("/").split("/")
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    arr = manifest.get("blocks") or []
    node = None
    k = 1
    while k < len(parts) and parts[k].isdigit():
        idx = int(parts[k])
        if idx >= len(arr):
            return node   # deepest valid block addressed so far
        node = arr[idx]
        if k + 1 < len(parts) and parts[k + 1] == "children":
            arr = node.get("children") or []
            k += 2
        else:
            break
    return node


def _simulate_subpath_patch(block: dict, patch: dict):
    """Return a deep copy of `block` with the patch applied IN MEMORY ONLY.
    Used to capture the post-patch SQL so we can execute it before apply_patch
    runs at the pipeline tail."""
    import copy
    op = patch.get("op")
    path = patch.get("path", "")
    parts = path.lstrip("/").split("/")
    # Strip the head down to the block-relative remainder, at ANY nesting depth
    # (/blocks/X(/children/Y)*/<rest...>). _block_pointer_from_path gives the
    # block identity prefix; everything after it is the in-block field path.
    ptr = _block_pointer_from_path(path)
    if ptr is None:
        return None
    head_len = len(ptr.lstrip("/").split("/"))
    rest = parts[head_len:]
    if not rest:
        return None

    out = copy.deepcopy(block)
    target = out
    for p in rest[:-1]:
        if isinstance(target, list):
            try:
                p = int(p)
            except ValueError:
                return None
        if isinstance(target, dict):
            target = target.setdefault(p, {})
        elif isinstance(target, list) and p < len(target):
            target = target[p]
        else:
            return None
    last = rest[-1]
    try:
        if isinstance(target, list):
            target[int(last)] = patch.get("value")
        else:
            if op == "remove":
                target.pop(last, None)
            else:
                target[last] = patch.get("value")
    except Exception:
        return None
    return out
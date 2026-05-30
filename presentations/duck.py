"""
Oracle → Arrow → DuckDB bridge + Excel upload routing.

execute_block_sql now detects two engines:
  - Oracle (default): SQL goes through DataClient.get_data, single result view
  - DuckDB-only (when SQL references upload__... tables): we ensure those
    sheets are loaded into DuckDB from S3 first, then run SQL on the DuckDB
    connection directly. No Oracle round-trip.

Excel sheet references in SQL use a stable convention:
   upload__<upload_id>__<sheet_sanitised_name>
Example: `upload__u_a8Df12__targets`

The caller (LLM, refresh endpoint) writes this name in SQL; we resolve it at
execute time to (s3_key, sheet_display_name).
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import re
from typing import TYPE_CHECKING, Callable, Optional

from .aggregation_gate import GateError, GateResult, MAX_RAW_ROWS, validate_and_wrap

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


log = logging.getLogger(__name__)


# ── DuckDB connection hardening ─────────────────────────────────────────────
# Block SQL is user-/LLM-authored. DuckDB's default config lets a plain SELECT
# reach the filesystem and network (read_csv, read_text, read_blob, glob,
# ATTACH, COPY, INSTALL/LOAD). This module only ever queries in-memory
# relations registered from DataFrames plus the session database, so we disable
# external access on every connection that may run block SQL — closing an
# arbitrary file-read vector without removing any capability the app uses.
def connect_duckdb(database: str = ":memory:", **kwargs):
    """Open a DuckDB connection with external filesystem/network access off.

    Use everywhere user- or LLM-authored block SQL may execute (the per-session
    DB, library preview, the DEV stub). Callers may still pass extra ``config``
    keys; ``enable_external_access`` defaults to ``"false"`` unless overridden.
    """
    import duckdb

    config = dict(kwargs.pop("config", None) or {})
    config.setdefault("enable_external_access", "false")
    return duckdb.connect(database, config=config, **kwargs)


# ════════════════════════════════════════════════════════════════════════════
# LEGACY BASKET PATH — unchanged
# ════════════════════════════════════════════════════════════════════════════
# (preserved verbatim from previous version — fetch_basket_item,
#  register_dataframe, populate_basket, list_views, preview_view,
#  summarize_views, _compute_stats, _jsonable, _safe_identifier, _table_alias)

def _table_alias(table_id: str) -> str:
    return table_id.split(".")[-1].lower()


def fetch_basket_item(dc, basket_item: dict) -> "pd.DataFrame":
    import pandas as pd

    table_id = basket_item["table"]
    columns = basket_item.get("columns") or ["*"]
    row_filter = basket_item.get("row_filter")
    row_limit = int(basket_item.get("row_limit", MAX_RAW_ROWS))

    cols_sql = ", ".join(columns) if columns != ["*"] else "*"
    where_sql = f" WHERE {row_filter}" if row_filter else ""
    limit_sql = f" FETCH FIRST {row_limit} ROWS ONLY" if row_limit > 0 else ""
    sql = f"SELECT {cols_sql} FROM {table_id}{where_sql}{limit_sql}"

    df = dc.get_data(base_prefix=None, dataset=table_id, query=sql, query_params={})

    if df is None or len(df.columns) == 0:
        placeholder_cols = columns if columns != ["*"] else ["_no_data"]
        df = pd.DataFrame({c: pd.Series(dtype=object) for c in placeholder_cols})
        log.info("duck.fetch_basket_item: %s -> 0 rows (no mock data, placeholder schema)", table_id)
    else:
        log.info("duck.fetch_basket_item: %s -> %d rows", table_id, len(df))

    return df


def register_dataframe(conn, view_name, df):
    try:
        conn.unregister(view_name)
    except Exception:
        pass
    conn.register(view_name, df)
    log.debug("duck.register_dataframe: %s (%d rows)", view_name, len(df))


def populate_basket(dc, conn, basket):
    loaded = {}
    for item in basket:
        # Phase 11.basket-blocks: basket now also holds saved library blocks
        # (kind="block") which aren't Oracle tables — skip them in fetch.
        if item.get("kind") == "block" or not item.get("table"):
            continue
        # Upload-backed basket entries don't fetch via Oracle.
        if item["table"].startswith("upload__"):
            continue
        view = _table_alias(item["table"])
        df = fetch_basket_item(dc, item)
        register_dataframe(conn, view, df)
        loaded[view] = {"table": item["table"], "rows": len(df)}
    return loaded


def list_views(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
    ).fetchall()
    return [r[0] for r in rows]


def preview_view(conn, view_name, limit=10):
    safe_name = _safe_identifier(view_name)
    df = conn.execute(f"SELECT * FROM {safe_name} LIMIT {int(limit)}").fetchdf()
    total = conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
    return {
        "columns": list(df.columns),
        "rows": df.values.tolist(),
        "row_count": int(total),
    }


def _safe_identifier(name):
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe view name: {name!r}")
    return name


def summarize_views(conn, view_names, sample_rows=5):
    out = {}
    for view in view_names:
        try:
            safe = _safe_identifier(view)
        except ValueError:
            continue
        try:
            cols = conn.execute(f"DESCRIBE {safe}").fetchdf()
            col_list = [
                {"name": str(r["column_name"]), "type": str(r["column_type"])}
                for _, r in cols.iterrows()
            ]
            row_count = int(conn.execute(f"SELECT COUNT(*) FROM {safe}").fetchone()[0])
            sample = []
            if row_count > 0:
                sample_df = conn.execute(f"SELECT * FROM {safe} LIMIT {sample_rows}").fetchdf()
                sample = [
                    [_jsonable(v) for v in row]
                    for row in sample_df.itertuples(index=False, name=None)
                ]
            stats = _compute_stats(conn, safe, col_list, row_count)
            out[view] = {"columns": col_list, "row_count": row_count, "sample": sample, "stats": stats}
        except Exception as exc:
            log.warning("summarize_views: failed for %s: %s", view, exc)
    return out


_NUMERIC_TYPES = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")


def _compute_stats(conn, safe_view, col_list, row_count):
    if row_count == 0:
        return {}
    stats = {}
    for c in col_list:
        ctype = c["type"].upper()
        if not any(t in ctype for t in _NUMERIC_TYPES):
            continue
        col = c["name"]
        if not col.replace("_", "").isalnum():
            continue
        try:
            row = conn.execute(
                f'SELECT MIN("{col}") AS mn, MAX("{col}") AS mx, AVG("{col}") AS av FROM {safe_view}'
            ).fetchone()
            mn, mx, av = row
            argmax_row = conn.execute(
                f'SELECT * FROM {safe_view} WHERE "{col}" = ? LIMIT 1', [mx]
            ).fetchdf()
            argmax = (
                {k: _jsonable(v) for k, v in argmax_row.iloc[0].to_dict().items()}
                if not argmax_row.empty else {}
            )
            stats[col] = {
                "min": _jsonable(mn), "max": _jsonable(mx),
                "avg": _jsonable(av), "argmax": argmax,
            }
        except Exception as exc:
            log.debug("_compute_stats: skipped %s.%s: %s", safe_view, col, exc)
    return stats


def _jsonable(v):
    import math
    if v is None:
        return None
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return str(v)


# ════════════════════════════════════════════════════════════════════════════
# UPLOAD ROUTING — Excel sheets become DuckDB views on demand
# ════════════════════════════════════════════════════════════════════════════
#
# Convention for upload table names in SQL:
#   upload__<upload_id>__<sheet_sanitised_name>
#
# The resolver translates this to (s3_key, sheet_display_name). The catalog
# (delivered by the /sources endpoint) carries the mapping; we keep a small
# in-process lookup that's rebuilt from the manifest on each call.

# Regex: catches `upload__u_aBcD12__targets` and friends. Word-boundary safe.
_UPLOAD_REF_RE = re.compile(r"\b(upload__[A-Za-z0-9_]+)\b")


def find_upload_refs(sql: str) -> list[str]:
    """Return all `upload__...` table references in a SQL string."""
    return list(set(_UPLOAD_REF_RE.findall(sql)))


def ensure_upload_views(
    conn: "duckdb.DuckDBPyConnection",
    refs: list[str],
    upload_lookup: dict,
    s3_get: Callable[[str], bytes],
) -> dict:
    """For each upload reference, register the corresponding Excel sheet as a
    DuckDB view if it isn't already.

    `upload_lookup` maps `upload_id → {s3_key, sheets: {sheet_sanitised_name → display_name}}`.

    Returns a dict of `{ref: row_count}` for what was loaded (or already loaded).
    """
    import pandas as pd

    loaded = {}
    for ref in refs:
        # Parse `upload__<id>__<sheet>` → (id, sheet)
        upload_id, sheet_key = _split_upload_ref(ref)

        info = upload_lookup.get(upload_id)
        if not info:
            raise ValueError(f"Upload bulunamadı: {upload_id}")
        sheet_display = info["sheets"].get(sheet_key)
        if sheet_display is None:
            raise ValueError(f"Sheet bulunamadı: {ref}")

        # Already loaded?
        existing = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [ref],
        ).fetchone()
        if existing:
            loaded[ref] = -1   # marker: was cached
            continue

        # Fetch xlsx bytes from S3 and read the requested sheet
        try:
            blob = s3_get(info["s3_key"])
        except Exception as exc:
            raise RuntimeError(f"Excel S3'ten okunamadı ({ref}): {exc}") from exc

        df = pd.read_excel(io.BytesIO(blob), sheet_name=sheet_display, engine="openpyxl")
        # Rename columns to match the catalog's sanitised names so SQL works.
        if "columns_sanitised" in info and ref in info["columns_sanitised"]:
            df.columns = info["columns_sanitised"][ref]
        register_dataframe(conn, ref, df)
        loaded[ref] = len(df)
        log.info("duck.ensure_upload_views: registered %s (%d rows)", ref, len(df))

    return loaded


def _split_upload_ref(ref: str) -> tuple[str, str]:
    """`upload__u_aBcD12__targets` → ('u_aBcD12', 'targets')"""
    if not ref.startswith("upload__"):
        raise ValueError(f"Bad ref: {ref!r}")
    rest = ref[len("upload__"):]
    # `u_aBcD12__targets` — split on first `__`
    parts = rest.split("__", 1)
    if len(parts) != 2:
        raise ValueError(f"Bad ref: {ref!r}")
    return parts[0], parts[1]


def build_upload_lookup(manifest: dict) -> dict:
    """Rebuild the in-memory upload lookup from manifest.uploads."""
    lookup: dict = {}
    for u in (manifest.get("uploads") or []):
        upload_id = u.get("id")
        if not upload_id:
            continue
        sheets_map = {}
        columns_sanitised: dict = {}
        for sheet in u.get("sheets") or []:
            sheets_map[sheet["name"]] = sheet.get("display_name") or sheet["name"]
            ref = f"upload__{upload_id}__{sheet['name']}"
            columns_sanitised[ref] = [c["name"] for c in sheet.get("columns") or []]
        lookup[upload_id] = {
            "s3_key": u.get("s3_key"),
            "sheets": sheets_map,
            "columns_sanitised": columns_sanitised,
        }
    return lookup


# ════════════════════════════════════════════════════════════════════════════
# BLOCK-DRIVEN PATH — Oracle OR DuckDB-only
# ════════════════════════════════════════════════════════════════════════════

def _block_view_name(block_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", block_id).lower()
    if not safe or safe[0].isdigit():
        safe = "b_" + safe
    return f"block_{safe}"


_MAX_ROWS_IN_MANIFEST = MAX_RAW_ROWS


def execute_block_sql(
    dc,
    conn,
    block_id: str,
    sql: str,
    sample_rows: int = 5,
    *,
    upload_lookup: Optional[dict] = None,
    s3_get: Optional[Callable[[str], bytes]] = None,
):
    """Validate + execute SQL, returning a data_source dict.

    Routing:
      - SQL contains `upload__...` references → DuckDB-only. We pre-load each
        referenced sheet into DuckDB, then run SQL there.
      - Otherwise → Oracle via DataClient (original behaviour).
    """
    import pandas as pd

    from presentations.concepts.integration import strip_concept_sentinel

    gate: GateResult = validate_and_wrap(sql)

    # A block may carry the Phase-7 `{{concept_filters}}` sentinel in its SQL.
    # The concept-apply path substitutes it with real predicates; every OTHER
    # execution path (manual refresh, chat-node re-run, /execute route) reaches
    # here WITHOUT that substitution, so the literal token would hit Oracle as
    # `WHERE {{concept_filters}}` → ORA-00936 "missing expression". Neutralize it
    # to a no-op `1 = 1` for execution, while keeping the authored SQL (sentinel
    # intact) in `original_sql` so future concept injection still works. Mirrors
    # run_block_manual's strip-at-exec pattern.
    exec_sql = strip_concept_sentinel(gate.sql)

    log.info(
        "duck.execute_block_sql: block=%s gate=%s rewritten=%s truncated=%s cap=%d",
        block_id, gate.reason, gate.rewritten, gate.truncated, gate.cap,
    )

    refs = find_upload_refs(exec_sql)
    is_duckdb_only = bool(refs)

    if is_duckdb_only:
        if upload_lookup is None or s3_get is None:
            raise RuntimeError(
                "Excel referansı içeren SQL için upload_lookup ve s3_get gerekli."
            )
        ensure_upload_views(conn, refs, upload_lookup, s3_get)
        # Run the SQL directly against DuckDB.
        try:
            df = conn.execute(exec_sql).fetchdf()
        except Exception as exc:
            raise RuntimeError(f"DuckDB SQL hatası: {exc}") from exc
    else:
        df = dc.get_data(
            base_prefix=None,
            dataset=f"block::{block_id}",
            query=exec_sql,
            query_params={},
        )

    if df is None:
        df = pd.DataFrame()
    df = df.reset_index(drop=True) if hasattr(df, "reset_index") else df

    # DuckDB.register reddediyor 0-column DataFrame'leri — empty sonuçta
    # crash etmesin, view register'ı atlanır, downstream rows=[] ile devam eder.
    view_name = _block_view_name(block_id)
    if len(df.columns) > 0:
        register_dataframe(conn, view_name, df)
    else:
        log.info("duck.execute_block_sql: block=%s empty result (no columns), skip register", block_id)

    columns = [str(c) for c in df.columns]
    total_rows = int(len(df))

    all_rows = []
    if total_rows > 0:
        rows_for_manifest = df.head(_MAX_ROWS_IN_MANIFEST)
        all_rows = [
            [_jsonable(v) for v in row]
            for row in rows_for_manifest.itertuples(index=False, name=None)
        ]

    sample = all_rows[:sample_rows]

    engine_label = "duckdb" if is_duckdb_only else "oracle"
    log.info(
        "duck.execute_block_sql: block=%s engine=%s -> %d rows, view=%s",
        block_id, engine_label, total_rows, view_name,
    )

    return {
        "sql":          exec_sql,
        "original_sql": gate.original_sql,
        "rewritten":    gate.rewritten,
        "truncated":    gate.truncated,
        "cap":          gate.cap,
        "reason":       gate.reason,
        "executed_at":  _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "row_count":    total_rows,
        "columns":      columns,
        "preview_rows": sample,
        "rows":         all_rows,
        "view_name":    view_name,
        "engine":       engine_label,
    }


def drop_block_view(conn, block_id):
    try:
        conn.unregister(_block_view_name(block_id))
    except Exception:
        pass
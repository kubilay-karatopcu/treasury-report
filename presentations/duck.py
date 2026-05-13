"""
Oracle → Arrow → DuckDB bridge.

Adım 5 hotfix:
- `execute_block_sql` artık `data_source.rows` alanına TÜM satırları yazıyor.
  `preview_rows` ilk 5 satır olarak duruyor (modal UI için), `rows` ise tüm
  agreged sonuç (chart/tablo render'ı için).
- Aggregation gate zaten 5000 satırla sınırlandırdığı için boyut endişesi yok.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from typing import TYPE_CHECKING

from .aggregation_gate import GateError, GateResult, MAX_RAW_ROWS, validate_and_wrap

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# LEGACY BASKET PATH — unchanged from Adım 1
# ════════════════════════════════════════════════════════════════════════════

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
# BLOCK-DRIVEN PATH — Adım 1, Adım 5'te `rows` eklendi
# ════════════════════════════════════════════════════════════════════════════

def _block_view_name(block_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", block_id).lower()
    if not safe or safe[0].isdigit():
        safe = "b_" + safe
    return f"block_{safe}"


# Cap on `rows` size we persist into the manifest. The aggregation gate already
# limits Oracle output to MAX_RAW_ROWS (5000) — this is just a belt-and-braces.
_MAX_ROWS_IN_MANIFEST = MAX_RAW_ROWS


def execute_block_sql(dc, conn, block_id, sql, sample_rows=5):
    """Validate, execute, and register an Oracle SQL on behalf of one block.

    Returned shape (matches block.data_source on the manifest):
        {
            "sql":          str,        # the SQL that actually ran
            "original_sql": str,        # what the LLM produced (pre-gate)
            "rewritten":    bool,
            "truncated":    bool,
            "cap":          int,
            "reason":       str,
            "executed_at":  str,        # UTC ISO-8601
            "row_count":    int,
            "columns":      [str, ...],
            "preview_rows": [[v, ...]], # first N rows (modal display)
            "rows":         [[v, ...]], # ALL rows (chart/table render)   ← Adım 5
            "view_name":    str,
        }
    """
    import pandas as pd  # noqa

    gate: GateResult = validate_and_wrap(sql)

    log.info(
        "duck.execute_block_sql: block=%s gate=%s rewritten=%s truncated=%s cap=%d",
        block_id, gate.reason, gate.rewritten, gate.truncated, gate.cap,
    )

    df = dc.get_data(
        base_prefix=None,
        dataset=f"block::{block_id}",
        query=gate.sql,
        query_params={},
    )

    if df is None:
        df = pd.DataFrame()
    df = df.reset_index(drop=True) if hasattr(df, "reset_index") else df

    view_name = _block_view_name(block_id)
    register_dataframe(conn, view_name, df)

    columns = [str(c) for c in df.columns]
    total_rows = int(len(df))

    # All rows — JSON-safe, capped for safety.
    all_rows = []
    if total_rows > 0:
        rows_for_manifest = df.head(_MAX_ROWS_IN_MANIFEST)
        all_rows = [
            [_jsonable(v) for v in row]
            for row in rows_for_manifest.itertuples(index=False, name=None)
        ]

    # First N rows for the source modal preview.
    sample = all_rows[:sample_rows]

    log.info(
        "duck.execute_block_sql: block=%s -> %d rows, view=%s",
        block_id, total_rows, view_name,
    )

    return {
        "sql":          gate.sql,
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
    }


def drop_block_view(conn, block_id):
    try:
        conn.unregister(_block_view_name(block_id))
    except Exception:
        pass

"""
Oracle → Arrow → DuckDB bridge.

The DataClient (real or fake) returns pandas DataFrames. We register them as
DuckDB views so blocks can run lightweight SQL transformations without going
back to Oracle.

Phase 4 keeps SQL generation simple: each basket item becomes a `SELECT * FROM`
against the table, optionally with a `row_filter`. Pandas → DuckDB happens via
PyArrow zero-copy. Bigger production fetches will move to streaming Arrow once
the real DataClient supports it.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


log = logging.getLogger(__name__)


def _table_alias(table_id: str) -> str:
    """`EDW.DEPOSITS_DAILY` → `deposits_daily` (DuckDB view name)."""
    return table_id.split(".")[-1].lower()


def fetch_basket_item(dc, basket_item: dict) -> "pd.DataFrame":
    """Run the DataClient query for a single basket item.

    DataClient interface (matches the production class):
        dc.get_data(base_prefix=..., dataset=..., query=..., query_params=...)

    If the DataClient returns nothing or a column-less frame (e.g. the local
    FakeDataClient when no CSV mock exists), we synthesize a 0-row frame with
    the requested columns so DuckDB.register doesn't choke.
    """
    import pandas as pd

    table_id = basket_item["table"]
    columns = basket_item.get("columns") or ["*"]
    row_filter = basket_item.get("row_filter")

    cols_sql = ", ".join(columns) if columns != ["*"] else "*"
    where_sql = f" WHERE {row_filter}" if row_filter else ""
    sql = f"SELECT {cols_sql} FROM {table_id}{where_sql}"

    df = dc.get_data(
        base_prefix=None,
        dataset=table_id,
        query=sql,
        query_params={},
    )

    if df is None or len(df.columns) == 0:
        placeholder_cols = columns if columns != ["*"] else ["_no_data"]
        df = pd.DataFrame({c: pd.Series(dtype=object) for c in placeholder_cols})
        log.info("duck.fetch_basket_item: %s -> 0 rows (no mock data, placeholder schema)", table_id)
    else:
        log.info("duck.fetch_basket_item: %s -> %d rows", table_id, len(df))

    return df


def register_dataframe(conn: "duckdb.DuckDBPyConnection", view_name: str, df: "pd.DataFrame") -> None:
    """Register a DataFrame as a DuckDB view, replacing if exists."""
    # `register` needs the DataFrame to outlive the connection; for our short-lived
    # session use case this is fine.
    try:
        conn.unregister(view_name)
    except Exception:
        pass
    conn.register(view_name, df)
    log.debug("duck.register_dataframe: %s (%d rows)", view_name, len(df))


def populate_basket(dc, conn: "duckdb.DuckDBPyConnection", basket: list[dict]) -> dict:
    """Fetch every basket item via the DataClient and register it in DuckDB.

    Returns a manifest of what was loaded:
        {"deposits_daily": {"table": "EDW.DEPOSITS_DAILY", "rows": 12345}, ...}
    """
    loaded = {}
    for item in basket:
        view = _table_alias(item["table"])
        df = fetch_basket_item(dc, item)
        register_dataframe(conn, view, df)
        loaded[view] = {"table": item["table"], "rows": len(df)}
    return loaded


def list_views(conn: "duckdb.DuckDBPyConnection") -> list[str]:
    """Return the names of registered views/tables in the DuckDB session."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
    ).fetchall()
    return [r[0] for r in rows]


def preview_view(conn: "duckdb.DuckDBPyConnection", view_name: str, limit: int = 10) -> dict:
    """Return a small preview of a view: columns + first N rows.

    Output shape:
        {"columns": ["A", "B"], "rows": [[1, "x"], [2, "y"]], "row_count": 2}
    """
    safe_name = _safe_identifier(view_name)
    df = conn.execute(f"SELECT * FROM {safe_name} LIMIT {int(limit)}").fetchdf()
    total = conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
    return {
        "columns": list(df.columns),
        "rows": df.values.tolist(),
        "row_count": int(total),
    }


def _safe_identifier(name: str) -> str:
    """Reject anything that's not a plain identifier — guards against SQL injection
    on routes like /duckdb/preview/<view>."""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe view name: {name!r}")
    return name


def summarize_views(
    conn: "duckdb.DuckDBPyConnection",
    view_names: list[str],
    sample_rows: int = 5,
) -> dict:
    """Return a compact schema + sample for each view — fed into the LLM prompt
    so it can compute realistic KPI values and chart series from real data.

    Output shape (per view):
        {
            "columns":    [{"name": "X", "type": "VARCHAR"}, ...],
            "row_count":  int,
            "sample":     [[v1, v2, ...], ...],   # column-aligned rows
            "stats":      {"NUM_COL": {"min":..., "max":..., "avg":..., "argmax":...}}
        }
    `argmax` is the row dict where the numeric column reached its max — very
    handy for "show the top branch" KPI prompts.
    """
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

            out[view] = {
                "columns":   col_list,
                "row_count": row_count,
                "sample":    sample,
                "stats":     stats,
            }
        except Exception as exc:
            log.warning("summarize_views: failed for %s: %s", view, exc)
    return out


_NUMERIC_TYPES = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")


def _compute_stats(conn, safe_view: str, col_list: list[dict], row_count: int) -> dict:
    """min/max/avg + argmax for each numeric column. Skips empty tables."""
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
                "min": _jsonable(mn),
                "max": _jsonable(mx),
                "avg": _jsonable(av),
                "argmax": argmax,
            }
        except Exception as exc:
            log.debug("_compute_stats: skipped %s.%s: %s", safe_view, col, exc)
    return stats


def _jsonable(v):
    """Coerce numpy/pandas/datetime scalars to JSON-safe Python primitives."""
    import math
    if v is None:
        return None
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) else v
    # numpy/pandas: rely on .item()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return str(v)

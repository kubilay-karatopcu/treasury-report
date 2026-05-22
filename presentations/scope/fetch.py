"""Cached-table fetch for the Hazırlık "Sunum'a geç" flow (spec §3.2, §8.b).

For each ``cached`` basket table this composes a projected Oracle SELECT, pulls
it through the existing DataClient, and materialises the result as a DuckDB view
named after the table's scope alias. Block queries in Sunum then reference that
alias as a normal view (the existing Phase 6.5 path).

Scope of 8.b (deliberately narrow):
- **Projection** is applied (only the scoped columns are pulled).
- A **single partition-date-range pushdown** is applied when a pinned
  ``between`` filter targets the concept bound to the table's partition column —
  this keeps the cached footprint in line with the routing estimate. It uses
  the partition column directly (no concept→column compiler), with parameterised
  binds (never string-concatenated values).
- **Lazy tables are skipped** here (their Oracle path is 8.d).
- **Full pinned-filter pushdown via the Phase 7 compiler is 8.d**
  (``§8.d — Pinned filter pushdown for cached tables``). Non-date pinned filters
  are still enforced at block-execution time by the variable resolver (8.a).

``fetch_cached_tables`` performs no Oracle call for a table the catalog can't
size/locate beyond a plain projected SELECT; it never raises for a missing
catalog entry.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from presentations.duck import register_dataframe
from presentations.scope.catalog import Catalog
from presentations.scope.schema import BasketItem, ScopeContract

log = logging.getLogger(__name__)


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def _partition_pushdown(
    scope: ScopeContract, item: BasketItem, catalog: Catalog | None,
) -> tuple[str, dict[str, Any]]:
    """Return (where_clause, binds) for a pinned date range on the partition
    column, or ("", {}) when not applicable."""
    if catalog is None:
        return "", {}
    tm = catalog.table_meta(item.table_ref.schema_name, item.table_ref.name)
    if tm is None or not tm.partition_column:
        return "", {}
    part_concept = tm.column_concept(tm.partition_column)
    if not part_concept:
        return "", {}
    for pf in scope.pinned_filters_for_alias(item.alias):
        if pf.op != "between" or pf.concept != part_concept:
            continue
        lo, hi = _as_date(pf.from_), _as_date(pf.to)
        if lo is None or hi is None:
            continue
        lo_bind, hi_bind = f"{item.alias}_from", f"{item.alias}_to"
        where = f"{tm.partition_column} BETWEEN :{lo_bind} AND :{hi_bind}"
        return where, {lo_bind: lo, hi_bind: hi}
    return "", {}


def compose_cached_sql(
    scope: ScopeContract, item: BasketItem, catalog: Catalog | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compose the projected Oracle SELECT (+ optional partition pushdown) for a
    cached basket table. Returns (sql, binds)."""
    proj = item.projection
    cols = "*" if (proj.include_all or not proj.columns) else ", ".join(proj.columns)
    table = f"{item.table_ref.schema_name}.{item.table_ref.name}"
    where, binds = _partition_pushdown(scope, item, catalog)
    where_sql = f" WHERE {where}" if where else ""
    return f"SELECT {cols} FROM {table}{where_sql}", binds


def fetch_cached_tables(
    dc, conn, scope: ScopeContract, *, catalog: Catalog | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch every ``cached`` basket table into a DuckDB view named by alias.

    Returns ``{alias: {"table": "<schema.name>", "rows": <n>}}``. Lazy tables
    are skipped (8.d). Raises on Oracle / DuckDB errors so the caller can mark
    ``status.state = failed`` with the message.
    """
    import pandas as pd

    loaded: dict[str, dict[str, Any]] = {}
    for item in scope.basket:
        if item.routing.decision != "cached":
            continue
        sql, binds = compose_cached_sql(scope, item, catalog)
        df = dc.get_data(
            base_prefix=None,
            dataset=f"scope::{scope.presentation_id}/{item.alias}",
            query=sql,
            query_params=binds,
        )
        if df is None:
            df = pd.DataFrame()
        if len(df.columns) > 0:
            register_dataframe(conn, item.alias, df)
        loaded[item.alias] = {
            "table": f"{item.table_ref.schema_name}.{item.table_ref.name}",
            "rows": int(len(df)),
        }
        log.info(
            "scope.fetch_cached_tables: %s ← %s (%d rows)",
            item.alias, f"{item.table_ref.schema_name}.{item.table_ref.name}", len(df),
        )
    return loaded

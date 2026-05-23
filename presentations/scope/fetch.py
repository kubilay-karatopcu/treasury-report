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
    if catalog is None or item.table_ref is None:
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


def _raw_predicates(scope: ScopeContract, item: BasketItem) -> tuple[list[str], dict[str, Any]]:
    """Fetch-time WHERE clauses for the alias's raw (non-concept) filters (§6R.4).
    Parameterised binds only — values are never concatenated into SQL."""
    clauses: list[str] = []
    binds: dict[str, Any] = {}
    for i, rf in enumerate(scope.raw_filters_for_alias(item.alias)):
        col = rf.column
        if rf.op in ("in", "not_in") and rf.values:
            names = []
            for j, v in enumerate(rf.values):
                b = f"{item.alias}_rf{i}_{j}"
                binds[b] = v
                names.append(f":{b}")
            op = "IN" if rf.op == "in" else "NOT IN"
            clauses.append(f"{col} {op} ({', '.join(names)})")
        elif rf.op == "between" and rf.from_ is not None and rf.to is not None:
            bf, bt = f"{item.alias}_rf{i}_f", f"{item.alias}_rf{i}_t"
            binds[bf] = _as_date(rf.from_) or rf.from_
            binds[bt] = _as_date(rf.to) or rf.to
            clauses.append(f"{col} BETWEEN :{bf} AND :{bt}")
        elif rf.op == "eq" and rf.value is not None:
            b = f"{item.alias}_rf{i}"
            binds[b] = rf.value
            clauses.append(f"{col} = :{b}")
    return clauses, binds


def _concept_pushdown(
    scope: ScopeContract, item: BasketItem,
    registry, binding_catalog,
) -> tuple[list[str], dict[str, Any]]:
    """Pinned concept-filter pushdown via the Phase 7 compiler (§3.2, 8.d).

    Compiles every pinned filter targeting this alias's concepts into Oracle
    WHERE clauses with parameterised binds. Concept-blind filters (where the
    table has no binding for the concept) are silently dropped — the variable
    resolver still enforces them at block execution time.

    Returns ``(clauses, binds)`` ready to merge into the SELECT.
    """
    if registry is None or binding_catalog is None:
        return [], {}
    from presentations.concepts.compiler import (
        ResolvedFilter,
        compile_filter_for_table,
    )

    schema = item.table_ref.schema_name
    name = item.table_ref.name
    clauses: list[str] = []
    binds: dict[str, Any] = {}
    partition_concept = None
    if hasattr(binding_catalog, "concept_bound_to_column"):
        partition_concept = None  # binding_catalog doesn't have this — derive below
    # Skip the partition date filter — already pushed via _partition_pushdown.

    for pf in scope.pinned_filters_for_alias(item.alias):
        # The variable resolver still handles last_n_days, so skip it here
        # (would require runtime "today" expression — out of scope for fetch).
        if pf.op == "last_n_days":
            continue

        # Convert Pydantic op → Phase 7 operator + values shape.
        if pf.op == "between":
            # Already covered by _partition_pushdown when this concept binds
            # to the partition column. Compile anyway — concept-blind cases
            # produce empty predicates, and non-partition between filters
            # (e.g. tenor range bucket) still get pushed.
            values: list = [pf.from_, pf.to]
            op = "between"
        elif pf.op in ("in", "not_in"):
            # not_in isn't a Phase 7 operator; compile as 'in' and let the
            # caller's NOT IN handling stay at variable-resolver layer.
            if pf.op == "not_in":
                continue
            values = list(pf.values or [])
            op = "in"
        elif pf.op == "eq":
            values = [pf.value]
            op = "eq"
        else:
            continue

        rf = ResolvedFilter(
            concept=pf.concept,
            operator=op,
            values=values,
            filter_id=pf.id,
        )
        try:
            compiled = compile_filter_for_table(
                rf, schema, name, registry, binding_catalog,
            )
        except Exception:
            log.warning("scope.fetch: compile failed for %s on %s.%s",
                        pf.id, schema, name, exc_info=True)
            continue
        if compiled.blind or not compiled.sql:
            continue
        clauses.append(compiled.sql)
        binds.update(compiled.params or {})
    return clauses, binds


def compose_cached_sql(
    scope: ScopeContract, item: BasketItem, catalog: Catalog | None = None,
    *,
    concept_registry=None, binding_catalog=None,
) -> tuple[str, dict[str, Any]]:
    """Compose the projected Oracle SELECT for a cached basket table, shrinking
    it with fetch-time WHERE clauses:

    1. Partition date-range pushdown (`_partition_pushdown`) when a pinned
       ``between`` targets the partition column's concept.
    2. Pinned concept-filter pushdown via the Phase 7 compiler — non-date
       pinned filters (currency / segment / branch …) are emitted as
       parameterised WHERE clauses against the table's bound columns.
    3. Raw (non-concept) per-alias filters (`_raw_predicates`, §6R.4).

    Returns ``(sql, binds)``. Concept-blind pinned filters are silently
    dropped here — the variable resolver still enforces them at block exec.
    """
    proj = item.projection
    cols = "*" if (proj.include_all or not proj.columns) else ", ".join(proj.columns)
    table = f"{item.table_ref.schema_name}.{item.table_ref.name}"

    where_parts: list[str] = []
    binds: dict[str, Any] = {}
    pw, pb = _partition_pushdown(scope, item, catalog)
    if pw:
        where_parts.append(pw)
        binds.update(pb)
    cw, cb = _concept_pushdown(scope, item, concept_registry, binding_catalog)
    where_parts.extend(cw)
    binds.update(cb)
    rw, rb = _raw_predicates(scope, item)
    where_parts.extend(rw)
    binds.update(rb)

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    return f"SELECT {cols} FROM {table}{where_sql}", binds


_AGG = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}


def compile_aggregate_sql(item: BasketItem) -> str:
    """Generate the GROUP BY SQL for a derived (aggregate) basket item (§6R).

    Runs against the *source alias* (a DuckDB view materialised earlier), so the
    aggregate is over the already-scoped source. The pivot UI and the LLM both
    produce the ``derivation`` definition; this is the single place SQL is
    emitted from it.
    """
    d = item.derivation
    selects = list(d.group_by)
    for m in d.measures:
        if m.fn == "count_distinct":
            selects.append(f"COUNT(DISTINCT {m.column}) AS {m.as_}")
        else:
            selects.append(f"{_AGG[m.fn]}({m.column}) AS {m.as_}")
    sel = ", ".join(selects) if selects else "*"
    group = (" GROUP BY " + ", ".join(d.group_by)) if d.group_by else ""
    return f"SELECT {sel} FROM {d.source_alias}{group}"


def fetch_cached_tables(
    dc, conn, scope: ScopeContract, *,
    catalog: Catalog | None = None,
    concept_registry=None, binding_catalog=None,
) -> dict[str, dict[str, Any]]:
    """Materialise the scope into DuckDB views named by alias.

    Two passes: (1) raw ``cached`` tables → projected Oracle SELECT → view;
    (2) derived aggregate tables → GROUP BY run *on DuckDB* over the
    already-materialised source view. Lazy tables are skipped (8.d). Raises on
    Oracle / DuckDB errors so the caller can mark ``status.state = failed``.

    Returns ``{alias: {...}}``.
    """
    import pandas as pd

    loaded: dict[str, dict[str, Any]] = {}

    # Pass 1 — raw cached tables.
    for item in scope.basket:
        if item.derivation is not None or item.table_ref is None:
            continue
        if item.routing.decision != "cached":
            continue
        sql, binds = compose_cached_sql(
            scope, item, catalog,
            concept_registry=concept_registry,
            binding_catalog=binding_catalog,
        )
        df = dc.get_data(
            base_prefix=None,
            dataset=f"scope::{scope.presentation_id}/{item.alias}",
            query=sql, query_params=binds,
        )
        if df is None:
            df = pd.DataFrame()
        if len(df.columns) > 0:
            register_dataframe(conn, item.alias, df)
        loaded[item.alias] = {
            "table": f"{item.table_ref.schema_name}.{item.table_ref.name}",
            "rows": int(len(df)),
        }
        log.info("scope.fetch_cached_tables: %s ← %s (%d rows)",
                 item.alias, f"{item.table_ref.schema_name}.{item.table_ref.name}", len(df))

    # Pass 2 — derived (aggregate) tables, computed on DuckDB over the source.
    for item in scope.derived_items():
        sql = compile_aggregate_sql(item)
        try:
            df = conn.execute(sql).fetchdf()
        except Exception as exc:
            raise RuntimeError(
                f"derived table '{item.alias}' failed ({sql!r}): {exc}"
            ) from exc
        if len(df.columns) > 0:
            register_dataframe(conn, item.alias, df)
        loaded[item.alias] = {"derived_from": item.derivation.source_alias, "rows": int(len(df))}
        log.info("scope.fetch_cached_tables: %s ⇐ aggregate of %s (%d rows)",
                 item.alias, item.derivation.source_alias, len(df))

    return loaded

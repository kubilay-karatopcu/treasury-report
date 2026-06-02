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


# Hard safety cap for an on-demand *lazy* fetch (a table routed lazy is, by
# definition, too big to fully cache). It bounds the rows pulled into pandas /
# DuckDB when pinned filters don't narrow the table enough — turning a potential
# pod OOM into a logged truncation. Cached tables are NOT capped: routing already
# guarantees they sit under the byte threshold.
SCOPE_FETCH_ROW_CAP = 5_000_000


def _as_date(v: Any) -> date | None:
    """Resolve a filter date value to a concrete date. Accepts date objects,
    ISO strings, and the relative grammar (``today``, ``today - 30d``,
    ``start_of_month`` …) via parse_date_expr — so a relative range stays
    dynamic, re-resolving to the run date on every fetch (spec §3.3). Non-date
    values → None."""
    if isinstance(v, date):
        return v
    try:
        from presentations.variables.resolver import parse_date_expr
        return parse_date_expr(v)
    except Exception:
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
        elif rf.op in ("gt", "gte", "lt", "lte") and rf.value is not None:
            sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[rf.op]
            b = f"{item.alias}_rf{i}"
            binds[b] = rf.value
            clauses.append(f"{col} {sym} :{b}")
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
    max_rows: int | None = None,
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
    # Optional safety cap (used by the lazy on-demand path) — bounds the pull
    # when the byte estimate is wrong/absent or pinned filters don't narrow
    # enough. Cached fetches pass max_rows=None (routing keeps them small).
    cap_sql = f" FETCH FIRST {int(max_rows)} ROWS ONLY" if max_rows else ""
    return f"SELECT {cols} FROM {table}{where_sql}{cap_sql}", binds


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


def compile_calculated_sql(item: BasketItem) -> str:
    """Generate the JOIN + SELECT SQL for a derived (calculated) basket item.

    Single source: ``SELECT expr AS name, … FROM src``.
    Multi-source : ``FROM src0 INNER JOIN src1 ON src0.k = src1.k …`` with
                   join_keys chained in order. Output columns are emitted
                   verbatim — the schema layer already ensured the column
                   names are unique and the expressions are bounded in size.

    Runs against the already-materialised source views (raw or aggregate),
    so ``fetch_cached_tables`` must process this derivation type *after*
    its sources are loaded — the dependency ordering is enforced by the
    fetch pass.
    """
    d = item.derivation
    if len(d.source_aliases) == 1:
        from_clause = d.source_aliases[0]
    else:
        # First alias is the FROM root; subsequent aliases come in via
        # INNER JOIN clauses. We trust the validator to have rejected
        # multi-source derivations missing join_keys.
        from_clause = d.source_aliases[0]
        joined: set[str] = {d.source_aliases[0]}
        for jk in d.join_keys:
            # Pick whichever side is not yet joined — gives a stable order
            # regardless of how the user expressed the keys.
            if jk.right_alias in joined and jk.left_alias not in joined:
                add_alias, left, right = jk.left_alias, jk.right_alias, jk.left_alias
                left_col, right_col = jk.right_column, jk.left_column
            else:
                add_alias = jk.right_alias if jk.right_alias not in joined else jk.left_alias
                left, right = jk.left_alias, jk.right_alias
                left_col, right_col = jk.left_column, jk.right_column
            from_clause += (
                f' INNER JOIN "{add_alias}" '
                f'ON "{left}"."{left_col}" = "{right}"."{right_col}"'
            )
            joined.add(add_alias)
    select_list = ", ".join(f"{c.expr} AS \"{c.name}\"" for c in d.columns)
    return f"SELECT {select_list} FROM {from_clause}"


def fetch_cached_tables(
    dc, conn, scope: ScopeContract, *,
    catalog: Catalog | None = None,
    concept_registry=None, binding_catalog=None,
    refetch_only: set[str] | None = None,
    drop_aliases: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Materialise the scope into DuckDB views named by alias.

    Two passes: (1) raw ``cached`` tables → projected Oracle SELECT → view;
    (2) derived aggregate tables → GROUP BY run *on DuckDB* over the
    already-materialised source view. Lazy tables are skipped (8.d). Raises on
    Oracle / DuckDB errors so the caller can mark ``status.state = failed``.

    Re-entry partial refresh (Phase 8.e, spec §3.6 step c):

      - ``drop_aliases``: views from the previous scope_v<N> that no longer
        exist in the new scope (or whose data must be invalidated) — these
        get ``DROP VIEW`` before the fetch pass.
      - ``refetch_only``: when supplied, only these aliases are re-fetched
        from Oracle. Other cached aliases are assumed to already have a
        DuckDB view registered by a previous build pass; we leave them
        untouched. Derived aggregate items always re-run if their source
        alias is in ``refetch_only`` (their result depends on it).

    When both are ``None`` the function behaves like the original first-build
    flow: every cached basket item is fetched and every derived item runs.

    Returns ``{alias: {...}}`` for the aliases actually touched in this call
    (does not include aliases that were left intact across re-entry).
    """
    import pandas as pd

    loaded: dict[str, dict[str, Any]] = {}

    # Step 0 — drop views for aliases that are gone in the new scope. Failure
    # is non-fatal: a view may already be gone from a pod restart.
    for alias in (drop_aliases or ()):
        try:
            conn.execute(f'DROP VIEW IF EXISTS "{alias}"')
            log.info("scope.fetch_cached_tables: dropped stale view '%s'", alias)
        except Exception:
            log.warning("scope.fetch_cached_tables: drop of '%s' failed",
                        alias, exc_info=True)

    # Pass 1 — raw cached tables.
    for item in scope.basket:
        if item.derivation is not None or item.table_ref is None:
            continue
        if item.routing.decision != "cached":
            continue
        if refetch_only is not None and item.alias not in refetch_only:
            # Re-entry partial refresh — view from scope_v<N-1> is reused.
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

    # Pass 2 — derived tables (aggregate + calculated), computed on DuckDB
    # over already-materialised source views. In partial-refresh mode we only
    # re-run when any source alias of the derivation was re-fetched.
    for item in scope.derived_items():
        d = item.derivation
        # Set of source aliases this derivation depends on.
        if d.kind == "aggregate":
            deps = {d.source_alias} if d.source_alias else set()
            sql = compile_aggregate_sql(item)
            kind_label = "aggregate"
            derived_from_value = d.source_alias
        else:                         # calculated
            deps = set(d.source_aliases)
            sql = compile_calculated_sql(item)
            kind_label = "calculated"
            derived_from_value = list(d.source_aliases)
        # Partial-refresh gate: any source alias touched → re-run.
        if refetch_only is not None and not (deps & refetch_only):
            continue
        try:
            df = conn.execute(sql).fetchdf()
        except Exception as exc:
            raise RuntimeError(
                f"derived table '{item.alias}' failed ({sql!r}): {exc}"
            ) from exc
        if len(df.columns) > 0:
            register_dataframe(conn, item.alias, df)
        loaded[item.alias] = {"derived_from": derived_from_value, "rows": int(len(df))}
        log.info("scope.fetch_cached_tables: %s ⇐ %s of %s (%d rows)",
                 item.alias, kind_label, derived_from_value, len(df))

    return loaded

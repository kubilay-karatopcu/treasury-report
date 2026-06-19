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


def _cached_row_guard(item: BasketItem, catalog: Catalog | None) -> int:
    """Emniyet tavanı (satır) — cached bir pull'un boyut tahmininin ÇOK üstüne
    kaçtığını yakalar (bayat istatistik / yanlış doc). Tahmine 3× tolerans
    tanınır; taban ``SCOPE_FETCH_ROW_CAP``. Aşım sessizce KIRPILMAZ — kırpmak
    blok verisini bozar — fetch hata verir ve build kullanıcıya "filtrele ya da
    lazy yap" der."""
    bpr = 50
    if catalog is not None and item.table_ref is not None:
        tm = catalog.table_meta(item.table_ref.schema_name, item.table_ref.name)
        if tm is not None:
            from presentations.scope.routing import _bytes_per_row
            bpr = _bytes_per_row(tm, item.projection)
    elif item.sql is not None and item.projection and item.projection.columns:
        bpr = max(50, 16 * len(item.projection.columns))
    est_rows = int((item.routing.estimated_bytes or 0) / max(1, bpr))
    return max(SCOPE_FETCH_ROW_CAP, est_rows * 3)


def _guard_overflow(alias: str, df, guard: int) -> None:
    if df is not None and len(df) > guard:
        raise RuntimeError(
            f"'{alias}' beklenenden çok daha büyük çıktı (≥{guard:,} satır) — "
            "boyut tahmini yanılmış olabilir. Tabloyu filtreleyip küçült ya da "
            "lazy olarak işaretle."
        )


def duck_source_aliases(scope: ScopeContract, item: BasketItem) -> set[str]:
    """Source aliases that must exist as a DuckDB VIEW before ``item``'s
    derivation can run. A filter on an ORACLE main needs no view (the filter
    re-queries Oracle directly); everything else consumes its sources as views.
    Shared by the fetch pass ordering and the inactive-alias skip below."""
    d = item.derivation
    if d is None:
        return set()
    if d.kind in ("aggregate", "python"):
        # python: tek source_alias `input_node_df` olarak verilir → view gerekir.
        srcs = [d.source_alias] if d.source_alias else []
    elif d.kind in ("calculated", "join", "union"):
        srcs = list(d.source_aliases)
    else:  # filter — only a DERIVED source needs a view; Oracle source hits Oracle
        src = scope.basket_item(d.source_alias)
        srcs = [d.source_alias] if (src is not None and src.table_ref is None) else []
    return set(srcs)


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
    if item.table_ref is None:
        return "", {}
    return _partition_pushdown_from(
        item.table_ref, list(scope.pinned_filters_for_alias(item.alias)),
        catalog, item.alias,
    )


def _partition_pushdown_from(
    table_ref, pinned, catalog: Catalog | None, prefix: str,
) -> tuple[str, dict[str, Any]]:
    """List-based partition pushdown — shared by the cached-table fetch and the
    filter-derivation compiler (Faz R1)."""
    if catalog is None or table_ref is None:
        return "", {}
    tm = catalog.table_meta(table_ref.schema_name, table_ref.name)
    if tm is None or not tm.partition_column:
        return "", {}
    part_concept = tm.column_concept(tm.partition_column)
    if not part_concept:
        return "", {}
    for pf in pinned:
        if pf.op != "between" or pf.concept != part_concept:
            continue
        lo, hi = _as_date(pf.from_), _as_date(pf.to)
        if lo is None or hi is None:
            continue
        lo_bind, hi_bind = f"{prefix}_from", f"{prefix}_to"
        where = f"{tm.partition_column} BETWEEN :{lo_bind} AND :{hi_bind}"
        return where, {lo_bind: lo, hi_bind: hi}
    return "", {}


def _raw_predicates(scope: ScopeContract, item: BasketItem) -> tuple[list[str], dict[str, Any]]:
    """Fetch-time WHERE clauses for the alias's raw (non-concept) filters (§6R.4)."""
    return _raw_predicates_from(scope.raw_filters_for_alias(item.alias), item.alias)


def _raw_predicates_from(raw_filters, prefix: str) -> tuple[list[str], dict[str, Any]]:
    """Compile a list of raw (column-level) filters into WHERE clauses + binds.
    Parameterised binds only — values are never concatenated into SQL. `prefix`
    namespaces the bind variables. Shared by the cached-table fetch (old path)
    and the filter-derivation compiler (Faz R1)."""
    clauses: list[str] = []
    binds: dict[str, Any] = {}
    for i, rf in enumerate(raw_filters):
        col = rf.column
        if rf.op in ("in", "not_in") and rf.values:
            names = []
            for j, v in enumerate(rf.values):
                b = f"{prefix}_rf{i}_{j}"
                binds[b] = v
                names.append(f":{b}")
            op = "IN" if rf.op == "in" else "NOT IN"
            clauses.append(f"{col} {op} ({', '.join(names)})")
        elif rf.op == "between" and rf.from_ is not None and rf.to is not None:
            bf, bt = f"{prefix}_rf{i}_f", f"{prefix}_rf{i}_t"
            binds[bf] = _as_date(rf.from_) or rf.from_
            binds[bt] = _as_date(rf.to) or rf.to
            clauses.append(f"{col} BETWEEN :{bf} AND :{bt}")
        elif rf.op == "eq" and rf.value is not None:
            b = f"{prefix}_rf{i}"
            binds[b] = rf.value
            clauses.append(f"{col} = :{b}")
        elif rf.op in ("gt", "gte", "lt", "lte") and rf.value is not None:
            sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[rf.op]
            b = f"{prefix}_rf{i}"
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
    if item.table_ref is None:
        return [], {}
    return _concept_pushdown_from(
        item.table_ref, list(scope.pinned_filters_for_alias(item.alias)),
        registry, binding_catalog,
    )


def _concept_pushdown_from(
    table_ref, pinned, registry, binding_catalog,
) -> tuple[list[str], dict[str, Any]]:
    """List-based pinned concept-filter pushdown via the Phase 7 compiler —
    shared by the cached-table fetch and the filter-derivation compiler."""
    if registry is None or binding_catalog is None:
        return [], {}
    from presentations.concepts.compiler import (
        ResolvedFilter,
        compile_filter_for_table,
    )

    schema = table_ref.schema_name
    name = table_ref.name
    clauses: list[str] = []
    binds: dict[str, Any] = {}

    # Relative-date resolver: a pinned date concept can carry "today - 7d" /
    # "today" — resolve to a concrete date BEFORE the compiler binds it, else the
    # raw string lands as a bind value and Oracle compares a DATE column to text
    # → no rows (the empty-filter-node bug). Non-date strings (e.g. "TRY") fail
    # to parse → kept verbatim.
    def _resolve_rel(v):
        if isinstance(v, str):
            d = _as_date(v)
            if d is not None:
                return d
        return v

    for pf in pinned:
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
            values: list = [_resolve_rel(pf.from_), _resolve_rel(pf.to)]
            op = "between"
        elif pf.op in ("in", "not_in"):
            # not_in isn't a Phase 7 operator; compile as 'in' and let the
            # caller's NOT IN handling stay at variable-resolver layer.
            if pf.op == "not_in":
                continue
            values = [_resolve_rel(x) for x in (pf.values or [])]
            op = "in"
        elif pf.op == "eq":
            values = [_resolve_rel(pf.value)]
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


def compile_filter_sql(
    scope: ScopeContract, item: BasketItem, catalog: Catalog | None = None,
    *,
    concept_registry=None, binding_catalog=None,
    max_rows: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Faz R1 — compose the Oracle SELECT for a ``filter`` derivation node.

    A filtered node re-queries its source main table (lazy Oracle table) with
    the derivation's EMBEDDED filters: ``SELECT <proj> FROM <source table> WHERE
    <filters>``. The source's ``table_ref`` + projection are inherited via
    ``derivation.source_alias``. Relative dates resolve at call time, so the
    materialised dataset is dynamic (a ``today - 7d`` filter re-shifts daily).

    Predicate composition mirrors ``compose_cached_sql`` but reads from the
    embedded ``derivation.filters`` instead of scope-level filters. Bind names
    are namespaced by the node's alias. Raises when the source can't be resolved.
    """
    d = item.derivation
    if d is None or d.kind != "filter":
        raise ValueError(f"compile_filter_sql: '{item.alias}' is not a filter node")
    source = scope.basket_item(d.source_alias)
    if source is None:
        raise ValueError(
            f"filter node '{item.alias}': source '{d.source_alias}' not in basket"
        )

    pinned = list(d.filters.pinned) if d.filters else []
    raw = list(d.filters.raw) if d.filters else []

    # ── Derived source (Faz A — zincirleme): the source is a DuckDB view
    # (aggregate/calculated/another filter). Filter it IN DuckDB. No table_ref →
    # no partition/concept pushdown (a view has no table-doc bindings); raw
    # column predicates only. DuckDB uses $name params, so swap the ':' the
    # Oracle composer emits → '$'. (The frontend produces raw filters when the
    # user filters a derived node.)
    if source.table_ref is None:
        rclauses, binds = _raw_predicates_from(raw, item.alias)
        duck_clauses = [c.replace(":", "$") for c in rclauses]
        where_sql = (" WHERE " + " AND ".join(duck_clauses)) if duck_clauses else ""
        cap_sql = f" LIMIT {int(max_rows)}" if max_rows else ""
        return f'SELECT * FROM "{d.source_alias}"{where_sql}{cap_sql}', binds

    # ── Oracle source (lazy main table): re-query Oracle with the filters.
    table_ref = source.table_ref
    table = f"{table_ref.schema_name}.{table_ref.name}"
    proj = item.projection if (item.projection and item.projection.columns) else source.projection
    cols = "*" if (proj.include_all or not proj.columns) else ", ".join(proj.columns)

    where_parts: list[str] = []
    binds: dict[str, Any] = {}
    pw, pb = _partition_pushdown_from(table_ref, pinned, catalog, item.alias)
    if pw:
        where_parts.append(pw)
        binds.update(pb)
    cw, cb = _concept_pushdown_from(table_ref, pinned, concept_registry, binding_catalog)
    where_parts.extend(cw)
    binds.update(cb)
    rw, rb = _raw_predicates_from(raw, item.alias)
    where_parts.extend(rw)
    binds.update(rb)

    where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
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
    # Kimlikler quote'lanır: şema zaten identifier-dışını reddediyor ama quote
    # (a) keyword kolon adlarını ("ORDER" gibi) çalışır kılar, (b) DuckDB'de
    # exact-case eşleşme sağlar (view kolonları DataFrame'in birebir adlarıdır).
    selects = [f'"{g}"' for g in d.group_by]
    for m in d.measures:
        if m.fn == "count_distinct":
            selects.append(f'COUNT(DISTINCT "{m.column}") AS "{m.as_}"')
        else:
            selects.append(f'{_AGG[m.fn]}("{m.column}") AS "{m.as_}"')
    sel = ", ".join(selects) if selects else "*"
    # Guard: measures-with-no-group_by emits `SELECT col, SUM(...) FROM t` with
    # NO GROUP BY → DuckDB binder error ("column must appear in the GROUP BY
    # clause"). Fail loud with an actionable message instead of shipping broken
    # SQL (the LLM occasionally proposes this; the right fix is a python_node).
    if d.measures and not d.group_by:
        raise ValueError(
            f"aggregate '{item.alias}': measures var ama group_by boş — "
            "GROUP BY'sız SUM/AVG geçersiz. Bu dönüşüm için python_node kullan."
        )
    group = (" GROUP BY " + ", ".join(f'"{g}"' for g in d.group_by)) if d.group_by else ""
    return f'SELECT {sel} FROM "{d.source_alias}"{group}'


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
        from_clause = f'"{d.source_aliases[0]}"'
    else:
        # First alias is the FROM root; subsequent aliases come in via INNER
        # JOIN clauses. We trust the validator to have rejected multi-source
        # derivations missing join_keys.
        #
        # Add ONE table at a time and fold EVERY key that links the new table to
        # an already-joined table into a single `ON ... AND ...` clause. Emitting
        # one JOIN per key (the old behaviour) re-joined the same table when a
        # join used multiple columns (e.g. date + currency) → DuckDB "Ambiguous
        # reference to table … duplicate alias".
        from_clause = f'"{d.source_aliases[0]}"'
        joined: set[str] = {d.source_aliases[0]}
        remaining = list(d.join_keys)
        while remaining:
            nxt = None
            for jk in remaining:
                if jk.left_alias in joined and jk.right_alias not in joined:
                    nxt = jk.right_alias
                    break
                if jk.right_alias in joined and jk.left_alias not in joined:
                    nxt = jk.left_alias
                    break
            if nxt is None:
                break   # remaining keys link already-joined tables (cycle) — skip
            conds, rest = [], []
            for jk in remaining:
                if ((jk.left_alias == nxt and jk.right_alias in joined)
                        or (jk.right_alias == nxt and jk.left_alias in joined)):
                    conds.append(
                        f'"{jk.left_alias}"."{jk.left_column}" = '
                        f'"{jk.right_alias}"."{jk.right_column}"'
                    )
                else:
                    rest.append(jk)
            from_clause += f' INNER JOIN "{nxt}" ON ' + " AND ".join(conds)
            joined.add(nxt)
            remaining = rest
    select_list = ", ".join(f"{c.expr} AS \"{c.name}\"" for c in d.columns)
    return f"SELECT {select_list} FROM {from_clause}"


def compile_join_sql(item: BasketItem, left_cols, right_cols) -> str:
    """JOIN of exactly two source aliases on one OR MORE keys (Hazırlık ER node).

    Output = all LEFT columns, then all RIGHT columns; a right-side name that
    collides with a left name is prefixed with the right alias
    (``competitor_BRANCH_CODE``) so the materialised view has unique columns.
    Runs on the already-materialised DuckDB views (like calculated), so the
    fetch pass must register both sources first.

    Multi-column joins (e.g. date + currency) AND every ``join_key`` into the
    ON clause — the schema guarantees ≥1 key, all between the two aliases.
    """
    d = item.derivation
    left, right = d.source_aliases[0], d.source_aliases[1]
    jtype = "LEFT JOIN" if d.join_type == "left" else "INNER JOIN"
    left_set = set(left_cols or [])
    sel = [f'"{left}"."{c}" AS "{c}"' for c in (left_cols or [])]
    for c in (right_cols or []):
        out = c if c not in left_set else f"{right}_{c}"
        sel.append(f'"{right}"."{c}" AS "{out}"')
    select_list = ", ".join(sel) if sel else "*"
    cond = " AND ".join(
        f'"{jk.left_alias}"."{jk.left_column}" = "{jk.right_alias}"."{jk.right_column}"'
        for jk in d.join_keys
    )
    return f'SELECT {select_list} FROM "{left}" {jtype} "{right}" ON {cond}'


def compile_union_sql(item: BasketItem) -> str:
    """UNION [ALL] of the source aliases, positional (``SELECT * FROM a UNION …``).

    Column count + types must line up — validated at design time (frontend
    pre-check) and enforced by DuckDB on execute. ``union_all=False`` → DISTINCT.
    """
    d = item.derivation
    op = "UNION ALL" if d.union_all else "UNION"
    return f" {op} ".join(f'SELECT * FROM "{a}"' for a in d.source_aliases)


def _pull_source_into_duck(
    dc, conn, scope: ScopeContract, src: BasketItem, *,
    catalog: Catalog | None,
    concept_registry, binding_catalog,
) -> bool:
    """Pull a non-derived source dataset (a ``table_ref`` main or a ``sql``
    dataset) straight into ``conn`` as a view so that a **cached** derivation
    sitting on a **lazy** source can still materialise at build / hydration time.

    Pass 1 only fetches ``cached`` mains, so a lazy source feeding a derivation
    was otherwise never registered and the derivation got silently dropped with
    "unresolved sources" — leaving no parquet and making the Sunum produced-table
    preview 500 with "Table … does not exist". This mirrors the cron
    ``_compute_dataset_df`` path, which already pulls a lazy source to feed a
    derived dataset.

    The source is pulled *projected* (only the scoped columns) but **uncapped** —
    a derivation needs every source row to be correct (truncating would skew an
    aggregate / weighted average). It is NOT persisted as a dataset: no parquet is
    written and it is not added to ``loaded`` / ``cached_tables`` — only the small
    derived RESULT is. Returns True when a view was registered.
    """
    import pandas as pd

    from presentations.duck import materialize_table

    if src.table_ref is not None:
        sql, binds = compose_cached_sql(
            scope, src, catalog,
            concept_registry=concept_registry, binding_catalog=binding_catalog,
        )
        label = f"{src.table_ref.schema_name}.{src.table_ref.name}"
    elif src.sql is not None:
        from presentations.sql.validator import validate_sql
        chk = validate_sql(src.sql)
        if not chk.ok:
            raise RuntimeError(
                f"derived source '{src.alias}' SQL whitelist'i geçemedi: "
                f"{'; '.join(chk.errors)}"
            )
        sql, binds = src.sql, {}
        label = "manuel SQL"
    else:
        return False  # derived source — resolved by the dependency loop, not here

    df = dc.get_data(
        base_prefix=None,
        dataset=f"scope::{scope.presentation_id}/{src.alias}",
        query=sql, query_params=binds,
    )
    if df is None or len(df.columns) == 0:
        return False
    materialize_table(conn, src.alias, df)
    log.info("scope.fetch_cached_tables: lazy source '%s' ← %s pulled on demand "
             "for a cached derivation (%d rows)", src.alias, label, len(df))
    return True


def fetch_cached_tables(
    dc, conn, scope: ScopeContract, *,
    catalog: Catalog | None = None,
    concept_registry=None, binding_catalog=None,
    refetch_only: set[str] | None = None,
    drop_aliases: set[str] | None = None,
    on_dataset=None,
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

    ``on_dataset(alias, df, sql)`` — optional hook, her dataset DuckDB'ye
    yazıldıktan sonra ana thread'de çağrılır. Build bunu (a) S3 parquet'e
    one-shot materialize ve (b) async-progress raporu için kullanır.

    Returns ``{alias: {...}}`` for the aliases actually touched in this call
    (does not include aliases that were left intact across re-entry).
    """
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor

    from presentations.duck import drop_relation, materialize_table

    loaded: dict[str, dict[str, Any]] = {}

    def _notify(alias: str, df, sql: str) -> None:
        if on_dataset is None:
            return
        try:
            on_dataset(alias, df, sql)
        except Exception:
            log.warning("fetch_cached_tables: on_dataset hook failed for %s",
                        alias, exc_info=True)

    # Step 0 — drop relations for aliases that are gone in the new scope.
    # Failure is non-fatal: a view may already be gone from a pod restart.
    for alias in (drop_aliases or ()):
        drop_relation(conn, alias)
        log.info("scope.fetch_cached_tables: dropped stale relation '%s'", alias)

    # Pasif alias'lar basket'te yalnız LINEAGE için durur (örn. manuel-SQL
    # node'unun "Çözümle" kaynak main'leri — SQL Oracle'a kendisi gider, main'in
    # view'ına ihtiyacı yoktur). Bir cached derivation'ın DuckDB kaynağı
    # OLMAYAN pasifler Oracle'dan hiç çekilmez — "Sunum'a geç" bu yüzden
    # gereksiz full-table pull yapıyordu.
    inactive = set(scope.inactive_aliases or [])
    needed_views: set[str] = set()
    for b in scope.basket:
        if b.derivation is not None and b.routing.decision == "cached":
            needed_views |= duck_source_aliases(scope, b)

    def _lineage_only(item: BasketItem) -> bool:
        return item.alias in inactive and item.alias not in needed_views

    # Pass 1 + 1b — raw cached tablolar ve manuel-SQL dataset'leri. Oracle
    # pull'ları birbirinden bağımsızdır → thread havuzunda PARALEL çekilir
    # (DataClient her get_data çağrısında kendi bağlantısını açar; refine-sizes
    # ve scheduler zaten arka planda eşzamanlı çağırıyor). DuckDB yazımları
    # paylaşılan bağlantıda THREAD-SAFE DEĞİL → yalnız bu (ana) thread'de.
    from presentations.sql.validator import validate_sql

    jobs: list[dict[str, Any]] = []
    for item in scope.basket:
        is_raw = item.derivation is None and item.table_ref is not None
        is_sql = item.sql is not None
        if not (is_raw or is_sql):
            continue
        if item.routing.decision != "cached":
            continue
        if _lineage_only(item):
            log.info("scope.fetch_cached_tables: '%s' pasif + lineage-only — fetch atlandı",
                     item.alias)
            continue
        if refetch_only is not None and item.alias not in refetch_only:
            continue   # re-entry partial refresh — relation from scope_v<N-1> reused
        guard = _cached_row_guard(item, catalog)
        if is_raw:
            sql, binds = compose_cached_sql(
                scope, item, catalog,
                concept_registry=concept_registry,
                binding_catalog=binding_catalog,
                max_rows=guard + 1,
            )
            persist_sql = sql
            meta = {"table": f"{item.table_ref.schema_name}.{item.table_ref.name}"}
        else:
            chk = validate_sql(item.sql)
            if not chk.ok:
                raise RuntimeError(
                    f"manuel SQL dataset '{item.alias}' whitelist'i geçemedi: "
                    f"{'; '.join(chk.errors)}"
                )
            sql = f"SELECT * FROM (\n{item.sql}\n) FETCH FIRST {guard + 1} ROWS ONLY"
            binds = {}
            persist_sql = item.sql
            meta = {"sql": True}
        jobs.append({"item": item, "sql": sql, "binds": binds, "guard": guard,
                     "persist_sql": persist_sql, "meta": meta})

    def _pull(job):
        return dc.get_data(
            base_prefix=None,
            dataset=f"scope::{scope.presentation_id}/{job['item'].alias}",
            query=job["sql"], query_params=job["binds"],
        )

    if len(jobs) <= 1:
        results = [(job, _pull(job)) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(jobs)),
                                thread_name_prefix="scope-fetch") as ex:
            futures = [(job, ex.submit(_pull, job)) for job in jobs]
            results = [(job, fut.result()) for job, fut in futures]

    for job, df in results:
        item = job["item"]
        if df is None:
            df = pd.DataFrame()
        _guard_overflow(item.alias, df, job["guard"])
        if len(df.columns) > 0:
            materialize_table(conn, item.alias, df)
        loaded[item.alias] = {**job["meta"], "rows": int(len(df))}
        _notify(item.alias, df, job["persist_sql"])
        log.info("scope.fetch_cached_tables: %s ← %s (%d rows)",
                 item.alias, job["meta"].get("table", "manuel SQL"), len(df))

    # Pass 2 — derived nodes (filter / aggregate / calculated) in DEPENDENCY
    # ORDER (Faz A — zincirleme). A node is processed once all its DuckDB-source
    # aliases are registered, so chains (filter → aggregate → filter …) build
    # correctly regardless of basket order:
    #   - filter on an ORACLE source → compile_filter_sql (Oracle), dc.get_data.
    #   - filter on a DERIVED source → compile_filter_sql (DuckDB $-binds), run
    #     on the materialised source view in DuckDB.
    #   - aggregate / calculated → DuckDB over the materialised source view(s).
    #   - python (Faz P) → source view'i DataFrame'e çek, sandbox'ta çalıştır.
    pending = [b for b in scope.basket
               if b.derivation is not None and b.routing.decision == "cached"]
    registered = set(loaded.keys())  # raw cached tables just loaded in Pass 1
    # Re-entry: an unchanged cached source isn't refetched (refetch_only) but its
    # view from the previous build is still live in this conn — count those as
    # registered, else a CHANGED aggregate over an UNCHANGED source never runs
    # (dependency check would wait on an alias this round never loads).
    if refetch_only is not None:
        try:
            from presentations.duck import list_views
            registered |= set(list_views(conn))
        except Exception:
            log.warning("fetch_cached_tables: list_views seed failed", exc_info=True)
    progressed = True
    while pending and progressed:
        progressed = False
        still = []
        for item in pending:
            d = item.derivation
            all_srcs = ({d.source_alias} if d.kind in ("aggregate", "filter", "python") and d.source_alias
                        else set(d.source_aliases))
            # Re-entry: an inherited derivation (neither it nor any of its sources
            # changed) keeps the previous build's view — skip it BEFORE the source
            # gate so we don't pull a (possibly lazy/expensive) source just to
            # rebuild an unchanged result.
            if refetch_only is not None and not (all_srcs & refetch_only) and item.alias not in refetch_only:
                registered.add(item.alias)   # treated as up-to-date (inherited view)
                progressed = True
                continue
            # A cached derivation can sit on a LAZY main (or lazy SQL dataset) that
            # Pass 1 skipped — lazy tables aren't pre-fetched. Pull those *table*
            # sources on demand and register them as views so the derivation can
            # run, mirroring the cron `_compute_dataset_df` path. The big source
            # itself is NOT persisted (no parquet, never counted as cached); only
            # the small derived RESULT is. Missing *derived* sources are left to
            # the dependency loop below.
            for alias in list(duck_source_aliases(scope, item) - registered):
                src = scope.basket_item(alias)
                if src is None or src.derivation is not None:
                    continue
                if _pull_source_into_duck(
                    dc, conn, scope, src, catalog=catalog,
                    concept_registry=concept_registry, binding_catalog=binding_catalog,
                ):
                    registered.add(alias)
            if not (duck_source_aliases(scope, item) <= registered):
                still.append(item)
                continue
            if d.kind == "filter":
                src = scope.basket_item(d.source_alias)
                sql, binds = compile_filter_sql(
                    scope, item, catalog,
                    concept_registry=concept_registry, binding_catalog=binding_catalog,
                )
                if src is not None and src.table_ref is not None:
                    df = dc.get_data(
                        base_prefix=None,
                        dataset=f"scope::{scope.presentation_id}/{item.alias}",
                        query=sql, query_params=binds,
                    )
                    df = df if df is not None else pd.DataFrame()
                else:
                    df = conn.execute(sql, binds).fetchdf() if binds else conn.execute(sql).fetchdf()
                derived_from_value = d.source_alias
                label = "filter"
            elif d.kind == "python":
                # Faz P — source view'ini DataFrame olarak çek, sandbox'ta çalıştır.
                from presentations.python_runtime import run_python_transform
                src_df = conn.execute(f'SELECT * FROM "{d.source_alias}"').fetchdf()
                result = run_python_transform(d.python_code, src_df)
                if not result.ok:
                    raise RuntimeError(
                        f"python node '{item.alias}' çalıştırması başarısız: {result.error}"
                    )
                df = result.df
                sql = d.python_code
                derived_from_value = d.source_alias
                label = "python"
            else:
                if d.kind == "aggregate":
                    sql = compile_aggregate_sql(item)
                elif d.kind == "join":
                    lc = list(conn.execute(
                        f'SELECT * FROM "{d.source_aliases[0]}" LIMIT 0').fetchdf().columns)
                    rc = list(conn.execute(
                        f'SELECT * FROM "{d.source_aliases[1]}" LIMIT 0').fetchdf().columns)
                    sql = compile_join_sql(item, lc, rc)
                elif d.kind == "union":
                    sql = compile_union_sql(item)
                else:  # calculated
                    sql = compile_calculated_sql(item)
                try:
                    df = conn.execute(sql).fetchdf()
                except Exception as exc:
                    raise RuntimeError(
                        f"derived table '{item.alias}' failed ({sql!r}): {exc}"
                    ) from exc
                derived_from_value = (d.source_alias if d.kind == "aggregate"
                                      else list(d.source_aliases))
                label = d.kind
            if len(df.columns) > 0:
                materialize_table(conn, item.alias, df)
            loaded[item.alias] = {"derived_from": derived_from_value, "rows": int(len(df))}
            registered.add(item.alias)
            progressed = True
            _notify(item.alias, df, sql)
            log.info("scope.fetch_cached_tables: %s ⇐ %s of %s (%d rows)",
                     item.alias, label, derived_from_value, len(df))
        pending = still
    if pending:
        log.warning("scope.fetch_cached_tables: %d derived node(s) had unresolved "
                    "sources (cycle / missing): %s",
                    len(pending), [b.alias for b in pending])

    return loaded

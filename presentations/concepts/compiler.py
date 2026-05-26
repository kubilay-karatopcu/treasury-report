"""Filter compiler (Phase 7.b) — concept-level filters → per-table SQL.

Pure, deterministic, side-effect-free (spec §4). Given a set of resolved
concept filters and a set of tables in play, emit per-table SQL predicates +
parameterized binds. The compiler **never** concatenates values into SQL — all
values become positional binds whose names carry the filter id so multiple
filters of the same concept don't collide.

Determinism (locked decision §10.3): byte-identical output for identical
inputs. No timestamps, no uuids, no dict-iteration-order dependence.

Backward compat: a table with no binding for a filter's concept is
**concept-blind** — the compiler emits an empty predicate with ``blind=True``
and the block renders un-filtered with a UI badge (§4.4). It never errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog


@dataclass(frozen=True)
class ResolvedFilter:
    """A concept-level filter resolved to canonical values (spec §3.3).

    ``operator``:
      - ``in``      — enum/bucket membership; ``values`` is a list of canonical
                      codes (or bucket codes).
      - ``between`` — time range; ``values`` is ``[from, to]`` (dates / exprs).
      - ``eq``      — single scalar; ``values`` is a one-element list.
    """
    concept: str
    operator: str
    values: list[Any]
    filter_id: str
    granularity: str | None = None


@dataclass(frozen=True)
class CompiledPredicate:
    """One filter compiled against one table."""
    filter_id: str
    concept: str
    blind: bool
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    empty: bool = False     # True when the filter resolved to no values → block empties


# Per (schema, table) → ordered list of compiled predicates.
PerTablePredicates = dict[tuple[str, str], list[CompiledPredicate]]


def _blind(f: ResolvedFilter) -> CompiledPredicate:
    return CompiledPredicate(filter_id=f.filter_id, concept=f.concept,
                             blind=True, sql="", params={})


def _empty(f: ResolvedFilter) -> CompiledPredicate:
    # Always-false predicate so the block returns zero rows without crashing
    # on an empty IN (...) (mirrors Phase 6.5.c EmptySelectionError behaviour).
    return CompiledPredicate(filter_id=f.filter_id, concept=f.concept,
                             blind=False, sql="1 = 0", params={}, empty=True)


def _bind_name(f: ResolvedFilter, suffix: str) -> str:
    return f"{f.filter_id}_{f.concept}_{suffix}"


def _canonical_values(f: ResolvedFilter, registry: ConceptRegistry) -> list[Any]:
    """Resolve each filter value to its canonical code via the registry.

    Unknown values (the concept exists but the value isn't in its alphabet) are
    dropped — they can't match anything. For concepts with no canonical_values
    the registry passes the value through unchanged (Phase 6.5 behaviour).
    """
    out: list[Any] = []
    for v in f.values:
        canon = registry.resolve_value(f.concept, v)
        if canon is None:
            continue
        if canon not in out:
            out.append(canon)
    return out


def _compile_in(
    f: ResolvedFilter, binding, registry: ConceptRegistry
) -> CompiledPredicate:
    col = binding.column
    kind = binding.transform.kind
    canon = _canonical_values(f, registry)

    if kind == "bucket_from_range":
        return _compile_bucket(f, binding, registry, canon)

    if not canon:
        return _empty(f)

    if kind == "identity":
        params = {}
        placeholders = []
        for i, v in enumerate(canon):
            name = _bind_name(f, str(i))
            params[name] = v
            placeholders.append(f":{name}")
        return CompiledPredicate(
            filter_id=f.filter_id, concept=f.concept, blind=False,
            sql=f"{col} IN ({', '.join(placeholders)})", params=params,
        )

    if kind == "map":
        # pairs maps table_value → canonical; invert to canonical → table_value.
        inv: dict[str, str] = {}
        for table_val, canon_code in binding.transform.pairs.items():
            inv.setdefault(canon_code, table_val)
        params = {}
        placeholders = []
        for i, v in enumerate(canon):
            table_val = inv.get(v)
            if table_val is None:
                continue  # canonical value has no representation in this table
            name = _bind_name(f, str(i))
            params[name] = table_val
            placeholders.append(f":{name}")
        if not placeholders:
            return _empty(f)
        return CompiledPredicate(
            filter_id=f.filter_id, concept=f.concept, blind=False,
            sql=f"{col} IN ({', '.join(placeholders)})", params=params,
        )

    if kind == "lookup":
        t = binding.transform
        params = {}
        placeholders = []
        for i, v in enumerate(canon):
            name = _bind_name(f, str(i))
            params[name] = v
            placeholders.append(f":{name}")
        sub = (f"{col} IN (SELECT {t.dim_key} FROM {t.dim_table} "
               f"WHERE {t.dim_canonical} IN ({', '.join(placeholders)}))")
        return CompiledPredicate(
            filter_id=f.filter_id, concept=f.concept, blind=False,
            sql=sub, params=params,
        )

    # time_truncation under an "in" operator is nonsensical — treat as blind.
    return _blind(f)


def _compile_bucket(
    f: ResolvedFilter, binding, registry: ConceptRegistry, canon: list[Any]
) -> CompiledPredicate:
    col = binding.column
    ranges_concept = registry.get(binding.transform.ranges_concept)
    if ranges_concept is None:
        return _blind(f)
    if not canon:
        return _empty(f)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, code in enumerate(canon):
        cv = ranges_concept.get_value(code)
        if cv is None or cv.day_range is None:
            continue
        lo, hi = cv.day_range
        lo_name = _bind_name(f, f"{i}_lo")
        params[lo_name] = lo
        if hi is None:
            clauses.append(f"({col} >= :{lo_name})")
        else:
            hi_name = _bind_name(f, f"{i}_hi")
            params[hi_name] = hi
            clauses.append(f"({col} >= :{lo_name} AND {col} < :{hi_name})")
    if not clauses:
        return _empty(f)
    sql = clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"
    return CompiledPredicate(
        filter_id=f.filter_id, concept=f.concept, blind=False,
        sql=sql, params=params,
    )


def _compile_between(f: ResolvedFilter, binding) -> CompiledPredicate:
    col = binding.column
    if len(f.values) != 2:
        return _blind(f)
    lo, hi = f.values
    from_name = _bind_name(f, "from")
    to_name = _bind_name(f, "to")
    target = f"TRUNC({col})" if binding.transform.kind == "time_truncation" else col
    return CompiledPredicate(
        filter_id=f.filter_id, concept=f.concept, blind=False,
        sql=f"{target} BETWEEN :{from_name} AND :{to_name}",
        params={from_name: lo, to_name: hi},
    )


def _compile_eq(f: ResolvedFilter, binding, registry: ConceptRegistry) -> CompiledPredicate:
    canon = _canonical_values(f, registry)
    if not canon:
        return _empty(f)
    name = _bind_name(f, "0")
    return CompiledPredicate(
        filter_id=f.filter_id, concept=f.concept, blind=False,
        sql=f"{binding.column} = :{name}", params={name: canon[0]},
    )


def compile_filter_for_table(
    f: ResolvedFilter,
    schema: str,
    table: str,
    registry: ConceptRegistry,
    catalog: BindingCatalog,
) -> CompiledPredicate:
    """Compile a single filter against a single table."""
    binding = catalog.get_binding(schema, table, f.concept)
    if binding is None:
        return _blind(f)
    if f.operator == "between":
        return _compile_between(f, binding)
    if f.operator == "eq":
        return _compile_eq(f, binding, registry)
    # default: "in"
    return _compile_in(f, binding, registry)


def compile_filters(
    filters: list[ResolvedFilter],
    tables: list[tuple[str, str]],
    registry: ConceptRegistry,
    catalog: BindingCatalog,
) -> PerTablePredicates:
    """Compile every (table, filter) pair (spec §4).

    Output is keyed by ``(schema, table)``; predicate order within a table
    follows ``filters`` order. Deterministic for fixed inputs.
    """
    out: PerTablePredicates = {}
    for schema, table in tables:
        preds: list[CompiledPredicate] = []
        for f in filters:
            preds.append(compile_filter_for_table(f, schema, table, registry, catalog))
        out[(schema, table)] = preds
    return out


def blind_concepts_for_table(preds: list[CompiledPredicate]) -> list[str]:
    """Concepts whose filter was concept-blind on this table (for the UI badge)."""
    return [p.concept for p in preds if p.blind]

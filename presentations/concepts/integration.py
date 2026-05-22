"""Wire the concept filter compiler into the dashboard apply-filters path (7.b.3).

Bridges Phase 6.5.c dashboard filters into Phase 7 concept compilation, then
injects the compiled predicates into a block's SQL — additively, at an
explicit sentinel, with zero impact on blocks that don't opt in.

Design constraints (locked decisions §10):
- §10.6 No SQL rewriting. We do NOT parse the block's user SQL. Predicates are
  injected at a literal sentinel token the block author places in their WHERE.
  No sentinel → no injection (the predicates are still *reported* so the UI can
  show what's available, but the executed SQL is untouched).
- §10.7 Concept-blind tables render normally with a badge — never an error.
- §6.2 Backward compat: a filter's concept ref is ``concept_ref`` if present,
  else the Phase 6.5 ``semantic_tag`` (direct lookup).

A block opts into concept filtering by:
  1. declaring ``source_tables: [{schema, table}, ...]``, and
  2. placing the sentinel ``{{concept_filters}}`` in its query's WHERE clause.

Blocks lacking either are byte-for-byte unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.compiler import (
    ResolvedFilter,
    CompiledPredicate,
    compile_filters,
)


SENTINEL = "{{concept_filters}}"


@dataclass(frozen=True)
class ConceptInjection:
    sql: str
    params: dict[str, Any]
    injected: bool
    applied: list[dict[str, Any]]   # [{filter_id, concept, sql}] for the response
    blind: list[str]                # concept ids the table is blind to
    empty: bool = False             # a concept filter selected nothing → block empties


def _concept_ref(filt: dict[str, Any]) -> str | None:
    """The concept a dashboard filter targets (§6.2: concept_ref ?? semantic_tag)."""
    return filt.get("concept_ref") or filt.get("semantic_tag")


def dashboard_filters_to_resolved(
    manifest_filters: list[dict[str, Any]],
    filter_state: dict[str, Any],
    registry: ConceptRegistry,
) -> list[ResolvedFilter]:
    """Convert Phase 6.5.c dashboard filters + live state → concept filters.

    Filters whose concept isn't in the registry are skipped here — the Phase
    6.5.c variable-binding path still handles them (this layer is additive).
    """
    out: list[ResolvedFilter] = []
    for f in manifest_filters or []:
        fid = f.get("id")
        concept = _concept_ref(f)
        if not fid or not concept or not registry.has(concept):
            continue
        val = filter_state.get(fid, f.get("default"))
        if val is None:
            continue
        ftype = f.get("type")
        if ftype == "date_range":
            if isinstance(val, dict) and "from" in val and "to" in val:
                out.append(ResolvedFilter(concept, "between",
                                          [val["from"], val["to"]], fid))
        elif ftype in ("enum_multi", "enum_single"):
            vals = val if isinstance(val, list) else [val]
            out.append(ResolvedFilter(concept, "in", list(vals), fid))
        elif ftype == "date":
            out.append(ResolvedFilter(concept, "eq", [val], fid))
        # number_range: deferred (no numeric concept transform in v0).
    return out


def _block_tables(block: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for t in block.get("source_tables") or []:
        if isinstance(t, dict) and t.get("schema") and t.get("table"):
            out.append((t["schema"], t["table"]))
    return out


def apply_concepts_to_block(
    block: dict[str, Any],
    base_sql: str,
    base_params: dict[str, Any],
    resolved_filters: list[ResolvedFilter],
    registry: ConceptRegistry,
    catalog: BindingCatalog,
) -> ConceptInjection:
    """Compile concept filters for ``block`` and inject into ``base_sql``.

    ``base_sql`` / ``base_params`` are the already-bound block SQL (post
    ``expand_binds``). Returns a :class:`ConceptInjection` describing the
    (possibly unchanged) SQL + merged params + what was applied / blind.
    """
    tables = _block_tables(block)
    if not tables or not resolved_filters:
        return ConceptInjection(sql=base_sql, params=dict(base_params),
                                injected=False, applied=[], blind=[])

    per_table = compile_filters(resolved_filters, tables, registry, catalog)

    # Collect: usable predicates, blind concepts, and any empty short-circuit.
    usable: list[CompiledPredicate] = []
    blind: list[str] = []
    seen_blind: set[str] = set()
    any_empty = False
    for _key, preds in per_table.items():
        for p in preds:
            if p.blind:
                if p.concept not in seen_blind:
                    seen_blind.add(p.concept)
                    blind.append(p.concept)
            elif p.empty:
                any_empty = True
            elif p.sql:
                usable.append(p)

    # An empty concept selection empties the whole block (mirrors §4.3).
    if any_empty:
        merged = dict(base_params)
        applied = [{"filter_id": p.filter_id, "concept": p.concept, "sql": p.sql}
                   for p in usable]
        if SENTINEL in base_sql:
            sql = base_sql.replace(SENTINEL, "1 = 0")
            return ConceptInjection(sql=sql, params=merged, injected=True,
                                    applied=applied, blind=blind, empty=True)
        return ConceptInjection(sql=base_sql, params=merged, injected=False,
                                applied=applied, blind=blind, empty=True)

    applied = [{"filter_id": p.filter_id, "concept": p.concept, "sql": p.sql}
               for p in usable]

    if not usable:
        return ConceptInjection(sql=base_sql, params=dict(base_params),
                                injected=False, applied=[], blind=blind)

    where = " AND ".join(p.sql for p in usable)
    merged = dict(base_params)
    for p in usable:
        merged.update(p.params)

    if SENTINEL in base_sql:
        sql = base_sql.replace(SENTINEL, where)
        return ConceptInjection(sql=sql, params=merged, injected=True,
                                applied=applied, blind=blind)

    # No sentinel — report what would apply, but leave SQL untouched (§10.6).
    return ConceptInjection(sql=base_sql, params=dict(base_params),
                            injected=False, applied=applied, blind=blind)

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

import re
from dataclasses import dataclass, field
from typing import Any

from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.compiler import (
    ResolvedFilter,
    CompiledPredicate,
    compile_filters,
)
from presentations.variables.resolver import parse_date_expr


SENTINEL = "{{concept_filters}}"

# Schema-qualified table after FROM/JOIN (e.g. "FROM EDW.DEPOSITS_DAILY t").
# Used only as a fallback to derive source_tables when the block (LLM or
# user) didn't declare them explicitly — reliable for the common single-table
# case; explicit source_tables always wins.
_FROM_JOIN_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_$#]*)\.([A-Za-z_][A-Za-z0-9_$#]*)",
    re.IGNORECASE,
)


def derive_source_tables(block: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``[(SCHEMA, TABLE), ...]`` for a block.

    Prefers the explicit ``source_tables`` field (LLM-authored / future
    scope contract). Falls back to parsing schema-qualified tables from the
    block's SQL ``FROM`` / ``JOIN`` clauses so concept filters still reach
    blocks whose author omitted the field. All identifiers upper-cased to
    match the catalog keys.
    """
    explicit = block.get("source_tables")
    if explicit:
        out: list[tuple[str, str]] = []
        for t in explicit:
            if isinstance(t, dict) and t.get("schema") and t.get("table"):
                out.append((str(t["schema"]).upper(), str(t["table"]).upper()))
        if out:
            return out
    sql = block.get("query") or (block.get("data_source") or {}).get("original_sql") or ""
    seen: set[tuple[str, str]] = set()
    out = []
    for m in _FROM_JOIN_RE.finditer(sql):
        key = (m.group(1).upper(), m.group(2).upper())
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# Trailing clauses that a WHERE predicate must precede (top-level only).
_TRAILING_CLAUSE_RE = re.compile(
    r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|FETCH\s+(?:FIRST|NEXT)|OFFSET|LIMIT|WINDOW)\b",
    re.IGNORECASE,
)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


def _toplevel_matches(text: str, regex: "re.Pattern") -> list:
    """Regex matches at parenthesis depth 0 (ignores subquery contents)."""
    out = []
    for m in regex.finditer(text):
        depth = text.count("(", 0, m.start()) - text.count(")", 0, m.start())
        if depth == 0:
            out.append(m)
    return out


# ── Auto-conceptualize a manual query ──────────────────────────────────────
# Detect literal predicates on concept-bound columns in a user's WHERE and
# lift them into dashboard concept filters, replacing them with the
# {{concept_filters}} sentinel. Non-concept predicates (e.g. STATUS='ACTIVE')
# stay hardcoded. v0 scope: top-level AND-separated `COL = '...'` / `COL IN
# (...)` on identity/map concepts. OR-containing WHEREs, BETWEEN/time, and
# lookup/bucket transforms are left untouched.

_AND_RE = re.compile(r"\bAND\b", re.IGNORECASE)
_OR_RE = re.compile(r"\bOR\b", re.IGNORECASE)
_EQ_PRED_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$#]*)\s*=\s*'([^']*)'\s*$")
_IN_PRED_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$#]*)\s+IN\s*\((.+)\)\s*$", re.IGNORECASE)
_STR_LIT_RE = re.compile(r"'([^']*)'")


def _split_top_and(text: str) -> list[str]:
    parts, last = [], 0
    for m in _AND_RE.finditer(text):
        s = m.start()
        if text.count("(", 0, s) - text.count(")", 0, s) == 0:
            parts.append(text[last:s])
            last = m.end()
    parts.append(text[last:])
    return [p.strip() for p in parts if p.strip()]


def _parse_predicate(conjunct: str) -> tuple[str | None, list[str]]:
    """Return (column, [string literal values]) for `COL = '...'` / `COL IN
    ('a','b')`, else (None, []). Only pure string-literal IN lists qualify
    (rejects IN (subquery), numbers, etc.)."""
    m = _EQ_PRED_RE.match(conjunct)
    if m:
        return m.group(1), [m.group(2)]
    m = _IN_PRED_RE.match(conjunct)
    if m:
        col, inner = m.group(1), m.group(2)
        vals = _STR_LIT_RE.findall(inner)
        residue = _STR_LIT_RE.sub("", inner)        # strip the 'literals'
        if vals and residue.replace(",", "").strip() == "":
            return col, vals
    return None, []


def _reverse_translate(binding, table_values: list[str]) -> list[str] | None:
    """Table literal(s) → canonical code(s). None if any value can't translate."""
    kind = binding.transform.kind
    if kind == "identity":
        return list(table_values)              # table value IS the canonical token
    if kind == "map":
        pairs = binding.transform.pairs        # {table_value: canonical}
        out = []
        for v in table_values:
            if v not in pairs:
                return None
            out.append(pairs[v])
        return out
    return None                                # bucket / lookup / time → don't convert


def conceptualize_query(sql: str, schema: str, table: str, catalog, registry) -> dict:
    """Lift concept-bound literal predicates out of ``sql`` into concept filters.

    Returns ``{rewritten_sql, seeded_filters, converted, skipped}``:
      - ``rewritten_sql``  : SQL with lifted predicates replaced by
                             ``{{concept_filters}}`` (non-concept predicates kept).
      - ``seeded_filters`` : dashboard filter defs (one per detected concept,
                             default = the extracted canonical values).
      - ``converted``      : ``[{column, concept, values}]`` for UI display.
      - ``skipped``        : human-readable reasons anything was left as-is.
    """
    result = {"rewritten_sql": sql, "seeded_filters": [], "converted": [], "skipped": []}

    where_m = _toplevel_matches(sql, _WHERE_RE)
    if not where_m:
        return result
    head = sql[:where_m[0].start()]
    rest = sql[where_m[0].end():]
    trailing = _toplevel_matches(rest, _TRAILING_CLAUSE_RE)
    cut = trailing[0].start() if trailing else len(rest)
    where_text, tail = rest[:cut], rest[cut:]

    if [m for m in _OR_RE.finditer(where_text)
        if where_text.count("(", 0, m.start()) - where_text.count(")", 0, m.start()) == 0]:
        result["skipped"].append("WHERE'de OR var — güvenlik için concept'e çevrilmedi")
        return result

    bindings = {b.column.upper(): b for b in catalog.get_bindings(schema, table)}

    kept: list[str] = []
    by_concept: dict[str, list[str]] = {}
    for cj in _split_top_and(where_text):
        if cj == SENTINEL or re.match(r"^1\s*=\s*1$", cj):
            continue
        col, vals = _parse_predicate(cj)
        binding = bindings.get(col.upper()) if col else None
        if binding is None:
            kept.append(cj)
            continue
        canon = _reverse_translate(binding, vals)
        if canon is None:
            kept.append(cj)
            result["skipped"].append(f"{col}: değer concept'e çevrilemedi, hardcoded kaldı")
            continue
        bucket = by_concept.setdefault(binding.concept, [])
        for c in canon:
            if c not in bucket:
                bucket.append(c)
        result["converted"].append({"column": col, "concept": binding.concept, "values": canon})

    if not by_concept:
        return result

    new_where = " AND ".join(kept + [SENTINEL])
    result["rewritten_sql"] = (head.rstrip() + " WHERE " + new_where
                               + (" " + tail.strip() if tail.strip() else "")).strip()

    for concept_id, canon_vals in by_concept.items():
        c = registry.get(concept_id)
        codes = [cv.code for cv in c.canonical_values] if c and c.canonical_values else list(canon_vals)
        result["seeded_filters"].append({
            "id": "f_" + concept_id,
            "semantic_tag": concept_id,
            "type": "enum_multi",
            "label": c.name if c else concept_id,
            "allowed_values": codes,
            "default": list(canon_vals),
        })
    return result


def inject_where_predicate(sql: str, predicate: str) -> str:
    """Append ``predicate`` to a single-SELECT query's WHERE without a sentinel.

    Inserts before the first top-level GROUP BY / ORDER BY / HAVING / FETCH /
    OFFSET (or at the end). ANDs onto an existing top-level WHERE, else adds a
    new WHERE. Parenthesis-aware so subquery WHEREs/clauses are never touched.
    Used when the block author (or LLM) didn't embed ``{{concept_filters}}``.
    """
    body = sql.rstrip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    clause = _toplevel_matches(body, _TRAILING_CLAUSE_RE)
    insert_at = clause[0].start() if clause else len(body)
    head, tail = body[:insert_at], body[insert_at:]
    if _toplevel_matches(head, _WHERE_RE):
        head = head.rstrip() + f" AND ({predicate})"
    else:
        head = head.rstrip() + f" WHERE {predicate}"
    return (head + (" " + tail if tail else "")).strip()


def strip_concept_sentinel(sql: str) -> str:
    """Neutralize an un-injected ``{{concept_filters}}`` to a no-op ``1 = 1``.

    A block may carry the sentinel but be executed in a path where no concept
    predicate applies (manual run, preview, or apply-filters with no active
    concept filter). The literal token would be invalid SQL, so any execution
    path that doesn't go through the concept compiler MUST call this on the
    final SQL before running it. Idempotent; a no-op when the sentinel is
    absent (the overwhelmingly common case for pre-Phase-7 blocks).
    """
    if SENTINEL in sql:
        return sql.replace(SENTINEL, "1 = 1")
    return sql


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
            # Resolve relative exprs ("today", "today - 30d") → concrete dates
            # here, at the boundary. The compiler stays pure/deterministic
            # (§10.3) and Oracle never sees a literal "today" bind. Mirrors the
            # variable path (dashboards/binding.py).
            if isinstance(val, dict) and "from" in val and "to" in val:
                try:
                    frm = parse_date_expr(val["from"])
                    to = parse_date_expr(val["to"])
                except (ValueError, TypeError):
                    continue   # unparseable bound → skip (don't crash apply)
                out.append(ResolvedFilter(concept, "between", [frm, to], fid))
        elif ftype in ("enum_multi", "enum_single"):
            vals = val if isinstance(val, list) else [val]
            out.append(ResolvedFilter(concept, "in", list(vals), fid))
        elif ftype == "date":
            try:
                d = parse_date_expr(val)
            except (ValueError, TypeError):
                continue
            out.append(ResolvedFilter(concept, "eq", [d], fid))
        # number_range: deferred (no numeric concept transform in v0).
    return out


def _block_tables(block: dict[str, Any]) -> list[tuple[str, str]]:
    # Explicit source_tables, else FROM-clause fallback (§ derive_source_tables).
    return derive_source_tables(block)


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
        else:
            sql = inject_where_predicate(base_sql, "1 = 0")
        return ConceptInjection(sql=sql, params=merged, injected=True,
                                applied=applied, blind=blind, empty=True)

    # Multi-table blocks: predicates are emitted UNQUALIFIED (the compiler is
    # per-table by design), but flattening them into one outer WHERE over a JOIN
    # makes Oracle raise ORA-00918 when two joined tables share the column — or,
    # worse, silently filter the wrong table. Reliable alias qualification isn't
    # possible here (lookup/bucket transforms emit subqueries, not bare columns),
    # so we inject only when the block has a SINGLE source table. Multi-table
    # blocks render concept-blind — surfaced via `blind` per locked decision #7
    # ("filter not applied here") rather than emitting wrong/broken SQL.
    if len(tables) > 1 and usable:
        for p in usable:
            if p.concept not in seen_blind:
                seen_blind.add(p.concept)
                blind.append(p.concept)
        usable = []

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
        # Preferred path: clean replacement at the author-placed sentinel.
        sql = base_sql.replace(SENTINEL, where)
    else:
        # No sentinel — inject into the WHERE clause directly so the filter
        # still applies (the user's expectation; parenthesis-aware insertion).
        sql = inject_where_predicate(base_sql, where)
    return ConceptInjection(sql=sql, params=merged, injected=True,
                            applied=applied, blind=blind)

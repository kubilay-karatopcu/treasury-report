"""Scope contract validators (spec §2.2).

Seven rules, each an independently-callable function returning
``(errors, warnings)`` so it can be unit-tested in isolation (the fixture
``examples/phase_8/expected_validator_outputs.yaml`` drives one case per rule).
:func:`validate_scope` runs all seven and aggregates into a
:class:`ValidationResult`.

The split between *errors* and *warnings* follows the spec: a warning means
"this probably isn't what you want / will be slow" (the scope can still be
saved and fetched); an error blocks the build. Concept coverage (rule 3) and
the projection partition-column omission (rule 6) are warnings; everything
else that fails is an error.

All catalog lookups go through the :mod:`presentations.scope.catalog`
abstraction. A table absent from the catalog is treated as "cannot verify" —
its column / coverage checks are skipped rather than failed, matching Phase 7's
concept-blind tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from presentations.scope.catalog import Catalog
from presentations.scope.schema import ScopeContract


# Floor below which a routing threshold is almost certainly a misconfiguration
# (1 MB). Matches the message in expected_validator_outputs.yaml.
THRESHOLD_FLOOR_BYTES = 1024 * 1024


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Rule 1: alias uniqueness ────────────────────────────────────────────────

def rule_alias_uniqueness(scope: ScopeContract, catalog: Catalog | None = None):
    errors: list[str] = []
    seen: set[str] = set()
    reported: set[str] = set()
    for item in scope.basket:
        a = item.alias
        if a in seen and a not in reported:
            errors.append(f"Duplicate basket alias '{a}'")
            reported.add(a)
        seen.add(a)
    return errors, []


# ── Rule 2: concept validity ────────────────────────────────────────────────

def rule_concept_validity(scope: ScopeContract, catalog: Catalog):
    errors: list[str] = []
    seen: set[str] = set()
    for f in [*scope.filters.pinned, *scope.filters.interactive]:
        c = f.concept
        if c in seen:
            continue
        if not catalog.concept_exists(c):
            errors.append(f"Concept '{c}' not in registry")
            seen.add(c)
    return errors, []


# ── Rule 3: concept coverage (warning) ──────────────────────────────────────

def rule_concept_coverage(scope: ScopeContract, catalog: Catalog):
    warnings: list[str] = []
    for f in [*scope.filters.pinned, *scope.filters.interactive]:
        if not f.applies_to:
            continue  # empty = "all tables that bind it"; nothing explicit to check.
        for alias in f.applies_to:
            item = scope.basket_item(alias)
            if item is None or item.table_ref is None:
                continue  # alias absent, or a derived/sql dataset (no table to verify).
            bound = catalog.table_binds_concept(
                item.table_ref.schema_name, item.table_ref.name, f.concept
            )
            if bound is False:
                warnings.append(
                    f"Filter '{f.id}' has no effect on alias '{alias}' "
                    f"(concept '{f.concept}' not bound)"
                )
            # bound is None → table not in catalog → skip (cannot verify).
    return [], warnings


# ── Rule 4: pinned filter consistency ───────────────────────────────────────

def _as_date(v):
    # Resolve ISO, date objects, and the relative grammar (today, today - 7d,
    # bare today - 7, start_of_month …) so the between-consistency check orders
    # relative ranges correctly instead of comparing the raw exprs as strings.
    if isinstance(v, date):
        return v
    try:
        from presentations.variables.resolver import parse_date_expr
        return parse_date_expr(v)
    except Exception:
        return None


def _as_number(v):
    # Somut sayısal sınır mı? bool, int alt sınıfı olduğu için hariç tutulur
    # (aksi halde True/False '1/0' gibi sıralanırdı).
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def rule_pinned_consistency(scope: ScopeContract, catalog: Catalog):
    errors: list[str] = []
    for f in scope.filters.pinned:
        if f.op == "between":
            lo, hi = f.from_, f.to
            lo_d, hi_d = _as_date(lo), _as_date(hi)
            # Only flag when both endpoints resolve to real dates. Unresolvable
            # values can't be ordered — skip rather than lexically comparing
            # exprs (which wrongly ranks "today - 7d" > "today").
            inverted = lo_d is not None and hi_d is not None and lo_d > hi_d
            if not inverted:
                # Tarih çözülmediyse, her iki sınır da somut sayıysa doğrudan
                # karşılaştır (ters sayısal aralık sessizce 0 satır eşler).
                lo_n, hi_n = _as_number(lo), _as_number(hi)
                inverted = lo_n is not None and hi_n is not None and lo_n > hi_n
            if inverted:
                errors.append(
                    f"Pinned filter '{f.id}': between requires from <= to "
                    f"(got {lo} > {hi})"
                )
        elif f.op in ("in", "not_in"):
            codes = catalog.concept_canonical_codes(f.concept)
            if codes is not None:
                allowed = set(codes)
                for v in (f.values or []):
                    if v not in allowed:
                        errors.append(
                            f"Pinned filter '{f.id}': value '{v}' not in concept "
                            f"'{f.concept}' canonical_values"
                        )
    return errors, []


# ── Rule 5: join consistency ────────────────────────────────────────────────

def rule_join_consistency(scope: ScopeContract, catalog: Catalog | None = None):
    errors: list[str] = []
    aliases = set(scope.alias_list())
    for j in scope.joins:
        for side_name, side in (("left", j.left), ("right", j.right)):
            if side.alias not in aliases:
                errors.append(
                    f"Join '{j.id}': {side_name} alias '{side.alias}' not in basket"
                )
                continue
            item = scope.basket_item(side.alias)
            if item is None:
                continue
            if item.projection.include_all:
                # Verify against the table schema when the catalog knows it.
                if catalog is not None and item.table_ref is not None:
                    tm = catalog.table_meta(
                        item.table_ref.schema_name, item.table_ref.name
                    )
                    if tm is not None and not tm.has_column(side.column):
                        errors.append(
                            f"Join '{j.id}': column '{side.column}' does not exist "
                            f"on {item.table_ref.name}"
                        )
            elif side.column not in item.projection.columns:
                errors.append(
                    f"Join '{j.id}': column '{side.column}' not projected on "
                    f"alias '{side.alias}'"
                )
    return errors, []


# ── Rule 6: projection sanity ───────────────────────────────────────────────

def rule_projection_sanity(scope: ScopeContract, catalog: Catalog):
    errors: list[str] = []
    warnings: list[str] = []
    for item in scope.basket:
        if item.table_ref is None:
            continue  # derived (aggregate) table — no Oracle columns to verify.
        tm = catalog.table_meta(item.table_ref.schema_name, item.table_ref.name)
        if tm is None:
            continue  # table not in catalog — cannot verify columns.
        proj = item.projection
        if not proj.include_all:
            for col in proj.columns:
                if not tm.has_column(col):
                    errors.append(
                        f"Projection on '{item.alias}': column '{col}' does not "
                        f"exist on {item.table_ref.name}"
                    )
            if tm.partition_column and tm.partition_column not in proj.columns:
                warnings.append(
                    f"Projection on '{item.alias}' omits partition column "
                    f"'{tm.partition_column}'; queries may be slow"
                )
    return errors, warnings


# ── Rule 7: routing threshold sanity ────────────────────────────────────────

def rule_routing_threshold(scope: ScopeContract, catalog: Catalog | None = None):
    errors: list[str] = []
    warnings: list[str] = []
    for item in scope.basket:
        r = item.routing
        if r.estimated_bytes < 0:
            errors.append(
                f"Routing for '{item.alias}': estimated_bytes must be >= 0 "
                f"(got {r.estimated_bytes})"
            )
        if r.threshold_bytes is not None and r.threshold_bytes < THRESHOLD_FLOOR_BYTES:
            warnings.append(
                f"Routing for '{item.alias}': threshold_bytes {r.threshold_bytes} "
                f"below floor ({THRESHOLD_FLOOR_BYTES}), likely misconfiguration"
            )
    return errors, warnings


# ── Rule 8: raw (non-concept) filter sanity (§6R.4) ─────────────────────────

def rule_raw_filters(scope: ScopeContract, catalog: Catalog):
    errors: list[str] = []
    aliases = set(scope.alias_list())
    for f in scope.filters.raw:
        if f.alias not in aliases:
            errors.append(f"Raw filter '{f.id}': alias '{f.alias}' not in basket")
            continue
        item = scope.basket_item(f.alias)
        tm = (catalog.table_meta(item.table_ref.schema_name, item.table_ref.name)
              if item and item.table_ref else None)
        if tm is not None and not tm.has_column(f.column):
            errors.append(
                f"Raw filter '{f.id}': column '{f.column}' does not exist on "
                f"{item.table_ref.name}"
            )
        if f.op == "between":
            lo, hi = _as_date(f.from_), _as_date(f.to)
            inverted = lo is not None and hi is not None and lo > hi
            if not inverted:
                # Tarih çözülmediyse somut sayısal sınırları doğrudan karşılaştır
                # (ters sayısal between sessizce 0 satır eşler).
                lo_n, hi_n = _as_number(f.from_), _as_number(f.to)
                inverted = lo_n is not None and hi_n is not None and lo_n > hi_n
            if inverted:
                errors.append(
                    f"Raw filter '{f.id}': between requires from <= to "
                    f"(got {f.from_} > {f.to})"
                )
    return errors, []


# ── Rule 9: derived (aggregate) tables (§6R) ────────────────────────────────

def rule_derived_tables(scope: ScopeContract, catalog: Catalog | None = None):
    """§6R aggregate + Polish-5 calculated: every source alias a derivation
    references must exist in the basket. Per-kind shape constraints
    (group_by/measures vs columns/join_keys) are already enforced by the
    Pydantic model_validator — this rule only checks cross-item references."""
    errors: list[str] = []
    aliases = set(scope.alias_list())
    for item in scope.derived_items():
        d = item.derivation
        if d.kind == "aggregate":
            if d.source_alias not in aliases:
                errors.append(
                    f"Derived table '{item.alias}': source alias '{d.source_alias}' not in basket"
                )
            if not d.group_by and not d.measures:
                errors.append(
                    f"Derived table '{item.alias}': aggregate needs at least one group_by or measure"
                )
        elif d.kind == "python":
            # Tek-girişli Python dönüşümü — source_alias basket'te olmalı.
            if d.source_alias not in aliases:
                errors.append(
                    f"Derived table '{item.alias}': source alias '{d.source_alias}' not in basket"
                )
        else:  # calculated / filter / join / union
            for src in d.source_aliases:
                if src not in aliases:
                    errors.append(
                        f"Derived table '{item.alias}': source alias '{src}' not in basket"
                    )
    return errors, []


# ── Rule 10: derivation DAG — cycle detection ───────────────────────────────

def rule_derivation_dag(scope: ScopeContract, catalog: Catalog | None = None):
    """Türetme zinciri DAG olmalı. Döngü (filter A → filter B → A) önceden
    validasyondan sızıp fetch pass'inde yalnız warning loglanıyordu — node'lar
    sessizce boş kalıyordu. Kahn topolojik sıralaması: eriyemeyen düğüm kümesi
    döngüdür → build'i açık hatayla durdur."""
    graph: dict[str, list[str]] = {}
    for item in scope.derived_items():
        d = item.derivation
        if d.kind in ("aggregate", "filter", "python"):
            srcs = [d.source_alias] if d.source_alias else []
        else:  # calculated / join / union
            srcs = list(d.source_aliases)
        graph[item.alias] = srcs

    indeg = {a: 0 for a in graph}
    dependents: dict[str, list[str]] = {a: [] for a in graph}
    for a, srcs in graph.items():
        for s in srcs:
            if s in graph:           # raw/sql kaynaklar yaprak — döngüye giremez
                indeg[a] += 1
                dependents[s].append(a)
    queue = [a for a, n in indeg.items() if n == 0]
    resolved = 0
    while queue:
        n = queue.pop()
        resolved += 1
        for m in dependents[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if resolved < len(graph):
        cyclic = sorted(a for a, n in indeg.items() if n > 0)
        return [
            "Derivation cycle: " + ", ".join(cyclic)
            + " — türetilmiş tablolar birbirine döngüsel bağlanamaz"
        ], []
    return [], []


# ── Aggregate ───────────────────────────────────────────────────────────────

# Ordered so the result reads predictably; §2.2 numbering (+ §6R raw/derived).
RULES = [
    rule_alias_uniqueness,
    rule_concept_validity,
    rule_concept_coverage,
    rule_pinned_consistency,
    rule_join_consistency,
    rule_projection_sanity,
    rule_routing_threshold,
    rule_raw_filters,
    rule_derived_tables,
    rule_derivation_dag,
]


def validate_scope(scope: ScopeContract, catalog: Catalog) -> ValidationResult:
    """Run all seven §2.2 rules and aggregate. ``ok`` is True iff no errors."""
    errors: list[str] = []
    warnings: list[str] = []
    for rule in RULES:
        e, w = rule(scope, catalog)
        errors.extend(e)
        warnings.extend(w)
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)

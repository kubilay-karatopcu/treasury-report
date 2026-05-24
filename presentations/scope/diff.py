"""Scope contract diff for the Hazırlık re-entry flow (spec §3.6).

When the user clicks "Sunum'a geç" on Hazırlık and a previous scope exists
(``scope_v<N>``), we need to know what changed so that:

  - Only changed aliases get re-fetched from Oracle (§8.e bullet 3).
  - Blocks affected by the change get flagged for a warning UI (§3.6 step g).
  - The new scope gets ``parent_version = N`` recorded (§3.6 step d).

This module is pure: it takes two ScopeContract instances and returns a
:class:`ScopeDiff` describing the changes, without consulting the catalog,
the LLM, or any I/O. The companion module :mod:`presentations.scope.impact`
turns a ScopeDiff + a dashboard manifest into the user-visible "affected
blocks" warning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from presentations.scope.schema import (
    BasketItem,
    Join,
    PinnedFilter,
    InteractiveFilter,
    ScopeContract,
)


@dataclass(frozen=True)
class FilterChange:
    """One pinned filter that changed between two scopes.

    Carries both the old and new shapes when the *same* id changed value,
    or one of them is ``None`` for pure add / remove.
    """
    filter_id: str
    old: PinnedFilter | None
    new: PinnedFilter | None


@dataclass(frozen=True)
class PinStateFlip:
    """A concept's pinned ↔ interactive transition (§3.6 / §10.e).

    Pinned and interactive filter ids carry distinct prefixes (``pf_*`` vs
    ``if_*``) so we can't detect flips by id-equality. We approximate by
    concept-equality: if an interactive filter exists in v_new whose concept
    appears in v_old's pinned set (and the pinned one is gone in v_new),
    that's a ``pinned_to_interactive`` flip — and vice versa.

    ``filter_id`` records the NEW side's id so the UI can highlight what
    survived; the OLD side already shows up in ``filter_changes`` as a
    removal.
    """
    filter_id: str
    direction: str    # "pinned_to_interactive" | "interactive_to_pinned"


@dataclass(frozen=True)
class JoinChange:
    """One join that was added, removed, or had its kind/keys changed."""
    join_id: str
    old: Join | None
    new: Join | None


@dataclass(frozen=True)
class ScopeDiff:
    """Structured diff between two scope contracts (old → new).

    Field semantics:

    ``added_aliases`` / ``removed_aliases``
        Basket items keyed by alias. Aliases that exist in only one side.

    ``changed_aliases``
        Alias is present in both but its BasketItem differs in projection,
        routing, table_ref, or derivation. Each entry pairs the (old, new)
        BasketItem for downstream inspection.

    ``filter_changes``
        Pinned filters keyed by id. ``add``: id in new only; ``remove``: id
        in old only; ``modify``: id in both with different op/values.

    ``pin_state_flips``
        Filter ids that changed between pinned and interactive.

    ``join_changes``
        Same shape as filter_changes but for joins (by id).

    All fields are deterministic — for fixed inputs the resulting diff is
    byte-identical (used by tests + audit).
    """
    added_aliases: dict[str, BasketItem] = field(default_factory=dict)
    removed_aliases: dict[str, BasketItem] = field(default_factory=dict)
    changed_aliases: dict[str, tuple[BasketItem, BasketItem]] = field(default_factory=dict)

    filter_changes: list[FilterChange] = field(default_factory=list)
    pin_state_flips: list[PinStateFlip] = field(default_factory=list)

    join_changes: list[JoinChange] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """``True`` when nothing differs — the build can short-circuit."""
        return not (
            self.added_aliases
            or self.removed_aliases
            or self.changed_aliases
            or self.filter_changes
            or self.pin_state_flips
            or self.join_changes
        )

    @property
    def affected_aliases(self) -> set[str]:
        """Aliases whose materialised DuckDB state must be refreshed (§8.e
        bullet 3). Removed aliases are dropped, added + changed ones are
        re-fetched, plus aliases targeted by a changed pinned filter via
        ``applies_to`` (the value pushdown might have shifted).

        Note this does *not* include aliases targeted by filters whose
        pin-state flipped without value change — those keep their existing
        view (only widget visibility changes; §10.e bullet 5)."""
        out: set[str] = set(self.added_aliases) | set(self.changed_aliases)
        for fc in self.filter_changes:
            for pf in (fc.old, fc.new):
                if pf is None:
                    continue
                for a in (pf.applies_to or []):
                    out.add(a)
        return out

    @property
    def has_breaking_changes(self) -> bool:
        """A *breaking* change is one that can leave existing blocks broken:
        a removed alias, or a removed pinned filter that a block binds. The
        full breaking analysis (block-level) lives in :mod:`scope.impact`;
        this is the scope-side hint."""
        if self.removed_aliases:
            return True
        for fc in self.filter_changes:
            if fc.new is None:           # outright removal
                return True
        return False


# ── Diff computation ────────────────────────────────────────────────────────

def _basket_eq(a: BasketItem, b: BasketItem) -> bool:
    """Two basket items count as 'unchanged' iff their table_ref / derivation
    / projection / routing decision are identical. We ignore layout (UI
    coordinates) because that's not a fetch-driving field."""
    # model_dump excludes layout via __eq__? No — we compare structurally
    # over the fields that actually drive fetch and validation.
    if a.alias != b.alias:
        return False
    a_ref = a.table_ref.model_dump(by_alias=True) if a.table_ref else None
    b_ref = b.table_ref.model_dump(by_alias=True) if b.table_ref else None
    if a_ref != b_ref:
        return False
    a_der = a.derivation.model_dump(by_alias=True) if a.derivation else None
    b_der = b.derivation.model_dump(by_alias=True) if b.derivation else None
    if a_der != b_der:
        return False
    if a.projection.model_dump() != b.projection.model_dump():
        return False
    # Routing: only the *decision* matters for diff (estimated_bytes is
    # informational). decided_by isn't observable downstream either.
    if a.routing.decision != b.routing.decision:
        return False
    return True


def _filter_eq(a: PinnedFilter, b: PinnedFilter) -> bool:
    """Two pinned filters are equal iff their concept/op/value carriers and
    applies_to are equal. id is matched separately by the caller."""
    return (
        a.concept == b.concept
        and a.op == b.op
        and a.from_ == b.from_
        and a.to == b.to
        and a.values == b.values
        and a.value == b.value
        and sorted(a.applies_to or []) == sorted(b.applies_to or [])
    )


def _join_eq(a: Join, b: Join) -> bool:
    return (
        a.kind == b.kind
        and a.left.model_dump() == b.left.model_dump()
        and a.right.model_dump() == b.right.model_dump()
    )


def diff_scopes(old: ScopeContract | None, new: ScopeContract) -> ScopeDiff:
    """Return a ScopeDiff describing how ``new`` differs from ``old``.

    When ``old`` is ``None`` (no previous version), every basket item is
    treated as added and every filter / join as new. This is the first-build
    case where we still want the diff API to be uniform — callers should not
    have to branch.
    """
    if old is None:
        return ScopeDiff(
            added_aliases={b.alias: b for b in new.basket},
            filter_changes=[FilterChange(filter_id=pf.id, old=None, new=pf)
                            for pf in new.filters.pinned],
            join_changes=[JoinChange(join_id=j.id, old=None, new=j) for j in new.joins],
        )

    diff = ScopeDiff()

    # ── Basket ─────────────────────────────────────────────────────────────
    old_by = {b.alias: b for b in old.basket}
    new_by = {b.alias: b for b in new.basket}

    for alias, b in new_by.items():
        if alias not in old_by:
            diff.added_aliases[alias] = b
        elif not _basket_eq(old_by[alias], b):
            diff.changed_aliases[alias] = (old_by[alias], b)
    for alias, b in old_by.items():
        if alias not in new_by:
            diff.removed_aliases[alias] = b

    # ── Pinned filters ─────────────────────────────────────────────────────
    old_pf = {f.id: f for f in old.filters.pinned}
    new_pf = {f.id: f for f in new.filters.pinned}

    for fid, pf in new_pf.items():
        if fid not in old_pf:
            diff.filter_changes.append(FilterChange(fid, old=None, new=pf))
        elif not _filter_eq(old_pf[fid], pf):
            diff.filter_changes.append(FilterChange(fid, old=old_pf[fid], new=pf))
    for fid, pf in old_pf.items():
        if fid not in new_pf:
            diff.filter_changes.append(FilterChange(fid, old=pf, new=None))

    # ── Pin-state flips (concept moved between pinned and interactive) ────
    # Schema enforces distinct `pf_*` / `if_*` id prefixes, so we match by
    # concept instead. A "flip" is: one side has a concept that the other
    # side no longer has in the *other* state — i.e. a pinned concept now
    # appears only on the interactive side (or vice versa).
    old_pinned_concepts = {f.concept for f in old.filters.pinned}
    new_pinned_concepts = {f.concept for f in new.filters.pinned}
    old_interactive_concepts = {f.concept for f in old.filters.interactive}
    new_interactive_concepts = {f.concept for f in new.filters.interactive}

    # pinned → interactive: was pinned in old, isn't pinned in new, IS
    # interactive in new (but wasn't interactive in old).
    for f in new.filters.interactive:
        if (f.concept in old_pinned_concepts
                and f.concept not in new_pinned_concepts
                and f.concept not in old_interactive_concepts):
            diff.pin_state_flips.append(PinStateFlip(f.id, "pinned_to_interactive"))
    # interactive → pinned: symmetric.
    for f in new.filters.pinned:
        if (f.concept in old_interactive_concepts
                and f.concept not in new_interactive_concepts
                and f.concept not in old_pinned_concepts):
            diff.pin_state_flips.append(PinStateFlip(f.id, "interactive_to_pinned"))

    # ── Joins ──────────────────────────────────────────────────────────────
    old_jn = {j.id: j for j in old.joins}
    new_jn = {j.id: j for j in new.joins}
    for jid, j in new_jn.items():
        if jid not in old_jn:
            diff.join_changes.append(JoinChange(jid, old=None, new=j))
        elif not _join_eq(old_jn[jid], j):
            diff.join_changes.append(JoinChange(jid, old=old_jn[jid], new=j))
    for jid, j in old_jn.items():
        if jid not in new_jn:
            diff.join_changes.append(JoinChange(jid, old=j, new=None))

    return diff


# ── Diff serialisation for the UI ──────────────────────────────────────────

def serialise_diff(diff: ScopeDiff) -> dict:
    """Compact, JSON-safe dict the frontend warning UI can render directly.

    Shape (all keys optional — empty arrays are omitted):
    ``{
        "added": [alias, …],
        "removed": [alias, …],
        "changed": [alias, …],
        "filters": {"added": [id, …], "removed": [id, …], "modified": [id, …]},
        "pin_flips": [{"id": …, "direction": …}],
        "joins":   {"added": [id, …], "removed": [id, …], "modified": [id, …]},
        "breaking": bool,
    }``
    """
    out: dict = {}
    if diff.added_aliases:
        out["added"] = sorted(diff.added_aliases)
    if diff.removed_aliases:
        out["removed"] = sorted(diff.removed_aliases)
    if diff.changed_aliases:
        out["changed"] = sorted(diff.changed_aliases)

    f_added, f_removed, f_modified = [], [], []
    for fc in diff.filter_changes:
        if fc.old is None and fc.new is not None:
            f_added.append(fc.filter_id)
        elif fc.new is None and fc.old is not None:
            f_removed.append(fc.filter_id)
        elif fc.old is not None and fc.new is not None:
            f_modified.append(fc.filter_id)
    if f_added or f_removed or f_modified:
        out["filters"] = {}
        if f_added:    out["filters"]["added"] = sorted(f_added)
        if f_removed:  out["filters"]["removed"] = sorted(f_removed)
        if f_modified: out["filters"]["modified"] = sorted(f_modified)

    if diff.pin_state_flips:
        out["pin_flips"] = [{"id": p.filter_id, "direction": p.direction}
                            for p in diff.pin_state_flips]

    j_added, j_removed, j_modified = [], [], []
    for jc in diff.join_changes:
        if jc.old is None and jc.new is not None:
            j_added.append(jc.join_id)
        elif jc.new is None and jc.old is not None:
            j_removed.append(jc.join_id)
        elif jc.old is not None and jc.new is not None:
            j_modified.append(jc.join_id)
    if j_added or j_removed or j_modified:
        out["joins"] = {}
        if j_added:    out["joins"]["added"] = sorted(j_added)
        if j_removed:  out["joins"]["removed"] = sorted(j_removed)
        if j_modified: out["joins"]["modified"] = sorted(j_modified)

    out["breaking"] = diff.has_breaking_changes
    return out

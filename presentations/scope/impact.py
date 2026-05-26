"""Block-level impact analysis for the Hazırlık re-entry flow (spec §3.6 g).

Given a :class:`ScopeDiff` produced by :mod:`presentations.scope.diff` and a
dashboard manifest, walks every block in the manifest and reports which ones
are *breaking* (SQL references a removed alias, or a binding points at a
removed pinned filter) versus merely *warning* (a referenced alias's
projection or routing changed, a referenced pinned filter's value changed,
a binding's pin state flipped).

Pure function with no I/O. The warning UI consumes the returned list as-is.

Manifest shape: top-level ``blocks`` is a tree of section_header / leaf
blocks. Leaves may carry ``data_source.sql`` (with ``FROM alias / JOIN
alias`` references) and ``variable_bindings`` (Phase 6.5+: each variable
may bind ``from_scope_filter: <pf_or_if_id>``). We walk both signals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from presentations.scope.binding import referenced_aliases
from presentations.scope.diff import ScopeDiff


@dataclass(frozen=True)
class AffectedBlock:
    """One block whose execution semantics shift with the new scope.

    ``severity``:
        - ``"breaking"`` — block will likely error or render empty until
          fixed (referenced alias removed, bound pinned filter removed).
        - ``"warning"`` — block still renders but the data shifts
          (projection change, filter value change, pin-state flip).

    ``reasons`` is a short Turkish list shown to the user under the block
    in the warning UI; multiple changes can stack (e.g. "alias 'x' projection
    değişti" + "pinned filter 'pf_q4' değer değişti").
    """
    block_id: str
    block_title: str
    block_type: str
    severity: str
    reasons: list[str] = field(default_factory=list)


def _iter_leaf_blocks(blocks: list[dict]) -> Iterable[dict]:
    """Yield every leaf block in document order — section_header blocks are
    walked but not yielded; their children are. Mirrors the nested-section
    walk pattern used elsewhere in the codebase."""
    for b in blocks or []:
        children = b.get("children")
        if isinstance(children, list):
            yield from _iter_leaf_blocks(children)
        else:
            yield b


def _block_sql(block: dict) -> str:
    ds = block.get("data_source") or {}
    return ds.get("sql") or ds.get("original_sql") or ""


def _block_bindings(block: dict) -> dict[str, dict]:
    """Block variable_bindings as a dict-of-dicts (handles either schema)."""
    bindings = block.get("variable_bindings") or {}
    if not isinstance(bindings, dict):
        return {}
    out: dict[str, dict] = {}
    for name, b in bindings.items():
        if isinstance(b, dict):
            out[name] = b
    return out


def compute_affected_blocks(diff: ScopeDiff, manifest: dict | None) -> list[AffectedBlock]:
    """Walk every leaf block and classify whether ``diff`` affects it.

    Returns the list in block-document order (so the warning UI groups
    breaking and warning entries consistently per call)."""
    if manifest is None or not diff or diff.is_empty:
        return []

    blocks = manifest.get("blocks") or []
    removed_aliases = set(diff.removed_aliases)
    changed_aliases = set(diff.changed_aliases)

    removed_filter_ids: set[str] = set()
    modified_filter_ids: set[str] = set()
    for fc in diff.filter_changes:
        if fc.new is None:
            removed_filter_ids.add(fc.filter_id)
        elif fc.old is not None:
            modified_filter_ids.add(fc.filter_id)
    flip_ids = {p.filter_id for p in diff.pin_state_flips}

    out: list[AffectedBlock] = []
    for b in _iter_leaf_blocks(blocks):
        # SQL alias references — lower-cased by ``referenced_aliases``.
        sql_aliases = referenced_aliases(_block_sql(b))

        broken_alias = removed_aliases & sql_aliases
        shifted_alias = changed_aliases & sql_aliases

        # Bound scope filters — Phase 6.5 + Phase 8 ``from_scope_filter``.
        broken_filters: set[str] = set()
        shifted_filters: set[str] = set()
        flipped_filters: set[str] = set()
        for var_name, binding in _block_bindings(b).items():
            fid = binding.get("from_scope_filter")
            if not fid:
                continue
            if fid in removed_filter_ids:
                broken_filters.add(fid)
            elif fid in modified_filter_ids:
                shifted_filters.add(fid)
            if fid in flip_ids:
                flipped_filters.add(fid)

        reasons: list[str] = []
        severity = None

        for a in sorted(broken_alias):
            reasons.append(f"Basket'ten silinen tablo'ya bağlı: '{a}'")
            severity = "breaking"
        for f in sorted(broken_filters):
            reasons.append(f"Silinen pinned filter'a bind: '{f}'")
            severity = "breaking"

        # Warnings stack on top — but if there's already a breaking, severity
        # stays breaking. Otherwise the strongest warning wins.
        for a in sorted(shifted_alias):
            reasons.append(f"Tablo değişti (projection/routing): '{a}'")
            severity = severity or "warning"
        for f in sorted(shifted_filters):
            reasons.append(f"Pinned filter değer değişti: '{f}'")
            severity = severity or "warning"
        for f in sorted(flipped_filters):
            reasons.append(f"Filter pin durumu değişti: '{f}'")
            severity = severity or "warning"

        if not reasons:
            continue
        out.append(AffectedBlock(
            block_id=b.get("id", "?"),
            block_title=b.get("title") or b.get("id") or "?",
            block_type=b.get("type") or "?",
            severity=severity or "warning",
            reasons=reasons,
        ))
    return out


def serialise_affected(blocks: list[AffectedBlock]) -> list[dict]:
    """JSON-safe dicts the frontend modal renders directly."""
    return [{
        "block_id": b.block_id,
        "block_title": b.block_title,
        "block_type": b.block_type,
        "severity": b.severity,
        "reasons": b.reasons,
    } for b in blocks]


def summarise(blocks: list[AffectedBlock]) -> dict:
    """Counts for the warning modal banner: ``{breaking, warning, total}``."""
    breaking = sum(1 for b in blocks if b.severity == "breaking")
    warning = sum(1 for b in blocks if b.severity == "warning")
    return {"breaking": breaking, "warning": warning, "total": breaking + warning}

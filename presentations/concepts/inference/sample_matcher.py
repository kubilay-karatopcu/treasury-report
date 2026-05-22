"""Sample-value pattern matching (Phase 7.c, spec §5.4).

For an enum/bucket concept with a canonical value alphabet, measure how many
of a column's distinct sample values resolve to a canonical code (directly or
via alias). A high overlap is strong evidence the column realizes the concept.

Returns the overlap ratio plus a ``pairs`` map (table value → canonical code)
for the values that resolved — used to choose ``identity`` (all values already
canonical) vs ``map`` (some needed alias translation).
"""
from __future__ import annotations

from typing import Any

from presentations.concepts.schema import Concept


def sample_overlap(profile_values: list[Any], concept: Concept) -> tuple[float, dict[str, str]]:
    """Return ``(ratio, pairs)`` of sample values that resolve to ``concept``.

    - ``ratio`` = resolved_distinct / total_distinct (0.0 when no samples or
      the concept has no canonical alphabet to match against).
    - ``pairs`` maps each resolved table value (as string) → canonical code.
    """
    if not concept.canonical_values:
        return 0.0, {}
    distinct: list[str] = []
    seen: set[str] = set()
    for v in profile_values:
        s = str(v)
        if s not in seen:
            seen.add(s)
            distinct.append(s)
    if not distinct:
        return 0.0, {}
    pairs: dict[str, str] = {}
    for s in distinct:
        canon = concept.resolve_value(s)
        if canon is not None:
            pairs[s] = canon
    return len(pairs) / len(distinct), pairs


def choose_transform(pairs: dict[str, str]) -> dict[str, Any]:
    """Pick ``identity`` vs ``map`` from the resolved pairs.

    identity when every table value already equals its canonical code;
    otherwise ``map`` carrying the (table value → canonical) pairs that differ
    plus the ones that match (so the compiler can translate all of them).
    """
    if all(table_val == canon for table_val, canon in pairs.items()):
        return {"kind": "identity"}
    return {"kind": "map", "pairs": dict(pairs)}

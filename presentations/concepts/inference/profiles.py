"""Build :class:`ColumnProfile`s from a Phase 6.5.b table doc (Phase 7.c.3).

The inference pipeline needs (name, dtype, sample_values) per column. The
existing table docs already carry exactly that — column ``type`` plus the
nightly ``distinct_values_sample`` cron output — so onboarding a table for
concept inference reuses the catalog the data team already maintains.
"""
from __future__ import annotations

from typing import Any

from presentations.concepts.inference.types import ColumnProfile


def profiles_from_table_doc(doc: Any) -> list[ColumnProfile]:
    """Map a :class:`presentations.table_docs.schema.TableDoc` to ColumnProfiles."""
    out: list[ColumnProfile] = []
    columns = getattr(doc, "columns", None) or {}
    for name, col in columns.items():
        dtype = getattr(col, "type", "") or ""
        samples = getattr(col, "distinct_values_sample", None) or []
        out.append(ColumnProfile(name=name, dtype=dtype, sample_values=list(samples)))
    return out


def profiles_from_dict(columns: dict[str, dict]) -> list[ColumnProfile]:
    """Map a raw ``{col: {type, distinct_values_sample}}`` dict to ColumnProfiles."""
    out: list[ColumnProfile] = []
    for name, meta in (columns or {}).items():
        if not isinstance(meta, dict):
            continue
        out.append(ColumnProfile(
            name=name,
            dtype=meta.get("type", "") or "",
            sample_values=list(meta.get("distinct_values_sample") or []),
        ))
    return out

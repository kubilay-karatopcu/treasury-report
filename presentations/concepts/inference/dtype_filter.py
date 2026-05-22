"""Dtype compatibility (Phase 7.c, spec §5.3).

Eliminates impossible (concept, transform) combinations given a column's
Oracle dtype. This is a *filter*, not a producer — it narrows what the regex
and sample stages may propose. Examples:

- ``currency`` (enum) needs a CHAR/VARCHAR column ≤ a few chars.
- time concepts need DATE / TIMESTAMP.
- ``maturity`` via ``bucket_from_range`` needs a NUMBER column;
  via ``identity`` / ``map`` needs CHAR/VARCHAR.
"""
from __future__ import annotations

import re


_CHAR_RE = re.compile(r"\b(N?VAR)?CHAR\d*\b|\bNVARCHAR2?\b|\bVARCHAR2?\b|\bCHAR\b|\bCLOB\b", re.I)
_NUM_RE = re.compile(r"\bNUMBER\b|\bINTEGER\b|\bINT\b|\bDECIMAL\b|\bNUMERIC\b|\bFLOAT\b|\bBINARY_(FLOAT|DOUBLE)\b", re.I)
_TEMPORAL_RE = re.compile(r"\bTIMESTAMP\b|\bDATE\b", re.I)


def dtype_family(dtype: str) -> str:
    """Coarse family: ``char`` | ``numeric`` | ``temporal`` | ``other``."""
    d = (dtype or "").strip()
    # TIMESTAMP/DATE first (DATE could otherwise be mistaken if a type embeds
    # the word, but Oracle types are clean enough).
    if _TEMPORAL_RE.search(d):
        return "temporal"
    if _NUM_RE.search(d):
        return "numeric"
    if _CHAR_RE.search(d):
        return "char"
    return "other"


def is_timestamp(dtype: str) -> bool:
    return bool(re.search(r"\bTIMESTAMP\b", dtype or "", re.I))


def candidate_transform_kinds(concept_type: str, dtype: str) -> list[str]:
    """Which transform kinds are dtype-plausible for this concept type.

    Returns an empty list when the column's dtype rules the concept out.
    """
    fam = dtype_family(dtype)
    if concept_type == "time":
        if fam != "temporal":
            return []
        # TIMESTAMP needs truncation to compare as a date; DATE is identity.
        return ["time_truncation"] if is_timestamp(dtype) else ["identity"]
    if concept_type == "bucket":
        if fam == "numeric":
            return ["bucket_from_range"]
        if fam == "char":
            return ["identity", "map"]
        return []
    if concept_type in ("enum", "scalar"):
        if fam == "char":
            return ["identity", "map"]
        # Some enums are numeric codes; allow identity on numeric too.
        if fam == "numeric":
            return ["identity"]
        return []
    return []

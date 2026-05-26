"""Dataclasses for the binding inference pipeline (Phase 7.c)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ColumnProfile:
    """Input to inference: a column's name, Oracle dtype, and sample values.

    ``sample_values`` come from Phase 6.5.b's nightly ``SELECT DISTINCT`` cron
    (reused here) — typically ≤ 50 distinct values.
    """
    name: str
    dtype: str
    sample_values: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class BindingProposal:
    """A proposed column→concept binding awaiting human review.

    ``transform`` is a plain dict (e.g. ``{"kind": "identity"}``) — JSON-
    serializable for the review queue. On approval it's validated into a
    :class:`presentations.concepts.schema.ColumnBinding` with
    ``confidence: human_verified``.
    """
    column: str
    concept: str
    transform: dict[str, Any]
    confidence: str         # inferred_regex | inferred_dtype | inferred_sample | llm_proposed
    score: float            # 0..1 — higher = more confident
    rationale: str
    stage: str              # which stage produced it: regex | sample | dtype | llm

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "concept": self.concept,
            "transform": self.transform,
            "confidence": self.confidence,
            "score": round(self.score, 3),
            "rationale": self.rationale,
            "stage": self.stage,
        }

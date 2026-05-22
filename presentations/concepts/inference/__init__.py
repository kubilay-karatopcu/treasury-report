"""Binding inference pipeline (Phase 7.c).

Proposes column→concept bindings for a new table via cheap deterministic
checks first (regex on names, dtype compatibility, sample-value overlap), with
an LLM fallback only for columns nothing else could place. Every proposal
carries a ``confidence`` provenance tag; only ``human_verified`` (set by an
operator in the review UI) ever reaches the filter compiler (locked §10.4).
"""
from presentations.concepts.inference.types import (  # noqa: F401
    ColumnProfile,
    BindingProposal,
)
from presentations.concepts.inference.pipeline import infer_bindings  # noqa: F401

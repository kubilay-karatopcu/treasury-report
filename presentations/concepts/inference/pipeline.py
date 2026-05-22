"""Inference orchestrator (Phase 7.c, spec §5.1).

Combines the cheap deterministic stages — regex name hints, dtype
compatibility, sample-value overlap — into ranked :class:`BindingProposal`s
per column. The LLM fallback (``llm_proposer``) is a separate stage the caller
invokes only for columns this pipeline leaves empty.

Producer stages (emit proposals):
  - sample : value overlap ≥ 0.4 against a concept's canonical alphabet.
  - regex  : column-name hint, dtype-compatible.
Dtype is a *filter* applied to both, never a producer on its own.

Confidence ranking (highest first): inferred_sample (high) > inferred_sample
(medium) > inferred_regex. None of these are usable by the compiler until an
operator promotes them to ``human_verified``.
"""
from __future__ import annotations

from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.inference.types import ColumnProfile, BindingProposal
from presentations.concepts.inference.regex_matcher import regex_candidates
from presentations.concepts.inference.dtype_filter import candidate_transform_kinds
from presentations.concepts.inference.sample_matcher import sample_overlap, choose_transform


_SAMPLE_STRONG = 0.8
_SAMPLE_WEAK = 0.4


def _proposals_for_column(
    profile: ColumnProfile, registry: ConceptRegistry
) -> list[BindingProposal]:
    name_hints = set(regex_candidates(profile.name))
    out: list[BindingProposal] = []

    for concept in registry.all_concepts():
        kinds = candidate_transform_kinds(concept.type, profile.dtype)
        if not kinds:
            continue  # dtype rules this concept out

        # ── sample stage (enum/bucket with a canonical alphabet) ──────────
        ratio, pairs = sample_overlap(profile.sample_values, concept)
        if ratio >= _SAMPLE_WEAK and pairs:
            transform = choose_transform(pairs)
            # Only emit a sample proposal if its transform kind is dtype-valid.
            if transform["kind"] in kinds:
                strong = ratio >= _SAMPLE_STRONG
                out.append(BindingProposal(
                    column=profile.name, concept=concept.id, transform=transform,
                    confidence="inferred_sample",
                    score=ratio,
                    rationale=(f"{len(pairs)}/{len(set(map(str, profile.sample_values)))} "
                               f"örnek değer {concept.id} kanonik alfabesine uyuyor"
                               + ("" if strong else " (zayıf — gözden geçir)")),
                    stage="sample",
                ))
                continue  # sample is the strongest signal; don't also regex it

        # ── regex stage (name hint, dtype-compatible) ────────────────────
        if concept.id in name_hints:
            kind = kinds[0]  # best dtype-implied transform
            transform: dict = {"kind": kind}
            if kind == "bucket_from_range":
                transform["ranges_concept"] = concept.id
            out.append(BindingProposal(
                column=profile.name, concept=concept.id, transform=transform,
                confidence="inferred_regex",
                score=0.5,
                rationale=f"kolon adı {profile.name!r} {concept.id} ismine benziyor",
                stage="regex",
            ))

    # Rank: higher score first, then concept id for determinism.
    out.sort(key=lambda p: (-p.score, p.concept))
    return out


def infer_bindings(
    profiles: list[ColumnProfile], registry: ConceptRegistry
) -> dict[str, list[BindingProposal]]:
    """Run the deterministic pipeline over a table's columns.

    Returns ``{column_name: [proposals ranked best-first]}``. Columns with no
    proposal map to an empty list (candidates for the LLM fallback in 7.c.2).
    """
    return {p.name: _proposals_for_column(p, registry) for p in profiles}


def columns_without_proposal(
    result: dict[str, list[BindingProposal]]
) -> list[str]:
    """Column names the deterministic stages couldn't place — LLM fallback set."""
    return [col for col, props in result.items() if not props]

"""LLM fallback proposer (Phase 7.c.2, spec §5.5).

Only invoked for columns the deterministic stages (regex / dtype / sample)
couldn't place. The LLM emits concept-level JSON — never SQL — which we parse,
validate against the registry + dtype rules, and surface as ``llm_proposed``
proposals in the review queue. LLM output is **never** auto-promoted (§10.4).

Decoupled from the concrete LLM client via a ``complete(system, user) -> str``
callable, so it's trivially testable and works with QwenClient / FakeLLM /
any OpenAI-compatible backend.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.inference.types import ColumnProfile, BindingProposal
from presentations.concepts.inference.dtype_filter import candidate_transform_kinds


log = logging.getLogger(__name__)

CompleteFn = Callable[[str, str], str]

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "binding_proposal.txt"
_VALID_KINDS = {"identity", "map", "lookup", "bucket_from_range", "time_truncation"}

# Load the prompt template once at import (CLAUDE.md: LLM strings live in files).
_PROMPT_TEMPLATE = _PROMPT_PATH.read_text(encoding="utf-8")


def _concepts_block(registry: ConceptRegistry) -> str:
    lines: list[str] = []
    for c in sorted(registry.all_concepts(), key=lambda x: x.id):
        codes = ", ".join(cv.code for cv in c.canonical_values[:12])
        tail = f" — değerler: {codes}" if codes else ""
        lines.append(f"- {c.id} ({c.type}): {c.name}{tail}")
    return "\n".join(lines)


def _columns_block(profiles: list[ColumnProfile]) -> str:
    lines: list[str] = []
    for p in profiles:
        sample = ", ".join(str(v) for v in p.sample_values[:10])
        lines.append(f"- {p.name} [{p.dtype}]"
                     + (f" örnekler: {sample}" if sample else ""))
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first balanced JSON object out of an LLM response.

    Tolerates code fences / prose around the JSON (GGUF wrappers love prose).
    Returns ``{}`` on failure rather than raising.
    """
    if not text:
        return {}
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    # Find the first balanced {...}.
    start = candidate.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def build_prompt(table: str, profiles: list[ColumnProfile], registry: ConceptRegistry) -> str:
    return (_PROMPT_TEMPLATE
            .replace("{concepts_block}", _concepts_block(registry))
            .replace("{columns_block}", _columns_block(profiles))
            .replace("{table}", table))


def _validate_proposal(
    col: str, raw: dict[str, Any], profile_by_name: dict[str, ColumnProfile],
    registry: ConceptRegistry,
) -> BindingProposal | None:
    concept_id = raw.get("concept")
    transform = raw.get("transform")
    if not concept_id or not isinstance(transform, dict):
        return None
    concept = registry.get(concept_id)
    if concept is None:
        return None  # hallucinated concept
    kind = transform.get("kind")
    if kind not in _VALID_KINDS:
        return None
    # dtype sanity — drop proposals the column's type can't support.
    profile = profile_by_name.get(col)
    if profile is not None and kind not in candidate_transform_kinds(concept.type, profile.dtype):
        return None
    try:
        score = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        score = 0.5
    score = max(0.0, min(1.0, score))
    return BindingProposal(
        column=col, concept=concept_id, transform=transform,
        confidence="llm_proposed", score=score,
        rationale=str(raw.get("rationale") or "LLM önerisi"),
        stage="llm",
    )


def propose_bindings_llm(
    table: str,
    profiles: list[ColumnProfile],
    registry: ConceptRegistry,
    complete_fn: CompleteFn,
) -> dict[str, list[BindingProposal]]:
    """Ask the LLM to place ``profiles`` (the deterministic-fallback columns).

    Returns ``{column: [proposals]}``. On any failure (no client, bad JSON,
    network error) returns an empty mapping — the fallback is best-effort.
    """
    if not profiles or complete_fn is None:
        return {}
    prompt = build_prompt(table, profiles, registry)
    try:
        raw_text = complete_fn("Sen bir veri kataloglama asistanısın.", prompt)
    except Exception:
        log.exception("LLM binding proposal call failed")
        return {}

    parsed = _extract_json(raw_text)
    cols = parsed.get("columns") if isinstance(parsed, dict) else None
    if not isinstance(cols, dict):
        return {}

    profile_by_name = {p.name: p for p in profiles}
    out: dict[str, list[BindingProposal]] = {}
    for col, proposals in cols.items():
        if not isinstance(proposals, list):
            continue
        valid: list[BindingProposal] = []
        for raw in proposals:
            if not isinstance(raw, dict):
                continue
            bp = _validate_proposal(col, raw, profile_by_name, registry)
            if bp is not None:
                valid.append(bp)
        if valid:
            out[col] = valid
    return out

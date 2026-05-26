"""Phase 9.c — discovery LLM client wrapper.

Sits on top of the existing :mod:`presentations.llm` clients
(QwenClient / FakeLLM). The wrapper enforces the §5.3 JSON output
contract and retries once with feedback when parsing fails. Tables the
LLM proposes that aren't in the current catalog are dropped silently
(and logged) — spec §5.5.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from presentations.catalog.models import TableEntry
from presentations.discovery.prompt import (
    DEFAULT_TOKEN_BUDGET,
    build_catalog_summary,
    build_system_prompt,
    build_user_message,
)


log = logging.getLogger(__name__)


class DiscoveryError(RuntimeError):
    """Raised when the LLM round trip ultimately fails (after the one
    retry). Callers should surface the user-facing message to the chat
    UI and bail."""


# ── Result shape ────────────────────────────────────────────────────────


@dataclass
class DiscoveryProposal:
    schema: str
    name: str
    rationale: str = ""
    match_score: float = 0.5
    suggested_companion: str | None = None

    @property
    def table_id(self) -> str:
        return f"{self.schema}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": self.schema,
            "name": self.name,
            "rationale": self.rationale,
            "match_score": self.match_score,
        }
        if self.suggested_companion:
            out["suggested_companion"] = self.suggested_companion
        return out


@dataclass
class DiscoveryResult:
    explanation: str = ""
    proposals: list[DiscoveryProposal] = field(default_factory=list)
    highlight_graph_node_ids: list[str] = field(default_factory=list)
    dropped_proposals: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "explanation": self.explanation,
            "proposals": [p.to_dict() for p in self.proposals],
            "highlight_graph_node_ids": list(self.highlight_graph_node_ids),
            "dropped_proposals": list(self.dropped_proposals),
        }


# ── Public entrypoint ───────────────────────────────────────────────────


def propose_tables(
    llm_client,
    *,
    user_request: str,
    catalog_entries: Iterable[TableEntry],
    current_basket: list[dict] | None = None,
    chat_history: list[dict] | None = None,
    user_department: str | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> DiscoveryResult:
    """Ask the LLM for table proposals.

    Two paths inside:
      - LLM clients with a native ``propose_tables`` method get it called
        directly (FakeLLM uses this for offline keyword matching).
      - Everyone else (QwenClient / OpenAI / Groq / OpenRouter) hits the
        generic ``complete(system, user)`` text-completion API and we parse
        + validate the JSON ourselves, with one retry on parse failure.

    Returns a :class:`DiscoveryResult`. Raises :class:`DiscoveryError`
    only when both attempts fail.
    """
    entries = list(catalog_entries)
    valid_ids = {e.table_id for e in entries}

    # Fast path — clients that know about discovery natively.
    if hasattr(llm_client, "propose_tables"):
        raw = llm_client.propose_tables(
            user_request=user_request,
            catalog_entries=entries,
            current_basket=current_basket or [],
            chat_history=chat_history or [],
            user_department=user_department,
        )
        return _shape_result(raw, valid_ids)

    # Slow path — generic completion → JSON parse → validate.
    system = build_system_prompt()
    catalog_summary = build_catalog_summary(
        entries,
        user_department=user_department,
        token_budget=token_budget,
    )
    user = build_user_message(
        user_request,
        catalog_summary=catalog_summary,
        current_basket=current_basket,
        chat_history=chat_history,
        user_department=user_department,
    )

    raw_text = _call_complete(llm_client, system, user)
    parsed = _parse_json(raw_text)

    if parsed.get("_invalid"):
        # One retry — feed the parse error back as a continuation.
        retry_user = (
            user
            + "\n\n## Önceki cevabın JSON parse edilemedi\n"
            + f"Hata: {parsed['_invalid']}\n"
            + "Lütfen SADECE geçerli bir JSON nesnesi döndür. Markdown, "
            "kod fence, prose yok."
        )
        try:
            raw_text = _call_complete(llm_client, system, retry_user)
            parsed = _parse_json(raw_text)
        except Exception as exc:
            log.exception("discovery: retry call failed")
            raise DiscoveryError("Bir sorun oldu, tekrar dener misiniz?") from exc

    if parsed.get("_invalid"):
        log.warning("discovery: JSON parse failed twice — %s", parsed["_invalid"])
        raise DiscoveryError("Bir sorun oldu, tekrar dener misiniz?")

    return _shape_result(parsed, valid_ids)


# ── Internals ───────────────────────────────────────────────────────────


def _call_complete(llm_client, system: str, user: str) -> str:
    """Call the underlying client's text-completion API.

    QwenClient exposes ``complete(system, user, max_tokens=..., temperature=...)``;
    we use it with conservative defaults. Any provider-side error becomes
    a :class:`DiscoveryError` so the route can return a graceful message.
    """
    if not hasattr(llm_client, "complete"):
        raise DiscoveryError("LLM client desteklenmiyor.")
    try:
        return llm_client.complete(system, user, max_tokens=1024, temperature=0.2)
    except Exception as exc:
        log.exception("discovery: complete() raised")
        raise DiscoveryError("LLM çağrısı başarısız oldu.") from exc


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction. Mirrors the pattern from
    :func:`presentations.llm._parse_scope_output`: strip code fences,
    locate the outermost ``{...}`` if prose surrounds it, return
    ``{"_invalid": "..."}`` on failure so the caller can retry."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = _FENCE_RE.sub("", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"_invalid": f"no JSON object in output (snippet: {raw[:200]!r})"}
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            return {"_invalid": str(exc)}


def _shape_result(raw: Any, valid_ids: set[str]) -> DiscoveryResult:
    """Validate the LLM's parsed output against the §5.3 contract and
    drop proposals that reference tables outside the catalog.

    Defensive parsing — the LLM (especially the FakeLLM stub) might dump
    bare lists, missing fields, or unexpected types. We coerce to the
    DiscoveryResult shape and skip anything that can't be made to fit.
    """
    if not isinstance(raw, dict):
        return DiscoveryResult(explanation="", proposals=[], highlight_graph_node_ids=[])

    explanation = str(raw.get("explanation") or "").strip()
    proposals_raw = raw.get("proposals") or []
    if not isinstance(proposals_raw, list):
        proposals_raw = []

    kept: list[DiscoveryProposal] = []
    dropped: list[dict[str, Any]] = []
    for p in proposals_raw:
        if not isinstance(p, dict):
            continue
        schema = str(p.get("schema") or "").strip()
        name = str(p.get("name") or "").strip()
        if not schema or not name:
            continue
        table_id = f"{schema}.{name}"
        if table_id not in valid_ids:
            log.info("discovery: dropping proposal not in catalog: %s", table_id)
            dropped.append({"schema": schema, "name": name, "reason": "not_in_catalog"})
            continue
        try:
            score = float(p.get("match_score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        kept.append(DiscoveryProposal(
            schema=schema,
            name=name,
            rationale=str(p.get("rationale") or "").strip(),
            match_score=max(0.0, min(1.0, score)),
            suggested_companion=(str(p.get("suggested_companion")).strip()
                                  if p.get("suggested_companion") else None),
        ))

    # Highlight ids: prefer the LLM's list when valid, otherwise derive
    # from the kept proposals so the contract always returns SOMETHING for
    # the frontend pulse effect.
    raw_highlights = raw.get("highlight_graph_node_ids") or []
    if not isinstance(raw_highlights, list):
        raw_highlights = []
    highlights = [str(h) for h in raw_highlights if isinstance(h, str) and h in valid_ids]
    if not highlights and kept:
        highlights = [p.table_id for p in kept]

    return DiscoveryResult(
        explanation=explanation,
        proposals=kept,
        highlight_graph_node_ids=highlights,
        dropped_proposals=dropped,
    )

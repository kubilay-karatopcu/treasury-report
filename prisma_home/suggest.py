"""Expert-suggestion logic (Phase 10D).

Two layers:

1. **Keyword scoring** (always runs, deterministic, offline-safe).
   Pulls candidate text from the manifest (title, description, block
   titles, basket table names) and scores each expert by keyword
   matches in its name + short_description + voice_examples.

2. **LLM refinement** (when ``LLM_CLIENT`` is reachable).
   Builds a compact prompt from the manifest summary + the candidate
   experts and asks Qwen to return top 1–3 with confidence + reason.
   Falls back to the keyword scores on any LLM error / parse failure.

The endpoint returns whichever layer succeeded last; the UI doesn't
have to know which path was taken — both yield the same shape.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


# ── Manifest summarisation ────────────────────────────────────────────────

def _iter_block_titles(manifest: dict) -> Iterable[str]:
    """Yield every block's title across nested sections + carousels."""
    for section in manifest.get("blocks") or []:
        if section.get("title"):
            yield section["title"]
        for child in section.get("children") or []:
            if child.get("title"):
                yield child["title"]
            for slide in child.get("children") or []:
                if slide.get("title"):
                    yield slide["title"]


def _iter_basket_tables(manifest: dict) -> Iterable[str]:
    for item in manifest.get("basket") or []:
        if isinstance(item, dict) and item.get("table"):
            yield item["table"]


def summarise_manifest(manifest: dict, title: str = "", description: str = "") -> dict:
    """Compact text view of a manifest for the LLM prompt / keyword match."""
    return {
        "title":           (title or manifest.get("meta", {}).get("title") or "").strip(),
        "description":     (description or "").strip(),
        "block_titles":    list(_iter_block_titles(manifest)),
        "basket_tables":   list(_iter_basket_tables(manifest)),
    }


# ── Keyword scoring (always runs) ─────────────────────────────────────────

_WORD_RE = re.compile(r"[a-zçğıöşü]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """Tokenise to lowercase Turkish words, drop very short tokens."""
    return {t.lower() for t in _WORD_RE.findall(text or "") if len(t) >= 3}


def _expert_corpus(expert) -> set[str]:
    """Tokens an expert is "about" — name + domain + description + voice + tags."""
    parts: list[str] = [
        expert.code or "",
        expert.name or "",
        expert.domain_label or "",
        expert.short_description or "",
    ]
    voice_examples = (expert.persona or {}).get("voice_examples") or []
    parts.extend(voice_examples)
    # Briefing-section semantic tags carry the strongest signal because
    # they're hand-curated tokens for what blocks the expert wants.
    for sec in (expert.briefing_recipe or {}).get("sections") or []:
        ff = sec.get("fill_from") or {}
        tag = ff.get("semantic_tag")
        if tag:
            parts.append(tag.replace("_", " "))
    return _tokens(" ".join(parts))


def keyword_score(summary: dict, experts) -> list[dict]:
    """Score each expert against the manifest summary.

    Returns sorted list of ``{id, code, confidence, reason}``. Confidence
    is normalised to [0, 1] by dividing the raw token-overlap count by the
    top scorer's count (so the best match is always 1.0). Reason is a
    short, deterministic English/Turkish blurb naming overlapping tokens.
    """
    text = " ".join([
        summary.get("title", ""),
        summary.get("description", ""),
        " ".join(summary.get("block_titles") or []),
        " ".join(summary.get("basket_tables") or []),
    ])
    manifest_tokens = _tokens(text)

    raw: list[tuple] = []
    for expert in experts:
        corpus = _expert_corpus(expert)
        hits = manifest_tokens & corpus
        if not hits:
            continue
        raw.append((expert, len(hits), sorted(hits)[:4]))

    if not raw:
        return []
    top_score = max(s for _, s, _ in raw)
    out: list[dict] = []
    for expert, score, hit_tokens in sorted(raw, key=lambda x: -x[1]):
        confidence = round(score / top_score, 2)
        reason = (
            f"{expert.domain_label} alanıyla örtüşen anahtar kelimeler: "
            + ", ".join(hit_tokens)
        )
        out.append({
            "id":         expert.id,
            "code":       expert.code,
            "confidence": confidence,
            "reason":     reason,
        })
    return out


# ── LLM-layer (optional refinement) ──────────────────────────────────────

_PROMPT_PATH = Path(__file__).parent / "prompts" / "suggest_experts.txt"


def _prompt_system(experts) -> str:
    """System prompt = static instructions + expert catalog summary."""
    base = _PROMPT_PATH.read_text(encoding="utf-8")
    catalog = "\n".join(
        f"- {e.id} ({e.code}) — {e.name}: {e.short_description}"
        for e in experts
    )
    return f"{base}\n{catalog}\n"


def _prompt_user(summary: dict) -> str:
    lines = [
        f"Başlık: {summary.get('title') or '(boş)'}",
        f"Açıklama: {summary.get('description') or '(boş)'}",
    ]
    if summary.get("block_titles"):
        lines.append("Blok başlıkları:")
        for t in summary["block_titles"]:
            lines.append(f"  - {t}")
    if summary.get("basket_tables"):
        lines.append("Veri kaynakları:")
        for t in summary["basket_tables"]:
            lines.append(f"  - {t}")
    return "\n".join(lines)


def _parse_llm_suggestions(raw: str) -> list[dict] | None:
    """Tolerant JSON-in-content extractor. Returns None on any failure
    (caller falls back to keyword scores)."""
    if not raw:
        return None
    # Strip code fences if the model added them. Handles both
    # ```json ... ``` and bare ``` ... ```. We don't care about the
    # outer fence — just extract the first {...} JSON object inside.
    text = raw.strip()
    if text.startswith("```"):
        # Drop opening fence + optional "json" language tag on the first line.
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Drop closing fence if present.
        if "```" in text:
            text = text.split("```", 1)[0]
    # Find the first {...} JSON object.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    suggestions = obj.get("suggestions")
    if not isinstance(suggestions, list):
        return None
    out: list[dict] = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        eid = s.get("id")
        if not isinstance(eid, str):
            continue
        try:
            conf = float(s.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        out.append({
            "id":         eid,
            "confidence": max(0.0, min(1.0, conf)),
            "reason":     str(s.get("reason") or "")[:280],
        })
    return out


def llm_refine(llm_client, summary: dict, experts) -> list[dict] | None:
    """Optional LLM pass. Returns None on any failure so the caller keeps
    the keyword-score baseline.

    The Qwen GGUF wrapper has flaky tool-calling so we send the prompt as
    a plain text completion and parse the JSON from the content (same
    pattern other PRISMA flows use)."""
    if llm_client is None:
        return None
    try:
        system = _prompt_system(experts)
        user = _prompt_user(summary)
        raw = llm_client.complete(system, user, max_tokens=512, temperature=0.1)
    except Exception as exc:
        log.info("suggest_experts: LLM call failed, using keyword scores: %s", exc)
        return None
    parsed = _parse_llm_suggestions(raw)
    if parsed is None:
        log.info("suggest_experts: LLM response not JSON-parseable, falling back")
        return None
    return parsed


# ── Public entry point ────────────────────────────────────────────────────

def suggest_experts(*, manifest: dict, title: str, description: str,
                    expert_store, llm_client=None) -> list[dict]:
    """Return ordered expert suggestions for a snapshot.

    Pipeline:
        1. Build a manifest summary.
        2. Score every visible expert by keyword overlap.
        3. (Optional) ask the LLM to refine ordering + reasons.
        4. Merge — keep the LLM order/reasons when present, fall back to
           keyword data for any expert the LLM didn't mention.

    Returns up to 5 suggestions, newest-confidence first. The UI marks
    confidence ≥ 0.7 as pre-checked and stars the top one.
    """
    if expert_store is None:
        return []
    experts = expert_store.list_all()
    if not experts:
        return []

    summary = summarise_manifest(manifest, title=title, description=description)
    baseline = keyword_score(summary, experts)
    llm_layer = llm_refine(llm_client, summary, experts)

    if llm_layer is None:
        return baseline[:5]

    # Merge: LLM order wins for any id it mentioned, keyword data fills the rest.
    # An LLM-mentioned id keeps the LLM confidence/reason; unknown LLM ids are dropped.
    valid_ids = {e.id for e in experts}
    id_to_code = {e.id: e.code for e in experts}
    by_id = {b["id"]: b for b in baseline}
    merged: list[dict] = []
    seen: set[str] = set()
    for s in llm_layer:
        if s["id"] not in valid_ids:
            continue
        merged.append({
            "id":         s["id"],
            "code":       id_to_code[s["id"]],
            "confidence": s["confidence"],
            "reason":     s["reason"],
        })
        seen.add(s["id"])
    # Append keyword-only matches the LLM missed, downgraded confidence.
    for b in baseline:
        if b["id"] in seen:
            continue
        merged.append({**b, "confidence": min(b["confidence"], 0.5)})
    return merged[:5]

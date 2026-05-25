"""Briefing engine — Phase 10E.

Replaces the static-markdown loader from Phase 10C with a recipe-driven
engine that:

1. Resolves each ``briefing_recipe.sections[i].fill_from`` to a concrete
   list of bound snapshots (the only ``kind`` we ship with at the moment;
   ``block``/``metric`` are spec-reserved for Phase 11+ when the library
   exposes blocks directly).
2. For sections with ``llm_paraphrase: true`` asks Qwen (or whichever
   LLM_CLIENT is configured) to write 2–4 sentences in the expert's
   voice, citing the snapshots. For ``false`` sections, renders a raw
   list.
3. Caches the rendered briefing per ``(expert_id, expert_version,
   sorted snapshot signature, ymd)`` so repeat hits within the cache TTL
   are free.

Falls back to ``StaticBriefing`` from Phase 10C whenever the LLM call
fails or returns garbage — the consumer experience stays the same; only
the prose quality degrades.

Engine outputs are intentionally close to the StaticBriefing shape so
the template doesn't have to know which path produced the data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .briefings import StaticBriefing, load_static_briefing

log = logging.getLogger(__name__)


# ── Output dataclasses ──────────────────────────────────────────────────────

@dataclass
class BriefingSection:
    id: str
    title: str
    content_html: str
    citations: list[dict] = field(default_factory=list)  # [{ref, title, kind}]
    kind: str = "snapshot"
    llm_paraphrase: bool = False


@dataclass
class BriefingResult:
    """What the template + JSON endpoint both consume.

    `sections` are the recipe-driven blocks (the new Phase 10E layer).
    `prose_html`, `metrics`, `related`, `sidebar_*` come straight from
    the StaticBriefing fallback so the right-rail sidebar + lead prose
    keep working when no sections render (e.g., expert with no bound
    snapshots).
    """
    expert_id: str
    sections: list[BriefingSection] = field(default_factory=list)
    prose_html: str = ""
    metrics: list[dict] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)
    sidebar_eyebrow: str = ""
    sidebar_subtitle: str = ""
    rendered_at: str = ""
    from_cache: bool = False
    cache_key: str = ""

    def to_dict(self) -> dict:
        return {
            "expert_id":         self.expert_id,
            "sections":          [
                {
                    "id":             s.id,
                    "title":          s.title,
                    "content_html":   s.content_html,
                    "citations":      s.citations,
                    "kind":           s.kind,
                    "llm_paraphrase": s.llm_paraphrase,
                }
                for s in self.sections
            ],
            "prose_html":        self.prose_html,
            "metrics":           self.metrics,
            "related":           self.related,
            "sidebar_eyebrow":   self.sidebar_eyebrow,
            "sidebar_subtitle":  self.sidebar_subtitle,
            "rendered_at":       self.rendered_at,
            "from_cache":        self.from_cache,
            "cache_key":         self.cache_key,
        }


# ── fill_from resolver ─────────────────────────────────────────────────────

def _resolve_fill_from(fill_from: dict, snapshots: list[dict]) -> list[dict]:
    """Filter the snapshot pool by the section's fill_from spec.

    Recognised keys (spec §5.6):
      - kind: "snapshot" | "block" | "metric"
      - role:        match against snapshot meta.semantic_role (when set)
      - semantic_tag: ditto
      - limit:       cap on returned items (default: 6)

    Unrecognised keys are silently ignored so the recipe can evolve
    forward-compatibly. ``block`` and ``metric`` kinds return empty for
    now (Phase 11 will fill them via the library catalog).
    """
    if not isinstance(fill_from, dict):
        return []
    kind = (fill_from.get("kind") or "snapshot").lower()
    if kind != "snapshot":
        return []
    limit = int(fill_from.get("limit") or 6)

    pool = list(snapshots)
    role = fill_from.get("role")
    if role:
        pool = [s for s in pool if s.get("semantic_role") == role]
    tag = fill_from.get("semantic_tag")
    if tag:
        pool = [s for s in pool if tag in (s.get("semantic_tags") or [])]
    # If no filters matched anything but the unfiltered pool is non-empty,
    # fall back to the full pool — better to show recent snapshots than an
    # empty section when the snapshot meta doesn't carry roles yet.
    if (role or tag) and not pool and snapshots:
        pool = list(snapshots)
    # Already sorted newest-first by find_snapshots_bound_to.
    return pool[:limit]


# ── Section renderers ───────────────────────────────────────────────────────

def _citation_of(snap: dict) -> dict:
    """Compact citation entry — embedded in BriefingSection.citations and
    referenced by sup numbers in the prose."""
    return {
        "ref":   snap.get("snapshot_id"),
        "title": snap.get("title") or snap.get("snapshot_id"),
        "kind":  "snapshot",
        "date":  (snap.get("created_at") or "")[:10],
    }


def _render_raw(items: list[dict], section: dict) -> str:
    """Non-LLM section — render a compact list of items.

    Used for sections that explicitly opt out of LLM paraphrasing
    (``llm_paraphrase: false``), like the bare "Kaynakça" citation list.
    """
    if not items:
        return '<p class="briefing-empty">Bu bölüm için henüz veri yok.</p>'
    lis = []
    for i, it in enumerate(items, 1):
        title = it.get("title") or it.get("snapshot_id") or "?"
        date = (it.get("created_at") or "")[:10]
        suffix = f' <span class="briefing-li-date">{date}</span>' if date else ""
        lis.append(f"<li>{title}{suffix}</li>")
    return f'<ul class="briefing-raw-list">{"".join(lis)}</ul>'


# ── LLM paraphrasing ───────────────────────────────────────────────────────

_PROMPT_PATH = Path(__file__).parent / "prompts" / "briefing_section.txt"
_ALLOWED_TAGS_RE = re.compile(r"<(?!/?(p|strong|em|sup)\b)[^>]+>", re.IGNORECASE)


def _strip_unsafe_html(html: str) -> str:
    """Whitelist tag stripping. The LLM is told to only emit
    <p>/<strong>/<em>/<sup>; this enforces it server-side.

    Anything outside that whitelist becomes plain text (the tag itself
    is removed but the inner content stays)."""
    return _ALLOWED_TAGS_RE.sub("", html or "")


def _build_persona_block(persona: dict) -> str:
    sys = (persona or {}).get("system_prompt", "").strip()
    examples = (persona or {}).get("voice_examples") or []
    parts = [sys]
    if examples:
        parts.append("Örnek ses tonu:")
        for ex in examples[:3]:
            parts.append(f"  - {ex}")
    return "\n".join(p for p in parts if p)


def _build_user_message(section: dict, items: list[dict]) -> str:
    section_title = section.get("title") or section.get("id") or "bölüm"
    lines = [
        f"Bölüm başlığı: {section_title}",
        f"Bölüm türü: {section.get('id') or '?'}",
    ]
    if items:
        lines.append("")
        lines.append("Dayanak snapshot'lar (sırayla 1'den başlayarak referans ver):")
        for i, it in enumerate(items, 1):
            title = it.get("title") or it.get("snapshot_id") or "?"
            desc = it.get("description") or ""
            date = (it.get("created_at") or "")[:10]
            lines.append(f"  {i}. [{it.get('snapshot_id')}] {title} ({date})")
            if desc:
                lines.append(f"     açıklama: {desc}")
    else:
        lines.append("")
        lines.append("Bu bölüm için bağlı snapshot yok.")
    return "\n".join(lines)


def _parse_llm_html(raw: str) -> Optional[str]:
    """Tolerant extractor. LLM is asked for {"html": "..."} JSON. Fall
    back to None on any parse failure so the engine can degrade gracefully."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if "```" in text:
            text = text.split("```", 1)[0]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    html = obj.get("html")
    if not isinstance(html, str) or not html.strip():
        return None
    return _strip_unsafe_html(html)


def _llm_paraphrase(llm_client, expert, section: dict, items: list[dict]) -> Optional[str]:
    if llm_client is None:
        return None
    try:
        base = _PROMPT_PATH.read_text(encoding="utf-8")
        persona_block = _build_persona_block(expert.persona)
        system = f"{base}\n{persona_block}\n"
        user = _build_user_message(section, items)
        raw = llm_client.complete(system, user, max_tokens=512, temperature=0.2)
    except Exception as exc:
        log.info("briefing: LLM call failed for %s/%s: %s",
                 expert.id, section.get("id"), exc)
        return None
    return _parse_llm_html(raw)


# ── Engine ──────────────────────────────────────────────────────────────────

class BriefingEngine:
    """Recipe-driven briefing renderer with an in-process cache.

    Cache layout: ``{ cache_key: (rendered_at_ts, BriefingResult) }``.
    Spec §5.6 calls for sha256 over the inputs; we keep the dict + the
    full key for testability. Eviction is lazy at write time (drops the
    oldest entry when the dict crosses ``max_entries``).

    Production note (Phase 12 spec §10.4): swap this dict for Redis when
    multi-pod consistency matters.
    """

    def __init__(self, *, expert_store, snapshot_store, llm_client=None,
                 max_entries: int = 256, default_ttl_seconds: int = 1800):
        self.expert_store = expert_store
        self.snapshot_store = snapshot_store
        self.llm_client = llm_client
        self.max_entries = max_entries
        self.default_ttl = default_ttl_seconds
        self._cache: dict[str, tuple[float, BriefingResult]] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def render_briefing(self, expert) -> BriefingResult:
        """Build (or fetch from cache) the briefing for one expert."""
        bound = self._bound_snapshots(expert.id)
        ttl = int(
            (expert.briefing_recipe or {}).get("cache_ttl_seconds")
            or self.default_ttl
        )
        key = self._cache_key(expert, bound)

        hit = self._cache.get(key)
        now = time.time()
        if hit is not None:
            ts, cached = hit
            if now - ts < ttl:
                # Don't mutate the cached object — return a shallow copy
                # with from_cache flipped to True for this response only.
                cached_copy = BriefingResult(**{**cached.__dict__, "from_cache": True})
                return cached_copy
            # Expired — fall through to re-render.
            self._cache.pop(key, None)

        result = self._render_fresh(expert, bound, cache_key=key)
        # Lazy eviction.
        if len(self._cache) >= self.max_entries:
            # Drop oldest by timestamp.
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[key] = (now, result)
        return result

    def invalidate(self, expert_id: Optional[str] = None) -> int:
        """Clear cache entries. With ``expert_id`` set, drops only that
        expert's keys; otherwise wipes everything."""
        if expert_id is None:
            n = len(self._cache)
            self._cache.clear()
            return n
        before = len(self._cache)
        prefix = f"{expert_id}:"
        self._cache = {
            k: v for k, v in self._cache.items() if not k.startswith(prefix)
        }
        return before - len(self._cache)

    # ── Internals ───────────────────────────────────────────────────────

    def _bound_snapshots(self, expert_id: str) -> list[dict]:
        if self.snapshot_store is None:
            return []
        try:
            all_meta = self.snapshot_store.list_all_meta()
        except AttributeError:
            return []
        return [m for m in all_meta if expert_id in (m.get("bound_experts") or [])]

    def _cache_key(self, expert, snapshots: list[dict]) -> str:
        """Spec §5.6: sha256 over (expert_id, expert_version, sorted
        snapshot signature, ymd). Snapshot signature includes
        manifest_version so editing a bound snapshot invalidates."""
        ymd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snap_sig = sorted(
            f"{s.get('snapshot_id')}@{s.get('manifest_version', '')}"
            for s in snapshots
        )
        material = json.dumps({
            "e":  expert.id,
            "ev": expert.version,
            "s":  snap_sig,
            "d":  ymd,
        }, sort_keys=True)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return f"{expert.id}:{digest}"

    def _render_fresh(self, expert, snapshots: list[dict], *,
                      cache_key: str) -> BriefingResult:
        # Pull the static markdown for the sidebar metadata + prose fallback.
        static = load_static_briefing(expert.id)

        sections: list[BriefingSection] = []
        for raw_section in (expert.briefing_recipe or {}).get("sections", []) or []:
            sec = self._render_section(expert, raw_section, snapshots)
            sections.append(sec)

        return BriefingResult(
            expert_id=expert.id,
            sections=sections,
            prose_html=static.prose_html,
            metrics=static.metrics,
            related=static.related,
            sidebar_eyebrow=static.sidebar_eyebrow,
            sidebar_subtitle=static.sidebar_subtitle,
            rendered_at=datetime.now(timezone.utc).isoformat(),
            from_cache=False,
            cache_key=cache_key,
        )

    def _render_section(self, expert, raw_section: dict,
                        snapshots: list[dict]) -> BriefingSection:
        items = _resolve_fill_from(raw_section.get("fill_from") or {}, snapshots)
        llm_paraphrase = bool(raw_section.get("llm_paraphrase"))

        if llm_paraphrase:
            html = _llm_paraphrase(self.llm_client, expert, raw_section, items)
            if not html:
                # Fall back to raw listing when LLM is unavailable / parse failed.
                html = _render_raw(items, raw_section)
        else:
            html = _render_raw(items, raw_section)

        return BriefingSection(
            id=str(raw_section.get("id") or "section"),
            title=str(raw_section.get("title") or ""),
            content_html=html,
            citations=[_citation_of(it) for it in items],
            kind=str((raw_section.get("fill_from") or {}).get("kind") or "snapshot"),
            llm_paraphrase=llm_paraphrase,
        )

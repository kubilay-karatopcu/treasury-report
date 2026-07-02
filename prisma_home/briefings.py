"""Static briefing loader + helpers (Phase 10C).

Briefings are markdown files with optional YAML frontmatter for the
right-rail sidebar metrics and related-experts panel. The structure
matches the prototype's `.expert-hero` two-column layout:

    ---
    metrics:
      - {k: "LCR",  v: "%118.4", delta: "−6.6pt", tone: neg}
      - {k: "NSFR", v: "%112.0", delta: "±0",      tone: neutral}
    related:
      - {code: DEP, name: "Mevduat", relation: "kaynak"}
      - {code: FND, name: "Fonlama", relation: "kaynak"}
    sidebar_eyebrow: "L2 · Domain Curator"
    sidebar_subtitle: "178 veri yaprağı · son sentez 06:42 TSI"
    ---

    Bu sabah likidite tarafı kırmızı bir uyarıyla açıldı...

Phase 10E replaces this static loader with the LLM-driven engine; the
returned shape is intentionally close to what the engine will emit so
the template doesn't need to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# Department → featured expert id for the consumer landing.
# Falls back to "liq" when a department isn't mapped (per spec §10C).
DEPT_TO_FEATURED_EXPERT: dict[str, str] = {
    "BİLANÇO YÖNETİMİ": "liq",
    "BİLANÇO ANALİZİ VE MEVDUAT YÖNETİMİ": "dep",
    "AKTİF PASİF YÖNETİMİ İŞTİRAKLER KOORDİNASYON": "nii",
    "AKTİF PASİF YÖNETİMİ VE FON TRANSFER FİYATLAMASI": "nii",
    "HAZİNE SATIŞ": "fnd",
    "FİNANSAL YAPAY ZEKA UYGULAMALARI": "liq",
    "MYU": "krd",
    "IBTECH-INF OPEN SOLUTIONS": "liq",
}

DEFAULT_FEATURED_EXPERT = "liq"


def featured_expert_for(user) -> str:
    """Return the expert id to highlight on the user's landing.

    Department lookup with `liq` fallback. Safe to call with a stub user
    that has no `department` attribute (DEV mode).
    """
    dept = getattr(user, "department", None) or ""
    return DEPT_TO_FEATURED_EXPERT.get(dept, DEFAULT_FEATURED_EXPERT)


@dataclass
class StaticBriefing:
    """Result of loading a static markdown briefing.

    Shape is chosen to be a strict subset of what Phase 10E's engine
    returns — template doesn't need to know which is in play.
    """
    expert_id: str
    prose_html: str
    metrics: list[dict] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)
    sidebar_eyebrow: str = ""
    sidebar_subtitle: str = ""
    citations: list[dict] = field(default_factory=list)  # populated by route from bound snapshots


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) from a markdown file.

    Accepts a `---\\n...\\n---\\n` block at the top. If absent, returns
    ({}, text) untouched.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    try:
        fm = yaml.safe_load(fm_block) or {}
    except Exception:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    return fm, body


def _md_to_html(md: str) -> str:
    """Very small markdown → HTML for static briefings.

    We deliberately don't pull in a markdown lib for this — the prose is
    paragraph + `**bold**` + `<sup>N</sup>` (left as literal). Keeps the
    bundle thin and avoids escaping surprises with the gold sup styling.
    """
    paragraphs = [p.strip() for p in md.split("\n\n") if p.strip()]
    out: list[str] = []
    for i, p in enumerate(paragraphs):
        # **bold** → <strong>...</strong>
        # Naive replacement is fine because the source is hand-authored.
        while "**" in p:
            p = p.replace("**", "<strong>", 1)
            if "**" in p:
                p = p.replace("**", "</strong>", 1)
            else:
                break
        # First paragraph gets the `lead` class to match the prototype.
        if i == 0:
            out.append(f'<p class="lead">{p}</p>')
        else:
            out.append(f'<p>{p}</p>')
    return "\n".join(out)


_BRIEFINGS_DIR = Path(__file__).resolve().parent.parent / "dev_data" / "briefings"


def load_static_briefing(expert_id: str, base_dir: Optional[Path] = None) -> StaticBriefing:
    """Load `<expert_id>_static.md` from briefings dir.

    Missing or unparseable files return an empty briefing — the expert
    detail page renders a "Brifing henüz hazır değil" placeholder rather
    than 500'ing.
    """
    base = Path(base_dir) if base_dir else _BRIEFINGS_DIR
    path = base / f"{expert_id}_static.md"
    if not path.exists():
        return StaticBriefing(
            expert_id=expert_id,
            prose_html='<p class="lead">Brifing henüz hazır değil.</p>',
        )
    text = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    return StaticBriefing(
        expert_id=expert_id,
        prose_html=_md_to_html(body),
        metrics=list(fm.get("metrics") or []),
        related=list(fm.get("related") or []),
        sidebar_eyebrow=str(fm.get("sidebar_eyebrow") or ""),
        sidebar_subtitle=str(fm.get("sidebar_subtitle") or ""),
    )


def find_snapshots_bound_to(snapshot_store, expert_id: str) -> list[dict]:
    """Cross-owner scan of snapshots whose meta.bound_experts contains expert_id.

    Returns meta dicts, newest first. Empty list if the store has no
    matching snapshots — the citation grid then renders the empty-state.
    """
    try:
        all_meta = snapshot_store.list_all_meta()
    except AttributeError:
        # Pre-Phase-10C store backends — defensive fallback.
        return []
    return [m for m in all_meta if expert_id in (m.get("bound_experts") or [])]

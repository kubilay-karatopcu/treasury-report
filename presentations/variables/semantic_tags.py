"""Semantic tag allow-list — Phase 6.5 seed of the Phase 7 concept registry.

Variables carry a mandatory ``semantic_tag``. In Phase 6.5 this was bounded by
the frozen ``SEMANTIC_TAGS_V0`` set. In Phase 7 (7.a) the concept registry
becomes the source of truth: when an active registry is installed via
:func:`set_active_registry`, validity + the UI tag list are sourced from it,
with ``SEMANTIC_TAGS_V0`` retained as a **baseline floor**.

The registry is always a *superset* of the v0 baseline (the migration emits a
concept per v0 tag), so every block authored under Phase 6.5 keeps validating
— zero regression. If no registry is installed (bare imports, some tests), the
functions fall back to the frozen set exactly as before.

``other`` is the explicit escape hatch and is flagged in the editor UI.
"""
from __future__ import annotations

from typing import Any


# Active concept registry — installed by the Flask app at startup via
# set_active_registry(). Duck-typed: needs all_ids() + all_concepts(). Kept as
# a module global (not imported) so this module stays free of a hard
# dependency on presentations.concepts (avoids an import cycle).
_active_registry: Any | None = None


def set_active_registry(registry: Any | None) -> None:
    """Install (or clear) the concept registry backing tag validity + the UI list."""
    global _active_registry
    _active_registry = registry


def _registry_ids() -> set[str] | None:
    if _active_registry is None:
        return None
    try:
        return set(_active_registry.all_ids())
    except Exception:
        return None


# Frozen set so it cannot be mutated at runtime.
SEMANTIC_TAGS_V0: frozenset[str] = frozenset({
    "as_of_time",
    "trade_time",
    "value_time",
    "settle_time",
    "currency",
    "maturity",
    "tenor_bucket",
    "counterparty",
    "branch",
    "region",
    "product_group",
    "segment",
    "rating_bucket",
    "user_id",
    "deal_id",
    "instrument_type",
    "other",
})


# Human-readable Turkish labels shown in the block-editor variable form.
_LABELS_TR: dict[str, str] = {
    "as_of_time":      "Snapshot zamanı (as-of)",
    "trade_time":      "İşlem zamanı",
    "value_time":      "Valör zamanı",
    "settle_time":     "Takas zamanı",
    "currency":        "Para birimi",
    "maturity":        "Vade",
    "tenor_bucket":    "Vade dilimi",
    "counterparty":    "Karşı taraf",
    "branch":          "Şube",
    "region":          "Bölge",
    "product_group":   "Ürün grubu",
    "segment":         "Segment",
    "rating_bucket":   "Rating dilimi",
    "user_id":         "Kullanıcı kimliği",
    "deal_id":         "İşlem kimliği",
    "instrument_type": "Enstrüman tipi",
    "other":           "Diğer (kategorisiz)",
}


# Short Turkish descriptions, surfaced as helper text in the editor.
_DESCRIPTIONS_TR: dict[str, str] = {
    "as_of_time":      "Pozisyon snapshot tarihi (gün sonu raporlama).",
    "trade_time":      "İşlemin gerçekleştiği tarih/zaman.",
    "value_time":      "Valör (etkili) tarihi.",
    "settle_time":     "Takasın tamamlandığı tarih.",
    "currency":        "Para birimi (ISO 4217).",
    "maturity":        "Vade kovası (1W, 1M, 3M, vs.).",
    "tenor_bucket":    "Vade dilimi grubu.",
    "counterparty":    "Karşı taraf veya grubu.",
    "branch":          "Şube kodu / adı.",
    "region":          "Bölge.",
    "product_group":   "Ürün grubu (mevduat, kredi, vs.).",
    "segment":         "Müşteri segmenti.",
    "rating_bucket":   "Kredi rating dilimi.",
    "user_id":         "Kullanıcı kimliği (sicil vs.).",
    "deal_id":         "İşlem (deal) kimliği.",
    "instrument_type": "Enstrüman tipi (bond, swap, vs.).",
    "other":           "Kategorisiz — Phase 7 göçünde elle gözden geçirilecek.",
}


def is_valid_tag(s: str | None) -> bool:
    """True if ``s`` is a valid semantic tag.

    Valid = in the frozen v0 baseline OR in the active concept registry. The
    baseline is always accepted so pre-Phase-7 blocks never break, even if a
    registry is installed that (incorrectly) omits a tag.
    """
    if not isinstance(s, str) or not s.strip():
        return False
    if s in SEMANTIC_TAGS_V0:
        return True
    ids = _registry_ids()
    return bool(ids and s in ids)


def describe_tag(s: str) -> str:
    """Return the Turkish label for a tag, or the tag itself if unknown."""
    return _LABELS_TR.get(s, s)


def tag_description(s: str) -> str:
    """Return the Turkish helper text for a tag, or empty string if unknown."""
    return _DESCRIPTIONS_TR.get(s, "")


def all_tags() -> list[dict[str, str]]:
    """Materialize the tag allow-list for UI dropdowns.

    Union of the v0 baseline and the active registry's concept ids — the
    result is always a superset of v0. When a concept is present in the
    registry its ``name`` / ``description`` win over the static Turkish
    label tables; otherwise the static tables are used. ``other`` is always
    rendered last (escape hatch; the editor flags it).
    """
    reg_by_id: dict[str, Any] = {}
    if _active_registry is not None:
        try:
            reg_by_id = {c.id: c for c in _active_registry.all_concepts()}
        except Exception:
            reg_by_id = {}

    ids = set(SEMANTIC_TAGS_V0) | set(reg_by_id.keys())

    def label_for(t: str) -> str:
        c = reg_by_id.get(t)
        if c is not None and getattr(c, "name", None):
            return c.name
        return describe_tag(t)

    def desc_for(t: str) -> str:
        c = reg_by_id.get(t)
        if c is not None and getattr(c, "description", None):
            return c.description
        return tag_description(t)

    ordered = sorted(t for t in ids if t != "other")
    if "other" in ids:
        ordered.append("other")
    return [
        {"tag": t, "label": label_for(t), "description": desc_for(t)}
        for t in ordered
    ]

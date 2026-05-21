"""Semantic tag allow-list — Phase 6.5 seed of the future concept registry.

Variables carry a mandatory ``semantic_tag`` drawn from ``SEMANTIC_TAGS_V0``.
When Phase 7 lands, this allow-list will be replaced by a YAML-backed concept
registry; the migration script keys off the same tag string. **Adding a new
tag here requires a code change + PR** — users cannot mint tags in v0.

The set is intentionally small and Treasury-flavoured. ``other`` is the
explicit escape hatch and is flagged in the editor UI.
"""
from __future__ import annotations


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
    """True if ``s`` is exactly one of the allow-listed semantic tags."""
    return isinstance(s, str) and s in SEMANTIC_TAGS_V0


def describe_tag(s: str) -> str:
    """Return the Turkish label for a tag, or the tag itself if unknown."""
    return _LABELS_TR.get(s, s)


def tag_description(s: str) -> str:
    """Return the Turkish helper text for a tag, or empty string if unknown."""
    return _DESCRIPTIONS_TR.get(s, "")


def all_tags() -> list[dict[str, str]]:
    """Materialize the allow-list for UI dropdowns.

    Returns a list ordered to keep ``other`` last (it is the escape hatch and
    the editor highlights it with a yellow warning).
    """
    ordered = sorted(t for t in SEMANTIC_TAGS_V0 if t != "other")
    if "other" in SEMANTIC_TAGS_V0:
        ordered.append("other")
    return [
        {"tag": t, "label": describe_tag(t), "description": tag_description(t)}
        for t in ordered
    ]

"""Atölye sidebar registry (Phase 10A).

Single source of truth for the producer sidebar's item list. Each route
calls `get_sidebar(active_key=...)` before rendering, so the active item
is highlighted and badge counts (Phase 10B/11) can be injected without
template churn.
"""
from __future__ import annotations


SIDEBAR_GROUPS = [
    {
        "label": "Pipeline",
        "items": [
            # Pipeline items now land on a per-phase checkpoint listing.
            # Each page shows the user's saved workshops for that phase plus
            # a "Yeniden Başla" CTA that drops them back into Keşif. The
            # actual editor URLs (atolye_kesif / hazirlik / editor) are
            # reached by clicking a checkpoint card or the CTA.
            {"key": "atolye",   "num": "A·0", "label": "Atölye Ana", "route": "prisma_home.atolye_home"},
            {"key": "kesif",    "num": "A·1", "label": "Keşif",     "route": "presentations.pipeline_kesif"},
            {"key": "hazirlik", "num": "A·2", "label": "Hazırlık",  "route": "presentations.pipeline_hazirlik"},
            {"key": "sunum",    "num": "A·3", "label": "Sunum",     "route": "presentations.pipeline_sunum"},
        ],
    },
    {
        "label": "Kütüphane",
        "items": [
            # Each library entry now leads to a list/search/filter screen
            # where every card opens its own documentation / edit screen.
            # No more pipeline redirects from a library card.
            {"key": "tablolar",   "num": "⊟", "label": "Tablolar",   "route": "presentations.atolye_tablolar"},
            {"key": "konseptler", "num": "◈", "label": "Konseptler", "route": "presentations.atolye_konseptler"},
            {"key": "bloklar",    "num": "▦", "label": "Bloklar",    "route": "presentations.atolye_bloklar"},
            {"key": "uzmanlar",  "num": "✦", "label": "Uzmanlar",  "route": "presentations.atolye_uzmanlar"},
            {"key": "taslaklar", "num": "◇", "label": "Şablonlar", "route": "presentations.atolye_taslaklar"},
            {"key": "sablonlar", "num": "∎", "label": "Süreçler",  "route": "presentations.atolye_sablonlar"},
        ],
    },
    {
        "label": "Meta",
        "items": [
            {"key": "surec", "num": "∿", "label": "Süreç İzleme", "route": "presentations.atolye_surecler"},
        ],
    },
]


def get_sidebar(active_key: str | None, counts: dict | None = None) -> list:
    """Return sidebar groups with active flag + injected badge counts.

    Phase 10A: badge counts default to None (templates render no chip).
    Phase 10B/11 will populate `counts` with live numbers.
    """
    counts = counts or {}
    result = []
    for group in SIDEBAR_GROUPS:
        items = [
            {
                **item,
                "active": item["key"] == active_key,
                "badge": counts.get(item["key"]),
            }
            for item in group["items"]
        ]
        result.append({"label": group["label"], "items": items})
    return result

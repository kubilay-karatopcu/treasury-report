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
            # Phase 11.wire: pipeline items point directly at the existing
            # presentations.* endpoints — no prisma_home redirect step. The
            # consumer-side stubs that used to live here are gone.
            {"key": "atolye",   "num": "A·0", "label": "Atölye Ana", "route": "prisma_home.atolye_home"},
            {"key": "kesif",    "num": "A·1", "label": "Keşif",     "route": "presentations.atolye_kesif"},
            {"key": "hazirlik", "num": "A·2", "label": "Hazırlık",  "route": "presentations.hazirlik_new"},
            {"key": "sunum",    "num": "A·3", "label": "Sunum",     "route": "presentations.list_presentations"},
        ],
    },
    {
        "label": "Kütüphane",
        "items": [
            # Phase 11.lib: tablolar + bloklar.
            # Phase 12.workshops: split the old "Şablonlar" entry in two —
            #  - "Şablonlar"  → in-progress workshops (Keşif/Hazırlık/Sunum)
            #  - "Süreçler"   → completed snapshots (was the old Şablonlar)
            # URLs kept stable: the legacy /atolye/sablonlar still serves the
            # snapshot list (now labeled "Süreçler") so external bookmarks
            # don't break. The new /atolye/taslaklar serves in-progress
            # workshops under the "Şablonlar" label.
            {"key": "tablolar",  "num": "⊟", "label": "Tablolar",  "route": "presentations.atolye_tablolar"},
            {"key": "bloklar",   "num": "▦", "label": "Bloklar",   "route": "presentations.atolye_bloklar"},
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

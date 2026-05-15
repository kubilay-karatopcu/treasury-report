"""PRISMA shell navigation — JSON-driven groups + active group resolution.

Loads `prisma_nav.json` once at import. Flask context processor injects
`prisma_groups`, `active_group`, `prisma_icons` into every template that
extends `_prisma_base.html`.

To change menu structure or URLs: edit `prisma_nav.json`. Restart Flask.
"""
import json
from pathlib import Path

from flask import request

from prisma_icons import ICONS

_NAV_JSON_PATH = Path(__file__).parent / "prisma_nav.json"
_GROUPS = json.loads(_NAV_JSON_PATH.read_text(encoding="utf-8"))

# URL → (group key, item label) index, built once.
_URL_TO_ITEM: dict[str, tuple[str, str]] = {}
for _g in _GROUPS:
    for _section in _g.get("sections", []):
        for _item in _section.get("items", []):
            _url = _item.get("url", "")
            if _url and _url != "#":
                _URL_TO_ITEM[_url] = (_g["key"], _item.get("label", ""))


_HOME_PATHS = {"/", "/home", "/login"}


def _resolve_active(path: str) -> tuple[str, str]:
    """Return (group_key, item_label) for the current path.

    Strategy:
      - Home/login → ('', '') so no group is active (sidebar collapsed).
      - Exact match → URL's group + item.
      - Longest prefix match.
      - Fallback → ('', '') — unknown path, no group active.
    """
    if path in _HOME_PATHS:
        return "", ""
    if path in _URL_TO_ITEM:
        return _URL_TO_ITEM[path]
    candidates = [
        (url, gk, label) for url, (gk, label) in _URL_TO_ITEM.items()
        if url != "/" and path.startswith(url)
    ]
    if candidates:
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        return candidates[0][1], candidates[0][2]
    return "", ""


def inject() -> dict:
    """Flask context processor — call via `app.context_processor(inject)`."""
    gk, item_label = _resolve_active(request.path or "/")
    return {
        "prisma_groups": _GROUPS,
        "prisma_icons": ICONS,
        "active_group": gk,
        "active_item_label": item_label,
    }

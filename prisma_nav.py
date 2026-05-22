"""PRISMA shell navigation — JSON-driven groups + active group resolution.

Loads `prisma_nav.json` once at import. Flask context processor injects
`prisma_groups`, `active_group`, `prisma_icons` into every template that
extends `_prisma_base.html`.

To change menu structure or URLs: edit `prisma_nav.json`. Restart Flask.
"""
import copy
import json
import logging
from pathlib import Path

from flask import current_app, request
from flask_login import current_user

from prisma_icons import ICONS

log = logging.getLogger(__name__)

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


def _inject_dashboards(groups: list[dict]) -> list[dict]:
    """R grubuna kullanıcının görebildiği Ekip Raporları'nı dinamik ekler.

    Performance not: her request'te S3 list ediyor — şu an küçük ölçek için
    kabul edilebilir. Cache ya da per-owner index sonra eklenebilir.
    """
    try:
        if not current_user.is_authenticated:
            return groups
        store = current_app.config.get("DASHBOARD_STORE")
        if store is None:
            return groups
        items = store.list_visible(
            user_sicil=current_user.sicil,
            user_department=getattr(current_user, "department", "") or "",
        )
    except Exception as exc:
        log.warning("prisma_nav: dashboard injection failed: %s", exc)
        return groups

    if not items:
        return groups

    new_groups = copy.deepcopy(groups)
    for g in new_groups:
        if g.get("key") != "R":
            continue
        section = {
            "title": "Ekip Raporları",
            "items": [
                {
                    "label": (m.get("name") or "(adsız)"),
                    "url":   f"/presentations/dashboard/{m['dashboard_id']}",
                    "icon":  "doc",
                }
                for m in items
            ],
        }
        g.setdefault("sections", []).append(section)
        break
    return new_groups


def inject() -> dict:
    """Flask context processor — call via `app.context_processor(inject)`."""
    gk, item_label = _resolve_active(request.path or "/")
    groups = _inject_dashboards(_GROUPS)

    # Eğer mevcut URL bir dashboard ise breadcrumb'ı dashboard adı yap
    if not item_label and request.path and request.path.startswith("/presentations/dashboard/"):
        did = request.path.rsplit("/", 1)[-1]
        for g in groups:
            if g.get("key") != "R":
                continue
            for sec in g.get("sections", []):
                if sec.get("title") != "Ekip Raporları":
                    continue
                for it in sec.get("items", []):
                    if it.get("url", "").endswith(f"/{did}"):
                        item_label = it.get("label", "")
                        gk = "R"
                        break

    return {
        "prisma_groups": groups,
        "prisma_icons": ICONS,
        "active_group": gk,
        "active_item_label": item_label,
    }

"""Süreç kayıt defteri — uzman ``bound_content.processes`` id'lerini sayfalara çözer.

Faz P0 (docs/DASHBOARD_ADAPTATION_PLAN.md §6.5): manuel panolar ayrı bir
"Panolar" menüsü yerine uzmanın "Süreçler" bölümünde listelenir. Uzman
YAML'ında yalnız string id durur (Atölye form round-trip'i string listesi
bekler — routes_library._form_to_expert_dict); id → sayfa eşlemesi burada,
kodda yaşar. Modül izolasyonu: mevduat_panel import EDİLMEZ — endpoint adı
string olarak çözülür, modül kayıtlı değilse süreç sessizce gizlenir
(landing'in eski MEVDUAT_PANEL_ENABLED korumalı kart deseniyle aynı sözleşme).
"""
from __future__ import annotations

import logging

from flask import current_app, url_for
from werkzeug.routing import BuildError

log = logging.getLogger(__name__)

#: id → süreç tanımı. ``page`` mevduat panel SPA'sının ?page= deep-link'i
#: (mevduat_panel.js boot'u sidebar'daki data-page id'lerine karşı doğrular).
PROCESS_REGISTRY: dict[str, dict] = {
    "mevduat.maliyet": {
        "label": "Outstanding Cost Analysis",
        "desc": "Monthly averages & daily evolution · bubble · rate heatmap",
        "endpoint": "mevduat_panel.index", "page": "cost-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.bakiye": {
        "label": "Outstanding Balance Analysis",
        "desc": "Balance bridge · balance/customer heatmap · composition",
        "endpoint": "mevduat_panel.index", "page": "balance-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.vade": {
        "label": "Outstanding Tenor Analysis",
        "desc": "Tenor ladder · WAT · term-structure curve · swap hedge",
        "endpoint": "mevduat_panel.index", "page": "tenor-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.donusler": {
        "label": "Weekly Deposit Rollovers",
        "desc": "Rollover tables · segment breakdown · customer drill",
        "endpoint": "mevduat_panel.index", "page": "weekly-report",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.yeni_uretim": {
        "label": "New Production — Volume & Pricing",
        "desc": "Rate-volume heatmap · AUM combo · pricing curve",
        "endpoint": "mevduat_panel.index", "page": "np-volume-pricing",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.sektor": {
        "label": "Sector Comparison",
        "desc": "BDDK/TCMB rates · sector outstanding · mix attribution",
        "endpoint": "mevduat_panel.index", "page": "sector-comparison",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.bsc": {
        "label": "BSC Presentation",
        "desc": "Full-screen presentation mode · deposit & sector slide set",
        "endpoint": "mevduat_panel.index", "page": "bsc-presentation",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
}


def resolve_processes(process_ids: list[str] | None) -> list[dict]:
    """Uzmanın süreç id listesini render edilebilir kartlara çözer.

    Bilinmeyen id, kapalı config bayrağı veya kayıtlı olmayan endpoint →
    süreç listeden düşer (uzman sayfası hata vermez); bilinmeyen id ayrıca
    loglanır ki YAML yazım hatası sessiz kalmasın.
    """
    out: list[dict] = []
    for pid in process_ids or []:
        meta = PROCESS_REGISTRY.get(pid)
        if meta is None:
            log.warning("bilinmeyen süreç id'si atlandı: %r", pid)
            continue
        flag = meta.get("config_flag")
        if flag and not current_app.config.get(flag):
            continue
        try:
            kwargs = {"page": meta["page"]} if meta.get("page") else {}
            url = url_for(meta["endpoint"], **kwargs)
        except BuildError:
            continue
        out.append({
            "id": pid,
            "num": f"{len(out) + 1:02d}",
            "label": meta["label"],
            "desc": meta.get("desc", ""),
            "url": url,
        })
    return out

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
        "label": "Stok Maliyet Analizi",
        "desc": "Aylık ortalama & günlük evrim · bubble · oran heatmap",
        "endpoint": "mevduat_panel.index", "page": "cost-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.bakiye": {
        "label": "Stok Bakiye Analizi",
        "desc": "Bakiye köprüsü · bakiye/müşteri heatmap · kompozisyon",
        "endpoint": "mevduat_panel.index", "page": "balance-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.vade": {
        "label": "Stok Vade Analizi",
        "desc": "Vade merdiveni · WAT · vade yapısı eğrisi · swap hedge",
        "endpoint": "mevduat_panel.index", "page": "tenor-analysis",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.donusler": {
        "label": "Haftalık Mevduat Dönüşleri",
        "desc": "Dönüş tabloları · segment kırılımı · müşteri drill",
        "endpoint": "mevduat_panel.index", "page": "weekly-report",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.yeni_uretim": {
        "label": "Yeni Üretim — Hacim & Fiyatlama",
        "desc": "Oran-hacim heatmap · AUM combo · fiyatlama eğrisi",
        "endpoint": "mevduat_panel.index", "page": "np-volume-pricing",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.sektor": {
        "label": "Sektör Karşılaştırması",
        "desc": "BDDK/TCMB oranları · sektör stoku · mix attribution",
        "endpoint": "mevduat_panel.index", "page": "sector-comparison",
        "config_flag": "MEVDUAT_PANEL_ENABLED",
    },
    "mevduat.bsc": {
        "label": "BSC Sunumu",
        "desc": "Tam ekran sunum modu · mevduat & sektör slide seti",
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

"""Klasör bazlı sol menü — Faz R3.

Bir rapor açıkken soldaki menü YALNIZ o raporun bulunduğu klasörün (masa
``department_views[].topics[]`` başlığının) kardeşlerini gösterir. Menü klasörün
bir parçasıdır: klasör ağacı YAML'da değişince menü de değişir, ayrıca elle
bakım gerektiren statik nav listesi kalmaz.

İzolasyon sözleşmesi: ``mevduat_panel`` bu modülü İMPORT ETMEZ. ``app.py``
:func:`folder_menu`'yü ``app.config["FOLDER_MENU_PROVIDER"]`` üzerinden geçirir;
panel modülleri yalnız config'ten okur. Sağlayıcı yoksa menü çizilmez ve sayfa
eskisi gibi çalışır.

Uygulamalar (``Expert.applications``) topic taşımaz; kendi sanal klasörlerini
oluştururlar ("Uygulamalar") — böylece onların da menüsü çıkar.
"""
from __future__ import annotations

import logging

from flask import current_app, url_for

from prisma_home.expert_views import (
    legacy_view,
    resolve_applications,
    resolve_view,
)
from prisma_home.processes import PROCESS_REGISTRY, resolve_processes

log = logging.getLogger(__name__)

#: Uygulamaların sanal klasör başlığı (masadaki bölüm adıyla aynı).
APPLICATIONS_TITLE = "Uygulamalar"


def _page_of(pid: str) -> str | None:
    """Sürecin SPA deep-link sayfası (``?page=``). Yoksa None."""
    return (PROCESS_REGISTRY.get(pid) or {}).get("page")


def _endpoint_of(pid: str) -> str | None:
    return (PROCESS_REGISTRY.get(pid) or {}).get("endpoint")


def _expert_url(expert) -> str | None:
    """Uzmanın masa sayfası (R9 — 'Masaya dön' hedefi). Kayıtsızsa None."""
    try:
        return url_for("prisma_home.expert_detail", code=expert.code)
    except Exception:
        return None


def _menu(title: str, pids: list[str], active_pid: str | None,
          expert=None) -> dict | None:
    """Süreç id listesini menü sözlüğüne çevirir. Çözülebilen süreç yoksa None.

    ``resolve_processes`` kapalı modül bayrağı / kayıtsız endpoint durumunda
    süreci düşürür; menü de o girdiyi göstermez (ölü link üretilmez).
    """
    cards = resolve_processes(pids)
    if not cards:
        return None
    items = [{
        "id": c["id"],
        "label": c["label"],
        "url": c["url"],
        # SPA içi geçiş için: aynı endpoint'teki kardeşler sunucuya gitmeden
        # sayfa değiştirebilsin (mevduat_panel.js data-page ile çalışır).
        "page": _page_of(c["id"]),
        "endpoint": _endpoint_of(c["id"]),
        "active": c["id"] == active_pid,
    } for c in cards]
    return {"title": title, "items": items,
            "expert_url": _expert_url(expert) if expert is not None else None}


def _find_expert(process_id: str, department: str | None):
    """Süreci içeren, kullanıcının erişebildiği ilk uzman + çözülmüş bakışı.

    Dönüş: ``(expert, view, in_applications)`` ya da ``(None, None, False)``.
    Aynı süreç birden çok uzmanda olabilir; ilk eşleşme yeterlidir (menü
    bağlamı, yetki kararı değil — erişim zaten ``resolve_view`` ile süzülür).
    """
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        return None, None, False
    try:
        experts = store.list_all()
    except Exception:
        log.warning("folder_menu: uzman listesi okunamadı", exc_info=True)
        return None, None, False

    for expert in experts:
        r = resolve_view(expert, department)
        if not r["granted"]:
            continue
        view = r["view"] if not r["legacy"] else legacy_view(expert)
        if process_id in (view.get("process_ids") or []):
            return expert, view, False
        if process_id in resolve_applications(expert, department):
            return expert, view, True
    return None, None, False


def folder_menu(process_id: str, department: str | None = None) -> dict | None:
    """``process_id``'nin bulunduğu klasörün menüsü.

    Dönüş: ``{"title": str, "items": [{id,label,url,page,endpoint,active}]}``
    ya da klasör/uzman bulunamazsa None (çağıran menüyü çizmez).
    """
    if not process_id:
        return None
    expert, view, in_apps = _find_expert(process_id, department)
    if expert is None:
        return None

    if in_apps:
        return _menu(APPLICATIONS_TITLE,
                     resolve_applications(expert, department), process_id,
                     expert=expert)

    for topic in (view.get("topics") or []):
        if process_id in (topic.get("process_ids") or []):
            # Legacy uzmanda topic başlığı boş olabilir → uzman adına düş.
            title = (topic.get("title") or "").strip() or expert.name
            return _menu(title, list(topic["process_ids"]), process_id,
                         expert=expert)
    return None


def folder_menu_for_page(page: str, department: str | None = None) -> dict | None:
    """SPA deep-link sayfasından (``?page=``) klasör menüsü.

    ``mevduat_panel`` SPA'sı süreç id'si değil sayfa anahtarı taşır; registry
    üzerinden süreç id'sine çevrilir. Eşleşme yoksa None.
    """
    if not page:
        return None
    for pid, meta in PROCESS_REGISTRY.items():
        if meta.get("page") == page:
            return folder_menu(pid, department)
    return None

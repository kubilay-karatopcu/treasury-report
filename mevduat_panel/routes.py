"""mevduat_panel sayfa + API route'lari.

Faz A0: yalniz kabuk — SPA sayfasi render edilir, /api/* henuz stub'dur.
Faz A2+ endpoint'leri geldikce stub daralir (Flask'ta spesifik kural
catch-all'dan once eslesir, stub'a dokunmak gerekmez).
"""
from __future__ import annotations

import json
import os
import threading

from flask import Blueprint, Response, current_app, render_template, request, url_for
from flask_login import current_user, login_required

mevduat_panel_bp = Blueprint(
    "mevduat_panel",
    __name__,
    template_folder="templates",
    static_folder="static",
)

#: Statik dosya cache-busting'i. JS/CSS degistiginde artir
#: (kaynak repo Kirmizi Cizgi #7: tarayici agresif cache'ler).
MEVDUAT_VERSION = "p2.21"


def _masa_url() -> str:
    """Masa (PRISMA landing) URL'i — prisma_home kayitli degilse köke düş.

    Izolasyon geregi prisma_home'a import bagimliligi kurulmaz; endpoint
    yoksa modul yine calisir.
    """
    if "prisma_home.landing" in current_app.view_functions:
        return url_for("prisma_home.landing")
    return "/"


def _user_department() -> str:
    """Oturumdaki kullanicinin departmani (LDAP). Anonim/eksikse bos string."""
    try:
        return getattr(current_user, "department", None) or ""
    except Exception:
        return ""


def _folder_menu(config_key: str, arg: str | None) -> dict | None:
    """R3 — klasor bazli sol menu, config saglayicisi uzerinden.

    Izolasyon geregi prisma_home import EDILMEZ; saglayici app.py tarafindan
    config'e konur. Saglayici yoksa ya da patlarsa menu cizilmez (None) ve
    sayfa eskisi gibi calisir.
    """
    if not arg:
        return None
    provider = current_app.config.get(config_key)
    if provider is None:
        return None
    try:
        return provider(arg, _user_department())
    except Exception:
        current_app.logger.warning("folder_menu cozulemedi: %r", arg, exc_info=True)
        return None


def folder_menu_for_process(process_id: str | None) -> dict | None:
    """Surec id'sinden klasor menusu (PRISMA-native sayfalar)."""
    return _folder_menu("FOLDER_MENU_PROVIDER", process_id)


def folder_menu_for_page(page: str | None) -> dict | None:
    """SPA deep-link sayfasindan (``?page=``) klasor menusu."""
    return _folder_menu("FOLDER_MENU_FOR_PAGE_PROVIDER", page)


#: SPA acilisinda ?page= verilmezse gosterilen varsayilan sayfa (menu baglami).
_DEFAULT_SPA_PAGE = "cost-analysis"


@mevduat_panel_bp.route("/")
@login_required
def index() -> Response:
    # R3 — SPA'nin sol menusu de klasorden gelir. Sayfa anahtari ?page= ile
    # secilir; verilmezse varsayilan sayfanin klasoru gosterilir.
    page = (request.args.get("page") or "").strip() or _DEFAULT_SPA_PAGE
    html = render_template(
        "mevduat_panel/index.html",
        masa_url=_masa_url(),
        mevduat_version=MEVDUAT_VERSION,
        folder_menu=folder_menu_for_page(page),
    )
    resp = Response(html, mimetype="text/html")
    # Kaynak repo disiplini: sayfa SPA state'i tasir, bayat HTML/JS
    # "degisiklik gorunmuyor" sinifinin ana sebebi.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _refresh_authorized() -> bool:
    """Data-refresh yetkisi: token varsa token, yoksa oturum.

    ODH cronjob'u başlıksız/oturumsuz curl atar → `MEVDUAT_PANEL_REFRESH_TOKEN`
    (config veya env) ayarlıysa `X-Refresh-Token` başlığı ya da `?token=` ile
    eşleşme aranır. Token hiç ayarlanmamışsa endpoint yalnız oturumlu kullanıcıya
    açıktır (yanlışlıkla herkese açık kalmasın)."""
    expected = current_app.config.get("MEVDUAT_PANEL_REFRESH_TOKEN") or os.environ.get(
        "MEVDUAT_PANEL_REFRESH_TOKEN"
    )
    if expected:
        supplied = request.headers.get("X-Refresh-Token") or request.args.get("token", "")
        return bool(supplied) and supplied == expected
    return bool(getattr(current_user, "is_authenticated", False))


@mevduat_panel_bp.route("/admin/refresh", methods=["POST"])
def admin_refresh() -> Response:
    """Motor cache'lerini boşalt + yeniden ısıt (ODH cronjob tazeleme kancası).

    `?async=1` → arka planda koşar, hemen 202 döner (cronjob worker'ı bekletmez).
    Varsayılan senkron: tazeleme özeti ({ok, steps, elapsed_s}) döner."""
    if not _refresh_authorized():
        return Response(
            json.dumps({"ok": False, "error": "yetkisiz"}),
            status=401,
            mimetype="application/json",
        )

    from .prewarm import refresh_all

    app = current_app._get_current_object()

    def _refresh_job(a):
        summary = refresh_all(a)
        # W5b — veri tazelenince uzman piramidi (blok→süreç→uzman) yeniden
        # değerlendirilir. Hook app.py'de kablolanır (prisma_home buradan
        # import edilmez — izolasyon sözleşmesi); yoksa/no-op hata yutulur.
        hook = a.config.get("MEVDUAT_POST_REFRESH_HOOK")
        if hook is not None:
            try:
                hook()
            except Exception:
                a.logger.exception("post-refresh hook başarısız")
        return summary

    if request.args.get("async") in ("1", "true", "yes"):
        threading.Thread(
            target=_refresh_job, args=(app,), name="mevduat-panel-refresh", daemon=True
        ).start()
        return Response(
            json.dumps({"ok": True, "status": "started"}),
            status=202,
            mimetype="application/json",
        )

    summary = _refresh_job(app)
    return Response(
        json.dumps(summary),
        status=200 if summary.get("ok") else 500,
        mimetype="application/json",
    )


@mevduat_panel_bp.route("/api/<path:subpath>")
@login_required
def api_stub(subpath: str) -> Response:
    """Henuz portlanmamis endpoint'ler icin tutarli JSON hatasi.

    Kaynak SPA'nin hata yolu {"ok": false, "error": ...} bekler; boylece
    sayfa bos-state'te zarifce durur, konsola HTML parse hatasi dusmez.
    """
    payload = {
        "ok": False,
        "error": f"'/api/{subpath}' henuz portlanmadi (bkz. docs/DASHBOARD_ADAPTATION_PLAN.md faz plani).",
    }
    return Response(json.dumps(payload), status=501, mimetype="application/json")

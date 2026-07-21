"""mevduat_panel sayfa + API route'lari.

Faz A0: yalniz kabuk — SPA sayfasi render edilir, /api/* henuz stub'dur.
Faz A2+ endpoint'leri geldikce stub daralir (Flask'ta spesifik kural
catch-all'dan once eslesir, stub'a dokunmak gerekmez).
"""
from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, render_template, url_for
from flask_login import login_required

mevduat_panel_bp = Blueprint(
    "mevduat_panel",
    __name__,
    template_folder="templates",
    static_folder="static",
)

#: Statik dosya cache-busting'i. JS/CSS degistiginde artir
#: (kaynak repo Kirmizi Cizgi #7: tarayici agresif cache'ler).
MEVDUAT_VERSION = "p1.1"


def _masa_url() -> str:
    """Masa (PRISMA landing) URL'i — prisma_home kayitli degilse köke düş.

    Izolasyon geregi prisma_home'a import bagimliligi kurulmaz; endpoint
    yoksa modul yine calisir.
    """
    if "prisma_home.landing" in current_app.view_functions:
        return url_for("prisma_home.landing")
    return "/"


@mevduat_panel_bp.route("/")
@login_required
def index() -> Response:
    html = render_template(
        "mevduat_panel/index.html",
        masa_url=_masa_url(),
        mevduat_version=MEVDUAT_VERSION,
    )
    resp = Response(html, mimetype="text/html")
    # Kaynak repo disiplini: sayfa SPA state'i tasir, bayat HTML/JS
    # "degisiklik gorunmuyor" sinifinin ana sebebi.
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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

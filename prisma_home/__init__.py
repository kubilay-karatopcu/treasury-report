"""PRISMA Home blueprint — new consumer/atölye shell (Phase 10A).

Owns the top bar, mode toggle, atölye sidebar and the new base template
that pre-existing PRISMA pages (editor, list, snapshot) extend.

Sibling-of-root layout: mirrors `presentations/` placement, not under a
`flask_app/` subpackage. The spec's `flask_app.prisma_home` namespace
maps onto the actual project's flat root structure.
"""
from flask import Blueprint

prisma_home_bp = Blueprint(
    "prisma_home",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/prisma_home/static",
)


@prisma_home_bp.app_context_processor
def _inject_sidebar_helper():
    """Phase 11.wrap: expose a helper templates can use to lazy-build the
    atölye sidebar from their own ``sidebar_active`` setting — so producer
    routes (editor, list, kesif, hazirlik, bloklar, surecler) don't each
    need to pass a sidebar through their context dict.

    Ayrıca masa modu bayrağı + ``masa_name`` yardımcısı tüm şablonlara
    enjekte edilir (topbar/landing/expert bunları okur)."""
    from .masa import masa_mode_on, masa_name
    from .sidebar import get_sidebar

    def build_sidebar(active_key):
        return get_sidebar(active_key=active_key)

    return {"build_sidebar": build_sidebar,
            "masa_mode": masa_mode_on(),
            "masa_name": masa_name}


@prisma_home_bp.before_app_request
def _guard_masa_mode():
    """Masa modunda Atölye + LLM uçlarını kapatır (404).

    Üretim tarafının tamamı (``presentations.*``), Atölye ana ve LLM uçları
    erişilemez olur. Masa (tüketici) + mevduat_panel süreçleri + statik
    dosyalar dokunulmaz. Anahtar kapalıyken hiçbir şey yapmaz."""
    from flask import abort, request

    from .masa import is_blocked_endpoint, masa_mode_on

    if masa_mode_on() and is_blocked_endpoint(request.endpoint):
        abort(404)


# Defer route import to avoid circular deps when the blueprint is registered
# from app.py at module-load time.
from . import routes  # noqa: E402, F401

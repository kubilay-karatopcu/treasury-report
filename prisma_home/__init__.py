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
    need to pass a sidebar through their context dict."""
    from .sidebar import get_sidebar

    def build_sidebar(active_key):
        return get_sidebar(active_key=active_key)

    return {"build_sidebar": build_sidebar}


# Defer route import to avoid circular deps when the blueprint is registered
# from app.py at module-load time.
from . import routes  # noqa: E402, F401

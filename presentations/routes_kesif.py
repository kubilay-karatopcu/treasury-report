"""Phase 9.a — Atölye / Keşif HTTP routes.

Surfaces:

- ``GET  /atolye``                          — alias → /atolye/kesif
- ``GET  /atolye/kesif``                    — Keşif Tables tab (the screen)
- ``GET  /atolye/kesif/draft``              — current draft pid + basket snapshot
- ``POST /atolye/kesif/draft/promote``      — promote draft → real pid + redirect URL

The catalog/<...> endpoints live in :mod:`presentations.catalog.api`;
Keşif consumes them via fetch from the React bundle.

The basket itself is read/written through Phase 8's existing endpoints
(``POST /presentations/<pid>/basket``). The only new draft-specific bit is
the *promote* step, which materializes the draft pid into a real one so
Hazırlık can pick up the basket.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from flask import Response, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.drafts.manager import DraftManager, is_draft_pid


log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────


_DRAFT_MGR_KEY = "_PHASE9_DRAFT_MANAGER"


def _draft_manager() -> DraftManager:
    """Lazily instantiate the manager (depends on SESSION_REGISTRY and
    DATA_CLIENT, both already in app config)."""
    mgr = current_app.config.get(_DRAFT_MGR_KEY)
    if mgr is None:
        mgr = DraftManager(
            session_registry=current_app.config["SESSION_REGISTRY"],
            data_client=current_app.config.get("DATA_CLIENT"),
        )
        current_app.config[_DRAFT_MGR_KEY] = mgr
    return mgr


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        mimetype="application/json",
    )


# ── Pages ─────────────────────────────────────────────────────────────────


@presentations_bp.route("/atolye")
@login_required
def atolye_root():
    """The Atölye umbrella lands on Keşif Tables by default (spec §6.1)."""
    return redirect(url_for("presentations.atolye_kesif"))


@presentations_bp.route("/atolye/kesif")
@login_required
def atolye_kesif():
    """Render the Keşif Tables tab shell.

    The React bundle mounts on ``#kesif-root`` and reads ``#kesif-data`` for
    the initial draft pid + a CSRF-free seed (none in v1) + the user's
    sicil. All catalog data comes from the catalog API; the template only
    seeds the bootstrap state.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    draft = _draft_manager().get_or_create_current(sicil)

    # Pre-read the draft's current basket so the basket panel hydrates
    # without an extra round-trip on first paint.
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        basket = manifest.get("basket") or []
    except Exception:
        basket = []
        log.warning("kesif: failed to preload draft basket", exc_info=True)

    payload = {
        "user": {
            "sicil": sicil,
            "name": getattr(current_user, "name", "") or "",
            "department": getattr(current_user, "department", "") or "",
        },
        "draft": {
            "pid": draft.pid,
            "created_at": draft.created_at,
            "basket_count": len(basket),
        },
        "basket": basket,
        "flags": {
            # Phase 9.b.1 — Cosmograph WebGL graph render. Default off until the
            # commercial license lands; can be flipped on per-environment.
            "use_cosmograph": bool(current_app.config.get("KESIF_USE_COSMOGRAPH", False)),
        },
        "cosmograph": {
            # The license key flows from app config when present (Cosmograph
            # 2.x reads it at component-mount time to unlock commercial use).
            "license_key": current_app.config.get("COSMOGRAPH_LICENSE_KEY") or None,
        },
        "endpoints": {
            "catalog_list": url_for("presentations.list_catalog"),
            "catalog_detail_template": "/presentations/catalog/{schema}/{table}",
            "catalog_graph": url_for("presentations.catalog_graph"),
            "basket_update": f"/presentations/{draft.pid}/basket",
            "draft_promote": url_for("presentations.kesif_draft_promote"),
            "hazirlik_template": "/presentations/hazirlik/{pid}",
        },
    }

    return render_template(
        "presentations/atolye/kesif.html",
        kesif_json=json.dumps(payload, ensure_ascii=False, default=str),
        title="Keşif",
    )


@presentations_bp.route("/atolye/kesif/draft", methods=["GET"])
@login_required
def kesif_draft_info():
    """Return the current draft pid + basket snapshot. Used by the SPA when
    it needs to re-sync (e.g., user switched tabs)."""
    sicil = getattr(current_user, "sicil", None) or ""
    mgr = _draft_manager()
    draft = mgr.get_or_create_current(sicil)
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        basket = manifest.get("basket") or []
    except Exception:
        basket = []
    mgr.update_basket_count(sicil, draft.pid, len(basket))
    return _json({
        "pid": draft.pid,
        "basket": basket,
        "basket_count": len(basket),
    })


@presentations_bp.route("/atolye/kesif/draft/promote", methods=["POST"])
@login_required
def kesif_draft_promote():
    """Promote the user's current draft pid into a real presentation,
    return the Hazırlık URL. The frontend hard-navigates after the response.

    Body (optional):
      - ``pid``   — explicit draft pid (defaults to the user's current)
      - ``title`` — initial meta.title for the promoted presentation
    """
    sicil = getattr(current_user, "sicil", None) or ""
    body = request.get_json(silent=True) or {}
    draft_pid = (body.get("pid") or "").strip()
    title = (body.get("title") or "").strip() or None
    mgr = _draft_manager()

    if not draft_pid:
        current = mgr.get_or_create_current(sicil)
        draft_pid = current.pid

    if not is_draft_pid(draft_pid):
        return _json({"error": "Geçersiz taslak."}, status=400)

    try:
        new_pid = mgr.promote(sicil, draft_pid, title=title)
    except Exception as exc:
        log.exception("kesif: draft promote failed")
        return _json({"error": str(exc)}, status=400)

    hazirlik_url = url_for("presentations.hazirlik", pid=new_pid)
    return _json({
        "ok": True,
        "presentation_id": new_pid,
        "hazirlik_url": hazirlik_url,
    })

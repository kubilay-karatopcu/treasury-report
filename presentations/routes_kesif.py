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
from datetime import datetime, timezone
from typing import Any

from flask import Response, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.catalog.loader import make_loader_from_app
from presentations.discovery import (
    DEFAULT_TOKEN_BUDGET,
    DiscoveryError,
    propose_tables,
)
from presentations.drafts.manager import DraftManager, is_draft_pid


log = logging.getLogger(__name__)


# Max chat-history messages we keep on the draft manifest. Spec §5.2:
# the LLM only sees the last 10 turns, but we keep ~30 stored so the user
# can scroll back through their prior session.
_CHAT_HISTORY_CAP = 30
# Pinned-loader cache key shared with catalog.api so chat reuses the
# loader instance + its catalog TTL cache instead of building a fresh one.
_CATALOG_LOADER_KEY = "_PHASE9_CATALOG_LOADER"


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
            "chat_send": url_for("presentations.kesif_chat_send"),
            "chat_clear": url_for("presentations.kesif_chat_clear"),
            "hazirlik_template": "/presentations/hazirlik/{pid}",
        },
        # Phase 9.c — chat history seed so the drawer hydrates without an
        # extra round-trip on first paint. Bound to the draft manifest so it
        # persists across reloads but resets when the user promotes.
        "chat": {
            "history": _chat_history_for_draft(sicil, draft.pid),
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


# ── Phase 9.c — Discovery chat ───────────────────────────────────────────


def _chat_history_for_draft(sicil: str, pid: str) -> list[dict]:
    """Read the persisted chat history off the draft manifest. Returns
    an empty list on any failure — chat is best-effort."""
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, pid)
        manifest = sess.get_manifest() or {}
        return list(manifest.get("kesif_chat_history") or [])
    except Exception:
        log.warning("kesif: chat history read failed", exc_info=True)
        return []


def _persist_chat_history(sicil: str, pid: str, history: list[dict]) -> None:
    """Write chat history back onto the draft manifest. Capped at
    ``_CHAT_HISTORY_CAP`` messages so the manifest can't grow unbounded."""
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, pid)
        manifest = sess.get_manifest() or {}
        capped = list(history[-_CHAT_HISTORY_CAP:])
        manifest["kesif_chat_history"] = capped
        manifest["version"] = manifest.get("version", 0) + 1
        sess.set_manifest(manifest)
    except Exception:
        log.warning("kesif: chat history persist failed", exc_info=True)


@presentations_bp.route("/atolye/kesif/chat", methods=["POST"])
@login_required
def kesif_chat_send():
    """Submit a chat message → run discovery LLM → return proposals.

    Body:
      - ``message``: str  (required)

    Response (200):
      {
        "user_message": { role, text, ts },
        "assistant_message": { role, text, proposals, highlights, ts },
        "history": [...],   // capped tail for client to render
      }

    On LLM failure the assistant message carries ``status: "error"`` and a
    graceful Turkish message; the route still returns 200 so the chat UI
    can keep going.
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return _json({"error": "Mesaj boş olamaz."}, status=400)
    if len(message) > 2000:
        return _json({"error": "Mesaj çok uzun (max 2000 karakter)."}, status=400)

    sicil = getattr(current_user, "sicil", None) or ""
    mgr = _draft_manager()
    draft = mgr.get_or_create_current(sicil)

    # Pull what the LLM needs: the catalog (same loader as /catalog), the
    # current basket, the chat history.
    loader = current_app.config.get(_CATALOG_LOADER_KEY)
    if loader is None:
        loader = make_loader_from_app(current_app)
        current_app.config[_CATALOG_LOADER_KEY] = loader
    try:
        catalog_entries = loader.load(user_sicil=sicil)
    except Exception:
        log.exception("kesif chat: catalog load failed")
        catalog_entries = []

    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        basket = manifest.get("basket") or []
        history = list(manifest.get("kesif_chat_history") or [])
    except Exception:
        basket = []
        history = []
        log.warning("kesif chat: draft read failed", exc_info=True)

    now = datetime.now(timezone.utc).isoformat()
    user_turn = {"role": "user", "text": message, "ts": now}
    history.append(user_turn)

    llm_client = current_app.config.get("LLM_CLIENT")
    token_budget = int(current_app.config.get(
        "PRESENTATIONS_DISCOVERY_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET,
    ))

    if llm_client is None:
        assistant_turn = {
            "role": "assistant",
            "text": "LLM yapılandırılmamış (LLM_CLIENT yok).",
            "status": "error",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        history.append(assistant_turn)
        _persist_chat_history(sicil, draft.pid, history)
        return _json({
            "user_message": user_turn,
            "assistant_message": assistant_turn,
            "history": history[-_CHAT_HISTORY_CAP:],
        })

    user_department = getattr(current_user, "department", None) or None
    try:
        result = propose_tables(
            llm_client,
            user_request=message,
            catalog_entries=catalog_entries,
            current_basket=basket,
            chat_history=history[:-1],  # exclude the just-appended user turn
            user_department=user_department,
            token_budget=token_budget,
        )
    except DiscoveryError as exc:
        log.warning("kesif chat: discovery error — %s", exc)
        assistant_turn = {
            "role": "assistant",
            "text": str(exc),
            "status": "error",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        history.append(assistant_turn)
        _persist_chat_history(sicil, draft.pid, history)
        return _json({
            "user_message": user_turn,
            "assistant_message": assistant_turn,
            "history": history[-_CHAT_HISTORY_CAP:],
        })

    assistant_turn = {
        "role": "assistant",
        "text": result.explanation or "",
        "proposals": [p.to_dict() for p in result.proposals],
        "highlights": list(result.highlight_graph_node_ids),
        "dropped": list(result.dropped_proposals),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    history.append(assistant_turn)
    _persist_chat_history(sicil, draft.pid, history)

    return _json({
        "user_message": user_turn,
        "assistant_message": assistant_turn,
        "history": history[-_CHAT_HISTORY_CAP:],
    })


@presentations_bp.route("/atolye/kesif/chat", methods=["DELETE"])
@login_required
def kesif_chat_clear():
    """Wipe the chat history for the current draft. Returns an empty list."""
    sicil = getattr(current_user, "sicil", None) or ""
    mgr = _draft_manager()
    draft = mgr.get_or_create_current(sicil)
    _persist_chat_history(sicil, draft.pid, [])
    return _json({"ok": True, "history": []})

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


def _build_workbench_payload(sicil: str, initial_view: str) -> dict:
    """Phase 11.workbench — shared payload for the unified Atölye Workbench
    (Keşif / Bloklar / Süreçler all share the same React shell).

    `initial_view` selects which center renders first: "tablolar" (graph),
    "bloklar" (library grid), or "surecler" (process placeholder). All
    endpoints are bundled so view swaps don't need server round-trips.
    """
    draft = _draft_manager().get_or_create_current(sicil)
    title = ""
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        basket = manifest.get("basket") or []
        title = (manifest.get("meta") or {}).get("title") or ""
    except Exception:
        basket = []
        log.warning("workbench: failed to preload draft basket", exc_info=True)

    return {
        "initial_view": initial_view,
        "user": {
            "sicil": sicil,
            "name": getattr(current_user, "name", "") or "",
            "department": getattr(current_user, "department", "") or "",
        },
        "draft": {
            "pid": draft.pid,
            "created_at": draft.created_at,
            "basket_count": len(basket),
            # Phase 12.kesif-header: title shown in the workshop strip.
            # Falls back to a friendly default — the user can edit it.
            "title": title,
        },
        "basket": basket,
        "cosmograph": {
            "license_key": current_app.config.get("COSMOGRAPH_LICENSE_KEY") or None,
        },
        "endpoints": {
            "catalog_list": url_for("presentations.list_catalog"),
            "catalog_detail_template": "/presentations/catalog/{schema}/{table}",
            "catalog_graph": url_for("presentations.catalog_graph"),
            "basket_update": f"/presentations/{draft.pid}/basket",
            "draft_promote": url_for("presentations.kesif_draft_promote"),
            # Phase 12.kesif-header endpoints — workshop title persistence
            # + explicit "Kaydet" button.
            "draft_title":  url_for("presentations.kesif_draft_title"),
            "draft_save":   url_for("presentations.kesif_draft_save"),
            "chat_send": url_for("presentations.kesif_chat_send"),
            "chat_clear": url_for("presentations.kesif_chat_clear"),
            "hazirlik_template": "/presentations/hazirlik/{pid}",
            # Phase 11.workbench — bloklar view's data source.
            "library_list": url_for("presentations.list_library"),
            "library_detail_template": "/presentations/library/{bid}",
            "library_preview_template": "/presentations/library/{bid}/preview",
        },
        "chat": {
            "history": _chat_history_for_draft(sicil, draft.pid),
        },
    }


@presentations_bp.route("/atolye/kesif")
@login_required
def atolye_kesif():
    """Atölye Workbench — Tablolar (graph) view."""
    sicil = getattr(current_user, "sicil", None) or ""
    payload = _build_workbench_payload(sicil, initial_view="tablolar")
    return render_template(
        "presentations/atolye/kesif.html",
        kesif_json=json.dumps(payload, ensure_ascii=False, default=str),
        title="Keşif",
    )


@presentations_bp.route("/atolye/bloklar")
@login_required
def atolye_bloklar():
    """Atölye Workbench — Bloklar view.

    Phase 11.workbench: renders the same kesif.html shell as ``atolye_kesif``
    with ``initial_view="bloklar"`` so the React app starts on the block grid.
    The legacy bloklar.html template is kept only for backward-compat with
    bookmarks that hit the bundle directly.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    payload = _build_workbench_payload(sicil, initial_view="bloklar")
    return render_template(
        "presentations/atolye/kesif.html",
        kesif_json=json.dumps(payload, ensure_ascii=False, default=str),
        title="Bloklar",
    )


@presentations_bp.route("/atolye/surecler")
@login_required
def atolye_surecler():
    """Atölye Workbench — Süreçler view.

    Phase 11.workbench: same shell as ``atolye_kesif`` with
    ``initial_view="surecler"``. Center renders the Phase 13 placeholder.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    payload = _build_workbench_payload(sicil, initial_view="surecler")
    return render_template(
        "presentations/atolye/kesif.html",
        kesif_json=json.dumps(payload, ensure_ascii=False, default=str),
        title="Süreçler",
    )


# ── Phase 11.lib — Tablolar / Şablonlar pages ─────────────────────────────

@presentations_bp.route("/atolye/tablolar")
@login_required
def atolye_tablolar():
    """Server-rendered catalog browser. Reads the same unified catalog the
    Keşif graph uses (Phase 9.a CatalogLoader) and groups tables by
    schema for a dark-themed flat browser.

    For deep dive on a single table the row link hands off to the existing
    ``/presentations/catalog/<schema>/<table>`` JSON endpoint — that's all
    Keşif itself uses so we get the same data contract.
    """
    from presentations.catalog.api import _get_loader

    sicil = getattr(current_user, "sicil", None) or ""
    try:
        entries = _get_loader().load(user_sicil=sicil)
    except Exception:
        log.exception("atolye_tablolar: catalog loader failed")
        entries = []

    # Group by schema, sort by name within each group. Schemas with the
    # most tables come first so EDW is naturally on top.
    by_schema: dict[str, list] = {}
    for e in entries:
        by_schema.setdefault(e.schema_name, []).append(e)
    grouped = sorted(
        ((s, sorted(ts, key=lambda x: x.name)) for s, ts in by_schema.items()),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    return render_template(
        "presentations/atolye/tablolar.html",
        groups=grouped,
        total=sum(len(ts) for _, ts in grouped),
    )


@presentations_bp.route("/atolye/sablonlar")
@login_required
def atolye_sablonlar():
    """Atölye / Şablonlar — curated starter presentations.

    For now: any snapshot bound to ≥1 expert serves as a "template" the
    user can clone. Phase 12 will introduce a first-class template
    contract (immutable, versioned, separate from snapshots); for now we
    surface what the user already has so the page isn't a dead stub.
    """
    snapshot_store = current_app.config.get("SNAPSHOT_STORE")
    expert_store = current_app.config.get("EXPERT_STORE")
    try:
        all_meta = snapshot_store.list_all_meta() if snapshot_store else []
    except Exception:
        log.exception("atolye_sablonlar: snapshot list_all_meta failed")
        all_meta = []

    # Only snapshots bound to an expert qualify as templates — gives the
    # page a coherent shape (otherwise every ad-hoc snapshot leaks in).
    templates = [m for m in all_meta if m.get("bound_experts")]
    templates.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    templates = templates[:24]  # keep it tight

    # Lookup expert metadata (code + accent color) for the badge column.
    expert_index = {}
    if expert_store is not None:
        for e in expert_store.list_all():
            expert_index[e.id] = {"code": e.code, "color": e.ui.get("accent_color", "#6B8AFD")}

    return render_template(
        "presentations/atolye/sablonlar.html",
        templates=templates,
        expert_index=expert_index,
        total=len(templates),
    )


# ── Phase 12.workshops — Şablonlar (in-progress workshops) ────────────────


def _phase_label(phase: str) -> str:
    """Human-readable Turkish label for a workshop phase."""
    return {
        "kesif":    "Keşif",
        "hazirlik": "Hazırlık",
        "sunum":    "Sunum",
    }.get(phase, "Keşif")


def _continue_url_for_phase(pid: str, phase: str) -> str:
    """Where "Devam Et" should land for a workshop in a given phase.

    - kesif    → /atolye/kesif (user's current draft; the rendered Keşif
                 page picks up the basket from manifest by sicil+pid).
    - hazirlik → /presentations/hazirlik/<pid> (re-opens scope editor)
    - sunum    → /presentations/<pid> (the full editor)
    """
    if phase == "sunum":
        return url_for("presentations.editor", pid=pid)
    if phase == "hazirlik":
        return url_for("presentations.hazirlik", pid=pid)
    return url_for("presentations.atolye_kesif")


def list_workshops_for(sicil: str) -> list[dict]:
    """Phase 12.workshops — Atölye home + Şablonlar both need the same
    list: every saved presentation owned by ``sicil``, enriched with
    a phase chip + a "continue here" URL. Snapshot-only presentations
    (those already published as Süreçler) are excluded so they don't
    double-count.
    """
    registry = current_app.config.get("SESSION_REGISTRY")
    if registry is None:
        return []
    try:
        items = registry.list_user_presentations(sicil) or []
    except Exception:
        log.exception("list_workshops_for: registry list failed")
        return []
    out = []
    for it in items:
        phase = it.get("phase", "kesif")
        pid = it.get("id")
        # Title fallback — manifests created via the auto-flow may have an
        # empty meta.title; surface the pid so the row isn't a blank line.
        title = (it.get("title") or "").strip()
        if not title:
            title = f"Adsız çalışma · {pid[:12]}"
        out.append({
            **it,
            "title": title,
            "phase_label": _phase_label(phase),
            "continue_url": _continue_url_for_phase(pid, phase),
        })
    return out


@presentations_bp.route("/atolye/taslaklar")
@login_required
def atolye_taslaklar():
    """Atölye / Şablonlar — in-progress workshops.

    Every saved presentation that hasn't been turned into a published
    snapshot yet lives here. Each row links back to the appropriate
    Keşif / Hazırlık / Sunum page so the user can resume.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    workshops = list_workshops_for(sicil)
    return render_template(
        "presentations/atolye/taslaklar.html",
        workshops=workshops,
        total=len(workshops),
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


# ── Phase 12.kesif-header — workshop title + explicit save ───────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@presentations_bp.route("/atolye/kesif/draft/title", methods=["POST"])
@login_required
def kesif_draft_title():
    """Persist the workshop's title to the current draft's manifest.

    Body: ``{"title": "Q4 Mevduat"}``. Empty strings clear the title back
    to the auto-generated default. Title lives at ``manifest.meta.title``
    so promote() naturally carries it across to the real presentation_id.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()[:200]
    mgr = _draft_manager()
    draft = mgr.get_or_create_current(sicil)
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        meta = manifest.setdefault("meta", {})
        meta["title"] = title
        manifest["updated_at"] = _utc_now_iso()
        sess.set_manifest(manifest)
    except Exception as exc:
        log.exception("kesif_draft_title: write failed")
        return _json({"error": str(exc)}, status=500)
    return _json({"ok": True, "title": title, "pid": draft.pid})


@presentations_bp.route("/atolye/kesif/draft/save", methods=["POST"])
@login_required
def kesif_draft_save():
    """Explicit "Kaydet" — bumps updated_at + re-persists the manifest
    so the workshop reliably appears at the top of Son Aktiviteler /
    Şablonlar. Auto-save covers most cases; this endpoint exists so the
    user has a visible confirmation when they click the Save button.
    """
    sicil = getattr(current_user, "sicil", None) or ""
    mgr = _draft_manager()
    draft = mgr.get_or_create_current(sicil)
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft.pid)
        manifest = sess.get_manifest() or {}
        manifest["updated_at"] = _utc_now_iso()
        sess.set_manifest(manifest)
    except Exception as exc:
        log.exception("kesif_draft_save: write failed")
        return _json({"error": str(exc)}, status=500)
    return _json({
        "ok": True,
        "pid": draft.pid,
        "updated_at": manifest.get("updated_at"),
    })


@presentations_bp.route("/atolye/kesif/draft/promote", methods=["POST"])
@login_required
def kesif_draft_promote():
    """Promote the user's current draft pid into a real presentation,
    return the Hazırlık URL. The frontend hard-navigates after the response.

    The draft's basket table IDs are forwarded to Hazırlık via the
    existing ``?seed=ID1,ID2,...`` deeplink mechanism — see
    ``routes_scope.hazirlik`` + ``_seed_basket_from_query``. Only the
    items the user actually basketed flow through; everything else they
    browsed in Keşif is dropped.

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

    # Snapshot the draft's basket BEFORE promotion — promote() deletes
    # the draft manifest, so we have to capture the table IDs first.
    basket_ids: list[str] = []
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, draft_pid)
        manifest = sess.get_manifest() or {}
        for item in (manifest.get("basket") or []):
            tid = (item.get("table") or "").strip()
            if tid:
                basket_ids.append(tid)
    except Exception:
        log.warning("kesif promote: basket snapshot failed", exc_info=True)

    try:
        new_pid = mgr.promote(sicil, draft_pid, title=title)
    except Exception as exc:
        log.exception("kesif: draft promote failed")
        return _json({"error": str(exc)}, status=400)

    if basket_ids:
        # de-dup while preserving order, then encode as ?seed=…
        seen: set[str] = set()
        unique = [t for t in basket_ids if not (t in seen or seen.add(t))]
        hazirlik_url = url_for(
            "presentations.hazirlik", pid=new_pid, seed=",".join(unique)
        )
    else:
        hazirlik_url = url_for("presentations.hazirlik", pid=new_pid)
    return _json({
        "ok": True,
        "presentation_id": new_pid,
        "hazirlik_url": hazirlik_url,
        "seeded_basket_count": len(basket_ids),
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

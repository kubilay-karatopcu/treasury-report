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
from itertools import groupby
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


def _build_workbench_payload(
    sicil: str, initial_view: str, resume_pid: str | None = None
) -> dict:
    """Phase 11.workbench — shared payload for the unified Atölye Workbench
    (Keşif / Bloklar / Süreçler all share the same React shell).

    `initial_view` selects which center renders first: "tablolar" (graph),
    "bloklar" (library grid), or "surecler" (process placeholder). All
    endpoints are bundled so view swaps don't need server round-trips.

    `resume_pid` re-enters Keşif for an already-promoted (real) presentation
    — e.g. the "Keşife Dön" button on Hazırlık. We then resume THAT pid's
    basket instead of the user's current draft: no new draft is minted, basket
    edits + "Hazırlık'a geç" target this pid directly (see kesif_draft_promote).
    A draft pid or empty input falls through to the normal current-draft flow.
    """
    resume = resume_pid if (resume_pid and not is_draft_pid(resume_pid)) else None
    if resume:
        pid = resume
        created_at = ""
    else:
        draft = _draft_manager().get_or_create_current(sicil)
        pid = draft.pid
        created_at = draft.created_at
    title = ""
    try:
        sess = current_app.config["SESSION_REGISTRY"].get_or_create(sicil, pid)
        manifest = sess.get_manifest() or {}
        basket = manifest.get("basket") or []
        title = (manifest.get("meta") or {}).get("title") or ""
    except Exception:
        basket = []
        log.warning("workbench: failed to preload basket for %s", pid, exc_info=True)

    return {
        "initial_view": initial_view,
        "user": {
            "sicil": sicil,
            "name": getattr(current_user, "name", "") or "",
            "department": getattr(current_user, "department", "") or "",
        },
        "draft": {
            "pid": pid,
            "created_at": created_at,
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
            "basket_update": f"/presentations/{pid}/basket",
            "draft_promote": url_for("presentations.kesif_draft_promote"),
            # Phase 12.kesif-header endpoints — workshop title persistence
            # + explicit "Kaydet" button.
            "draft_title":  url_for("presentations.kesif_draft_title"),
            "draft_save":   url_for("presentations.kesif_draft_save"),
            "chat_send": url_for("presentations.kesif_chat_send"),
            "chat_clear": url_for("presentations.kesif_chat_clear"),
            "hazirlik_template": "/presentations/hazirlik/{pid}",
            # Phase 11.workbench — bloklar view's data source. After the library
            # consolidation the old /library/* routes are gone; the BLOCK_STORE
            # listing + preview live under /blocks/*. url_for keeps the proxy
            # SCRIPT_NAME prefix; the {team}/{bid} placeholders are filled client
            # side (blocks_workbench previewUrl).
            "library_list": url_for("presentations.api_list_blocks"),
            "library_preview_template": url_for(
                "presentations.block_library_preview",
                team="__TEAM__", block_id="__BID__",
            ).replace("__TEAM__", "{team}").replace("__BID__", "{bid}"),
        },
        "chat": {
            "history": _chat_history_for_draft(sicil, pid),
        },
    }


@presentations_bp.route("/atolye/kesif")
@login_required
def atolye_kesif():
    """Atölye Workbench — Tablolar (graph) view."""
    sicil = getattr(current_user, "sicil", None) or ""
    resume_pid = (request.args.get("pid") or "").strip() or None
    payload = _build_workbench_payload(
        sicil, initial_view="tablolar", resume_pid=resume_pid
    )
    return render_template(
        "presentations/atolye/kesif.html",
        kesif_json=json.dumps(payload, ensure_ascii=False, default=str),
        title="Keşif",
    )


@presentations_bp.route("/atolye/bloklar")
@login_required
def atolye_bloklar():
    """Atölye / Kütüphane / Bloklar — server-rendered library grid.

    Lists all saved Phase 6.5 blocks the user can see, with search +
    filter (team, viz type, tag) chips. Clicking a card opens the
    dedicated block editor (/blocks/edit/<team>/<id>). Replaces the
    previous React-workbench-with-initial_view=bloklar setup.
    """
    blocks_initial: list[dict] = []
    try:
        block_store = current_app.config.get("BLOCK_STORE")
        if block_store is not None:
            blocks_initial = [s.to_dict() for s in block_store.list_blocks()]
    except Exception:
        log.exception("atolye_bloklar: BlockStore listing failed")
        blocks_initial = []

    return render_template(
        "presentations/atolye/bloklar.html",
        blocks_initial=blocks_initial,
        blocks_initial_json=json.dumps(blocks_initial, ensure_ascii=False, default=str),
        total=len(blocks_initial),
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


# ── Phase 7.ui — Konseptler (registry browser + binding hub) ──────────────

def _scope_rank(scope: str | None) -> tuple[int, str]:
    """Sort order for concept scopes: global → dept:* → user → other."""
    s = scope or "global"
    if s == "global":
        return (0, "")
    if s.startswith("dept:"):
        return (1, s)
    if s == "user":
        return (2, "")
    return (3, s)


@presentations_bp.route("/atolye/konseptler")
@login_required
def atolye_konseptler():
    """Atölye / Kütüphane / Konseptler — concept registry browser + binding hub.

    Lists every concept (grouped by scope) with its canonical values and a
    reverse index of which documented-table columns bind it, plus an inline
    "kolon → concept" assignment form. Assignments write a ``human_verified``
    ColumnBinding into the binding catalog (S3 in prod, dir in dev) — exactly
    what the Phase 7.b filter compiler reads.
    """
    registry = current_app.config.get("CONCEPT_REGISTRY")
    catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")

    concepts = list(registry.all_concepts()) if registry else []
    concepts.sort(key=lambda c: (_scope_rank(c.scope), c.id))

    # Reverse index: concept_id → [{schema, table, column, kind, confidence}].
    bindings_by_concept: dict[str, list[dict]] = {}
    if catalog is not None:
        try:
            for schema, table in catalog.all_keys():
                for b in catalog.get_bindings(schema, table, verified_only=False):
                    bindings_by_concept.setdefault(b.concept, []).append({
                        "schema": schema, "table": table, "column": b.column,
                        "kind": b.transform.kind, "confidence": b.confidence,
                    })
        except Exception:
            log.exception("atolye_konseptler: reverse-index build failed")

    concept_dicts: list[dict] = []
    for c in concepts:
        binds = sorted(
            bindings_by_concept.get(c.id, []),
            key=lambda x: (x["schema"], x["table"], x["column"]),
        )
        concept_dicts.append({
            "id": c.id, "name": c.name, "type": c.type,
            "scope": c.scope or "global",
            "description": c.description or "",
            "canonical_values": [
                {"code": cv.code, "label": cv.label or "",
                 "aliases": list(cv.aliases or [])}
                for cv in c.canonical_values
            ],
            "bindings": binds,
        })

    # Group by scope for the template (concepts are already scope-sorted), so
    # the divider rendering is a clean nested loop instead of stateful Jinja.
    def _scope_label(scope: str) -> str:
        if scope == "global":
            return "Global (kurumsal)"
        if scope == "user":
            return "Kullanıcı"
        if scope.startswith("dept:"):
            return f"Departman · {scope[5:]}"
        return scope

    concept_groups: list[dict] = []
    for scope, items in groupby(concept_dicts, key=lambda c: c["scope"]):
        # Key is "concepts", NOT "items": Jinja attribute access on a dict
        # resolves ``g.items`` to the builtin dict.items method, not the value.
        concept_groups.append({
            "scope": scope, "label": _scope_label(scope), "concepts": list(items),
        })

    # Documented tables for the assign dropdown. Columns are fetched lazily
    # from /catalog/<schema>/<table> when a table is picked (keeps this page
    # cheap even with hundreds of tables).
    from presentations.catalog.api import _get_loader

    sicil = getattr(current_user, "sicil", None) or ""
    tables: list[dict] = []
    try:
        for e in _get_loader().load(user_sicil=sicil):
            tables.append({"schema": e.schema_name, "table": e.name})
    except Exception:
        log.exception("atolye_konseptler: documented-table list failed")
    tables.sort(key=lambda t: (t["schema"], t["table"]))

    return render_template(
        "presentations/atolye/konseptler.html",
        concept_groups=concept_groups,
        concepts_json=json.dumps(concept_dicts, ensure_ascii=False),
        tables_json=json.dumps(tables, ensure_ascii=False),
        total_concepts=len(concept_dicts),
        total_bindings=sum(len(v) for v in bindings_by_concept.values()),
    )


def _build_transform(kind: str, params: dict | None) -> dict:
    """Assemble a transform dict from the assign form's kind + params.
    Validation happens downstream via ColumnBinding.model_validate."""
    params = params or {}
    if kind == "identity":
        return {"kind": "identity"}
    if kind == "time_truncation":
        return {"kind": "time_truncation"}
    if kind == "map":
        pairs = params.get("pairs")
        return {"kind": "map", "pairs": pairs if isinstance(pairs, dict) else {}}
    if kind == "lookup":
        return {
            "kind": "lookup",
            "dim_table": (params.get("dim_table") or "").strip(),
            "dim_key": (params.get("dim_key") or "").strip(),
            "dim_canonical": (params.get("dim_canonical") or "").strip(),
        }
    if kind == "bucket_from_range":
        return {
            "kind": "bucket_from_range",
            "ranges_concept": (params.get("ranges_concept") or "").strip(),
        }
    return {"kind": kind}


@presentations_bp.route("/atolye/konseptler/bind", methods=["POST"])
@login_required
def konsept_bind():
    """Assign a concept to a documented-table column → human_verified binding.

    Body: ``{schema, table, column, concept, transform_kind, transform_params}``.
    Merges into the table's binding doc (preserving other keys) and persists
    via the binding catalog (dir in dev, S3 in prod).
    """
    from presentations.concepts.schema import ColumnBinding

    body = request.get_json(silent=True) or {}
    schema = (body.get("schema") or "").strip().upper()
    table = (body.get("table") or "").strip().upper()
    column = (body.get("column") or "").strip().upper()
    concept = (body.get("concept") or "").strip()
    transform = _build_transform(
        (body.get("transform_kind") or "").strip(),
        body.get("transform_params"),
    )

    registry = current_app.config.get("CONCEPT_REGISTRY")
    catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")

    errors: list[str] = []
    if not schema or not table or not column:
        errors.append("Şema, tablo ve kolon zorunlu.")
    if not concept:
        errors.append("Concept seçilmeli.")
    elif registry is not None and not registry.has(concept):
        errors.append(f"Bilinmeyen concept: {concept}")
    if not transform.get("kind"):
        errors.append("Transform türü seçilmeli.")
    if catalog is None:
        errors.append("Binding catalog yapılandırılmamış.")
    if errors:
        return _json({"ok": False, "errors": errors}, status=400)

    sicil = getattr(current_user, "sicil", None) or "unknown"
    candidate = {
        "concept": concept, "column": column, "transform": transform,
        "confidence": "human_verified", "verified_by": sicil,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    try:
        model = ColumnBinding.model_validate(candidate)
    except Exception as exc:
        return _json({"ok": False, "errors": [f"Geçersiz binding: {exc}"]}, status=400)

    doc = catalog.get_raw_doc(schema, table)
    if not isinstance(doc, dict):
        doc = {"table": table, "schema": schema}
    doc.setdefault("table", table)
    doc.setdefault("schema", schema)

    existing = doc.get("concept_bindings") or []
    by_key: dict[tuple[str, str], dict] = {}
    for b in existing:
        if isinstance(b, dict) and b.get("column") and b.get("concept"):
            by_key[(b["column"], b["concept"])] = b
    by_key[(column, concept)] = model.model_dump(mode="json", exclude_none=True)
    doc["concept_bindings"] = list(by_key.values())

    try:
        catalog.save_doc(schema, table, doc)
    except Exception as exc:
        log.exception("konsept_bind: save failed for %s.%s", schema, table)
        return _json({"ok": False, "errors": [f"Kaydedilemedi: {exc}"]}, status=500)

    return _json({"ok": True, "binding": {
        "schema": schema, "table": table, "column": column,
        "concept": concept, "kind": transform["kind"], "confidence": "human_verified",
    }})


@presentations_bp.route("/atolye/konseptler/unbind", methods=["POST"])
@login_required
def konsept_unbind():
    """Remove a (column, concept) binding from a table's binding doc."""
    body = request.get_json(silent=True) or {}
    schema = (body.get("schema") or "").strip().upper()
    table = (body.get("table") or "").strip().upper()
    column = (body.get("column") or "").strip().upper()
    concept = (body.get("concept") or "").strip()

    catalog = current_app.config.get("CONCEPT_BINDING_CATALOG")
    if catalog is None:
        return _json({"ok": False, "errors": ["Binding catalog yok."]}, status=400)

    doc = catalog.get_raw_doc(schema, table)
    if not isinstance(doc, dict):
        return _json({"ok": True, "removed": 0})
    existing = doc.get("concept_bindings") or []
    kept = [
        b for b in existing
        if not (isinstance(b, dict)
                and b.get("column") == column and b.get("concept") == concept)
    ]
    doc["concept_bindings"] = kept
    try:
        catalog.save_doc(schema, table, doc)
    except Exception as exc:
        log.exception("konsept_unbind: save failed for %s.%s", schema, table)
        return _json({"ok": False, "errors": [str(exc)]}, status=500)
    return _json({"ok": True, "removed": len(existing) - len(kept)})


@presentations_bp.route("/atolye/konseptler/create", methods=["POST"])
@login_required
def konsept_create():
    """Define a new concept → registry scope file (user or global scope).

    Body: ``{id, name, type, scope, description, canonical_values:[{code,label,aliases}]}``.
    Validates via :class:`Concept`, appends to the scope file (preserving its
    other concepts + metadata), and persists via the registry (dir in dev,
    S3 in prod). New ids must be globally unique across the registry.
    """
    from presentations.concepts.schema import Concept, load_concept_file_from_dict

    body = request.get_json(silent=True) or {}
    cid = (body.get("id") or "").strip().lower()
    name = (body.get("name") or "").strip()
    ctype = (body.get("type") or "").strip()
    scope = (body.get("scope") or "user").strip()
    description = (body.get("description") or "").strip()
    cvs_in = body.get("canonical_values") or []

    registry = current_app.config.get("CONCEPT_REGISTRY")
    errors: list[str] = []
    if registry is None:
        return _json({"ok": False, "errors": ["Concept registry yapılandırılmamış."]}, status=400)
    if not cid or not name or not ctype:
        errors.append("id, ad ve tür zorunlu.")
    if ctype not in ("enum", "time", "bucket", "scalar"):
        errors.append("Tür enum / time / bucket / scalar olmalı.")
    if scope not in ("user", "global"):
        errors.append("Scope 'user' veya 'global' olmalı.")
    if cid and registry.has(cid):
        errors.append(f"'{cid}' zaten tanımlı — id benzersiz olmalı.")
    if errors:
        return _json({"ok": False, "errors": errors}, status=400)

    concept_dict: dict[str, Any] = {"id": cid, "name": name, "type": ctype, "scope": scope}
    if description:
        concept_dict["description"] = description
    if ctype in ("enum", "bucket"):
        cvs: list[dict] = []
        for cv in cvs_in:
            code = (cv.get("code") or "").strip()
            if not code:
                continue
            entry: dict[str, Any] = {"code": code}
            label = (cv.get("label") or "").strip()
            if label:
                entry["label"] = label
            aliases = [a.strip() for a in (cv.get("aliases") or []) if a and a.strip()]
            if aliases:
                entry["aliases"] = aliases
            cvs.append(entry)
        concept_dict["canonical_values"] = cvs

    # Validate the concept standalone first for a clean error message.
    try:
        Concept.model_validate(concept_dict)
    except Exception as exc:
        return _json({"ok": False, "errors": [f"Geçersiz concept: {exc}"]}, status=400)

    file_name = scope  # user → user.yaml, global → global.yaml
    raw = registry.get_file_raw(file_name)
    if not isinstance(raw, dict):
        raw = {"version": 1, "scope": scope, "concepts": []}
    raw.setdefault("version", 1)
    raw.setdefault("scope", scope)
    if not isinstance(raw.get("concepts"), list):
        raw["concepts"] = []
    raw["concepts"].append(concept_dict)

    # Validate the whole file (scope match, in-file id uniqueness).
    try:
        load_concept_file_from_dict(raw)
    except Exception as exc:
        return _json({"ok": False, "errors": [f"Dosya doğrulanamadı: {exc}"]}, status=400)

    try:
        registry.save_file(file_name, raw)
    except Exception as exc:
        log.exception("konsept_create: save failed for %s", cid)
        return _json({"ok": False, "errors": [f"Kaydedilemedi: {exc}"]}, status=500)

    return _json({"ok": True, "id": cid, "scope": scope})


@presentations_bp.route("/atolye/konseptler/concept/<concept_id>", methods=["DELETE"])
@login_required
def konsept_delete(concept_id: str):
    """Delete a concept from its scope file. Any column bindings that referenced
    it become concept-blind (harmless) — remove them per-table from the binding
    list if you want a clean slate."""
    registry = current_app.config.get("CONCEPT_REGISTRY")
    if registry is None:
        return _json({"ok": False, "error": "Concept registry yapılandırılmamış."}, status=400)
    if not registry.has(concept_id):
        return _json({"ok": False, "error": "Concept bulunamadı."}, status=404)
    try:
        ok = registry.delete_concept(concept_id)
    except Exception as exc:
        log.exception("konsept_delete failed for %s", concept_id)
        return _json({"ok": False, "error": f"Silinemedi: {exc}"}, status=500)
    if not ok:
        return _json({"ok": False, "error": "Concept scope dosyasında bulunamadı."}, status=404)
    return _json({"ok": True, "id": concept_id})


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


# ── Pipeline phase checkpoint pages ────────────────────────────────────
# Sidebar Pipeline > Keşif / Hazırlık / Sunum land here. Each page lists
# the user's saved workshops in that phase + a "Yeniden Başla" CTA card
# that drops them back into Keşif with a fresh draft. Clicking a card
# resumes the workshop at the right editor URL (kesif graph / hazırlık /
# sunum editor) — same `continue_url` the Şablonlar listing builds.


_PIPELINE_PHASES = {
    "kesif": {
        "label": "Keşif",
        "num": "A·1",
        "eyebrow": "Pipeline · 01 Keşif",
        "intro": (
            "Sepetini topladığın, tabloları keşfettiğin tüm çalışmalar burada. "
            "Bir karta tıkla, kaldığın yerden devam et — ya da yeniden başla."
        ),
        "empty_title": "Henüz Keşif çalışması yok",
        "empty_body": (
            "Yeniden Başla'ya tıkla, grafik üzerinden sepetini topla. "
            "Her çalışma otomatik olarak burada gözükmeye başlar."
        ),
    },
    "hazirlik": {
        "label": "Hazırlık",
        "num": "A·2",
        "eyebrow": "Pipeline · 02 Hazırlık",
        "intro": (
            "Scope kurduğun, tablo ilişkilerini bağladığın, filtre tasarladığın "
            "çalışmalar. Devam etmek için karta tıkla."
        ),
        "empty_title": "Hazırlık aşamasında çalışma yok",
        "empty_body": (
            "Önce Keşif'te sepetini topla. Hazırlığa geçtiğinde bu liste dolar."
        ),
    },
    "sunum": {
        "label": "Sunum",
        "num": "A·3",
        "eyebrow": "Pipeline · 03 Sunum",
        "intro": (
            "Blok yerleştirdiğin, hikaye ördüğün tüm sunumlar. Karta tıkla, "
            "editörde aç."
        ),
        "empty_title": "Henüz Sunum yok",
        "empty_body": (
            "Hazırlık aşamasında scope'unu kapatıp ilk bloğunu eklediğinde "
            "sunum çalışmaların burada listelenir."
        ),
    },
}


def _checkpoints_for(sicil: str, phase: str) -> list[dict]:
    """Return only the workshops that fall in ``phase``."""
    return [w for w in list_workshops_for(sicil) if w.get("phase") == phase]


def _render_pipeline_phase(phase: str, sidebar_active: str):
    """Shared renderer for the three /atolye/pipeline/<phase> pages."""
    cfg = _PIPELINE_PHASES[phase]
    sicil = getattr(current_user, "sicil", None) or ""
    checkpoints = _checkpoints_for(sicil, phase)
    return render_template(
        "presentations/atolye/pipeline_phase.html",
        phase=phase,
        phase_cfg=cfg,
        sidebar_active=sidebar_active,
        checkpoints=checkpoints,
        total=len(checkpoints),
        restart_url=url_for("presentations.atolye_kesif"),
    )


@presentations_bp.route("/atolye/pipeline/kesif")
@login_required
def pipeline_kesif():
    return _render_pipeline_phase("kesif", sidebar_active="kesif")


@presentations_bp.route("/atolye/pipeline/hazirlik")
@login_required
def pipeline_hazirlik():
    return _render_pipeline_phase("hazirlik", sidebar_active="hazirlik")


@presentations_bp.route("/atolye/pipeline/sunum")
@login_required
def pipeline_sunum():
    return _render_pipeline_phase("sunum", sidebar_active="sunum")


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

    # `draft_pid` may be a real (already-promoted) pid when the user re-entered
    # Keşif from Hazırlık/Sunum via "Keşife Dön" (resume mode). There is nothing
    # to promote then — basket edits were persisted live to the pid — so we
    # forward straight back to Hazırlık for the SAME pid (no new presentation).
    is_draft = is_draft_pid(draft_pid)

    # Snapshot the basket BEFORE promotion — promote() deletes the draft
    # manifest, so we have to capture the table IDs first.
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

    # Refuse promotion of an empty draft — Hazırlık + Sunum can't do anything
    # without at least one table to fetch, and producing an empty manifest
    # just leaves dangling pids in the session store.
    if not basket_ids:
        return _json({
            "error": "Sepete en az 1 tablo ekleyin — boş sepetle hazırlığa geçilemez.",
        }, status=400)

    if is_draft:
        try:
            new_pid = mgr.promote(sicil, draft_pid, title=title)
        except Exception as exc:
            log.exception("kesif: draft promote failed")
            return _json({"error": str(exc)}, status=400)
    else:
        # Resume mode: keep the same real pid; forward nav re-opens its scope.
        new_pid = draft_pid

    if is_draft and basket_ids:
        # Newly promoted pid is fresh — forward the basket via ?seed so Hazırlık
        # picks it up. de-dup while preserving order.
        seen: set[str] = set()
        unique = [t for t in basket_ids if not (t in seen or seen.add(t))]
        hazirlik_url = url_for(
            "presentations.hazirlik", pid=new_pid, seed=",".join(unique)
        )
    else:
        # Resume (real pid): the scope already exists for this pid — DON'T
        # re-seed, that would clobber existing Hazırlık ER/scope state.
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

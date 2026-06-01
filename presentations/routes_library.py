"""Kütüphane edit/view routes for Tablolar, Uzmanlar and Süreçler.

Each Kütüphane entry has the same shape:

- A list page (cards, search/filter) — already implemented for Tablolar +
  Bloklar in :mod:`presentations.routes_kesif`.
- A per-item edit page that lets the user inspect + edit the underlying
  YAML documentation. This module owns those edit pages plus the JSON
  endpoints they use for save/validate.

Storage:

- Tables: :class:`presentations.table_docs.store.TableDoc` YAMLs, loaded
  via ``app.config["TABLE_DOC_STORE"]``.
- Experts: :class:`prisma_home.experts.Expert` YAMLs, loaded via
  ``app.config["EXPERT_STORE"]``.

Both editors render the same dark, monospaced YAML textarea + metadata
sidebar shell — only the read-only metadata column differs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from flask import (
    Response,
    abort,
    current_app,
    render_template,
    request,
)
from flask_login import current_user, login_required

from presentations import presentations_bp
from presentations.table_docs.schema import load_table_doc_from_dict
from presentations.table_docs.store import (
    TableDocNotFoundError,
    TableDocStoreError,
)


log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        mimetype="application/json",
    )


def _table_store():
    store = current_app.config.get("TABLE_DOC_STORE")
    if store is None:
        raise RuntimeError("TABLE_DOC_STORE not configured")
    return store


def _expert_store():
    store = current_app.config.get("EXPERT_STORE")
    if store is None:
        raise RuntimeError("EXPERT_STORE not configured")
    return store


def _expert_yaml_dir() -> Path | None:
    """Resolve the LocalExpertStore base dir. Returns ``None`` if the store
    isn't filesystem-backed (e.g. a future S3 backend) — caller treats that
    as read-only."""
    store = _expert_store()
    base = getattr(store, "base_dir", None)
    return Path(base) if base else None


# ════════════════════════════════════════════════════════════════════════
# Tables — table doc editor
# ════════════════════════════════════════════════════════════════════════


@presentations_bp.route("/atolye/tablolar/<schema>/<table>")
@login_required
def tablo_edit(schema: str, table: str):
    """Structured form editor for a single TableDoc.

    Loads the existing doc (or a seed shell if none exists) and renders
    a form-based editor. Save POSTs ``{form: {...}}`` to the same
    save endpoint; payload is rebuilt into the YAML shape server-side.
    """
    from presentations.variables.semantic_tags import all_tags

    # all_tags() returns dicts (id/label/description) — pick out just the ids
    # for the dropdown, sorted with the 'other' escape hatch at the bottom.
    try:
        tag_ids = sorted([t["id"] for t in all_tags() if t.get("id") and t["id"] != "other"])
    except Exception:
        tag_ids = []
    tag_ids.append("other")

    store = _table_store()
    exists = True
    try:
        doc = store.load(schema, table)
        form = _table_doc_to_form(doc)
        meta = {
            "table": doc.table,
            "schema": doc.schema_name,
            "columns_count": len(doc.columns),
            "filterable_count": sum(1 for c in doc.columns.values() if c.filterable),
        }
    except TableDocNotFoundError:
        exists = False
        form = {
            "description": "",
            "partition_column": "",
            "estimated_daily_rows": None,
            "columns": [],
        }
        meta = {
            "table": table,
            "schema": schema,
            "columns_count": 0,
            "filterable_count": 0,
        }
    except TableDocStoreError as exc:
        abort(400, description=str(exc))

    return render_template(
        "presentations/atolye/tablo_edit.html",
        schema=schema,
        table=table,
        exists=exists,
        form=form,
        form_json=json.dumps(form, ensure_ascii=False, default=str),
        meta=meta,
        semantic_tags=tag_ids,
        filter_roles=["time_axis", "dimension", "measure_threshold"],
    )


def _table_doc_to_form(doc) -> dict:
    """Convert a TableDoc into the form payload the editor consumes."""
    columns: list[dict] = []
    for name, col in doc.columns.items():
        lookup = None
        if col.lookup is not None:
            lookup = {
                "table": col.lookup.table,
                "key": col.lookup.key,
                "display": col.lookup.display,
            }
        columns.append({
            "name": name,
            "type": col.type,
            "description": col.description or "",
            "filterable": bool(col.filterable),
            "filter_role": col.filter_role or "",
            "suggested_variable": col.suggested_variable or "",
            "suggested_semantic_tag": col.suggested_semantic_tag or "",
            "aggregatable": bool(col.aggregatable),
            "get_distinct": bool(col.get_distinct),
            "visible_in_ui": bool(col.visible_in_ui),
            "lookup": lookup,
        })
    return {
        "description": doc.description or "",
        "partition_column": doc.partition_column or "",
        "estimated_daily_rows": doc.estimated_daily_rows,
        "columns": columns,
    }


@presentations_bp.route("/atolye/tablolar/<schema>/<table>/api/save", methods=["POST"])
@login_required
def tablo_save(schema: str, table: str):
    """Validate + persist a TableDoc edit.

    Body: ``{"form": {...}}`` from the structured editor (preferred), or
    ``{"yaml": "..."}`` for the legacy raw-YAML path. Both flow through
    ``load_table_doc_from_dict`` for the same schema validation.

    Form shape (per ``_form_to_table_doc_dict`` below):

        {
          "description": str,
          "partition_column": str | "",
          "estimated_daily_rows": int | null,
          "columns": [
            { "name": "COL", "type": "DATE", "description": "...",
              "filterable": bool, "filter_role": "time_axis"|...,
              "suggested_variable": str, "suggested_semantic_tag": str,
              "aggregatable": bool, "visible_in_ui": bool,
              "lookup": { "table": "...", "key": "...", "display": "..." } | null
            },
            ...
          ]
        }
    """
    body = request.get_json(silent=True) or {}

    if "form" in body and isinstance(body["form"], dict):
        try:
            parsed = _form_to_table_doc_dict(schema, table, body["form"])
        except ValueError as exc:
            return _json({"ok": False, "errors": [str(exc)]}, status=400)
    else:
        raw = body.get("yaml")
        if not isinstance(raw, str) or not raw.strip():
            return _json({"ok": False, "errors": ["İçerik boş olamaz."]}, status=400)
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return _json({"ok": False, "errors": [f"YAML parse hatası: {exc}"]}, status=400)
        if not isinstance(parsed, dict):
            return _json({"ok": False, "errors": ["YAML kökü bir mapping olmalı."]}, status=400)
        # URL ↔ payload mismatch check (same as before).
        yaml_schema = (parsed.get("schema") or "").strip()
        yaml_table = (parsed.get("table") or "").strip()
        if yaml_schema and yaml_schema != schema:
            return _json({
                "ok": False,
                "errors": [f"YAML'daki schema ({yaml_schema}) URL ile ({schema}) uyuşmuyor."],
            }, status=400)
        if yaml_table and yaml_table != table:
            return _json({
                "ok": False,
                "errors": [f"YAML'daki table ({yaml_table}) URL ile ({table}) uyuşmuyor."],
            }, status=400)
        parsed.setdefault("schema", schema)
        parsed.setdefault("table", table)

    try:
        doc = load_table_doc_from_dict(parsed)
    except Exception as exc:
        return _json({"ok": False, "errors": _humanize_validation_error(exc)}, status=400)

    try:
        store = _table_store()
        store.save(doc)
    except Exception as exc:
        log.exception("tablo_save: store.save failed for %s.%s", schema, table)
        return _json({"ok": False, "errors": [f"Kaydedilemedi: {exc}"]}, status=500)

    return _json({
        "ok": True,
        "schema": doc.schema_name,
        "table": doc.table,
        "columns_count": len(doc.columns),
    })


@presentations_bp.route("/atolye/tablolar/<schema>/<table>", methods=["DELETE"])
@login_required
def tablo_delete(schema: str, table: str):
    """Delete a table's documentation (TableDoc). Concept bindings live in a
    separate catalog and are left intact — remove them from Konseptler if
    needed. Returns 404 when the table was never documented."""
    try:
        ok = _table_store().delete(schema, table)
    except Exception as exc:
        log.exception("tablo_delete failed for %s.%s", schema, table)
        return _json({"ok": False, "error": f"Silinemedi: {exc}"}, status=500)
    if not ok:
        return _json({"ok": False, "error": "Dokümante tablo bulunamadı."}, status=404)
    return _json({"ok": True, "schema": schema, "table": table})


def _humanize_validation_error(exc: Exception) -> list[str]:
    """Convert a pydantic.ValidationError (or any other exception) into a
    short list of Turkish, user-friendly messages — strip the
    ``errors.pydantic.dev`` URLs and per-error noise that confuse users.
    """
    try:
        from pydantic import ValidationError
    except ImportError:
        ValidationError = None  # type: ignore

    if ValidationError is not None and isinstance(exc, ValidationError):
        out: list[str] = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", []) if p != "__root__")
            msg = e.get("msg", "geçersiz değer")
            t = e.get("type", "")
            if t == "string_pattern_mismatch":
                msg = "geçersiz karakter (sadece harf, rakam, alt çizgi; rakamla başlayamaz)"
            elif t == "missing":
                msg = "zorunlu alan"
            elif t == "value_error":
                # pydantic prefixes value_errors with "Value error, " — strip.
                msg = msg.removeprefix("Value error, ").strip()
            out.append(f"{loc}: {msg}" if loc else msg)
        return out or ["Şema doğrulama hatası."]
    # Non-pydantic exceptions (rare): keep the message but cap length so a
    # 5kB pydantic dump doesn't bomb the modal.
    s = str(exc).splitlines()[0][:200]
    return [f"Şema doğrulama hatası: {s}"]


def _form_to_table_doc_dict(schema: str, table: str, form: dict) -> dict:
    """Convert the structured form payload into the YAML-shaped dict that
    ``load_table_doc_from_dict`` consumes. Strips empty optional fields so
    they don't leak through as ``None`` strings into the YAML.
    """
    out: dict = {"schema": schema, "table": table}

    desc = (form.get("description") or "").strip()
    if desc:
        out["description"] = desc

    partition = (form.get("partition_column") or "").strip()
    if partition:
        out["partition_column"] = partition

    rows = form.get("estimated_daily_rows")
    if rows not in (None, "", 0):
        try:
            out["estimated_daily_rows"] = int(rows)
        except (TypeError, ValueError):
            raise ValueError("Günlük tahmini satır sayısı tamsayı olmalı.")

    cols_in = form.get("columns") or []
    if not isinstance(cols_in, list):
        raise ValueError("Kolonlar listesi gönderilmedi.")

    cols_dict: dict = {}
    seen_names: set[str] = set()
    for idx, col in enumerate(cols_in):
        if not isinstance(col, dict):
            raise ValueError(f"#{idx+1} kolonu obje değil.")
        name = (col.get("name") or "").strip().upper()
        if not name:
            raise ValueError(f"#{idx+1} kolonun ismi boş.")
        if name in seen_names:
            raise ValueError(f"{name} kolonu birden fazla tanımlanmış.")
        seen_names.add(name)

        type_ = (col.get("type") or "").strip()
        if not type_:
            raise ValueError(f"{name}: tip boş.")

        entry: dict = {"type": type_}
        cdesc = (col.get("description") or "").strip()
        if cdesc:
            entry["description"] = cdesc

        if col.get("filterable"):
            entry["filterable"] = True
            fr = (col.get("filter_role") or "").strip()
            if fr:
                entry["filter_role"] = fr
        sv = (col.get("suggested_variable") or "").strip()
        if sv:
            entry["suggested_variable"] = sv
        sst = (col.get("suggested_semantic_tag") or "").strip()
        if sst:
            entry["suggested_semantic_tag"] = sst
        if col.get("aggregatable"):
            entry["aggregatable"] = True
        if col.get("get_distinct"):
            entry["get_distinct"] = True
        # visible_in_ui defaults True on the dataclass; only emit when False
        # so the YAML stays minimal.
        if col.get("visible_in_ui") is False:
            entry["visible_in_ui"] = False

        lookup = col.get("lookup")
        if isinstance(lookup, dict):
            lt = (lookup.get("table") or "").strip()
            lk = (lookup.get("key") or "").strip()
            ld = (lookup.get("display") or "").strip()
            if lt or lk or ld:
                if not (lt and lk and ld):
                    raise ValueError(
                        f"{name}: lookup için table/key/display üçü birden gerekli."
                    )
                entry["lookup"] = {"table": lt, "key": lk, "display": ld}

        cols_dict[name] = entry

    out["columns"] = cols_dict
    return out


# ════════════════════════════════════════════════════════════════════════
# Uzmanlar — expert list + edit
# ════════════════════════════════════════════════════════════════════════


@presentations_bp.route("/atolye/uzmanlar")
@login_required
def atolye_uzmanlar():
    """List all experts the current user can see, as library cards."""
    try:
        experts = _expert_store().list_for_user(current_user)
    except Exception:
        log.exception("atolye_uzmanlar: expert listing failed")
        experts = []
    return render_template(
        "presentations/atolye/uzmanlar.html",
        experts=experts,
        total=len(experts),
    )


@presentations_bp.route("/atolye/uzmanlar/<expert_id>")
@login_required
def uzman_edit(expert_id: str):
    """Structured-form editor for a single expert."""
    store = _expert_store()
    expert = store.load(expert_id.lower())
    if expert is None:
        abort(404, description=f"Uzman bulunamadı: {expert_id}")

    # Access check — mirror prisma_home.expert_detail.
    read = expert.access_scope.get("read") or []
    dept = getattr(current_user, "department", None) or ""
    if "*" not in read and dept not in read:
        abort(403)

    edit_list = expert.access_scope.get("edit") or []
    can_edit = ("*" in edit_list) or (dept in edit_list)

    # Form payload — what the editor renders + edits.
    form = _expert_to_form(expert)

    meta = {
        "id": expert.id,
        "code": expert.code,
        "name": expert.name,
        "domain_label": expert.domain_label,
        "version": expert.version,
        "status": expert.status,
        "accent_color": (expert.ui or {}).get("accent_color", "#6B8AFD"),
        "glyph": (expert.ui or {}).get("glyph", ""),
        "bound_blocks": len((expert.bound_content or {}).get("blocks", []) or []),
        "bound_snapshots": len((expert.bound_content or {}).get("snapshots", []) or []),
        "edit_scope": edit_list,
    }

    # Picker hints — surface known blocks so the user can pick by ID instead
    # of typing free-form. We pull just IDs (BlockStore.list_blocks returns
    # summaries) and team-prefix them so they look like the spec's
    # ``team/id`` references.
    block_choices: list[str] = []
    try:
        block_store = current_app.config.get("BLOCK_STORE")
        if block_store is not None:
            block_choices = [
                f"{s.team}/{s.id}" for s in block_store.list_blocks()
            ]
    except Exception:
        log.warning("uzman_edit: block listing failed", exc_info=True)

    return render_template(
        "presentations/atolye/uzman_edit.html",
        expert=expert,
        meta=meta,
        form=form,
        form_json=json.dumps(form, ensure_ascii=False, default=str),
        block_choices=block_choices,
        can_edit=can_edit,
    )


def _expert_to_form(expert) -> dict:
    """Convert an Expert dataclass into the editor's form shape."""
    persona = expert.persona or {}
    bound = expert.bound_content or {}
    recipe = expert.briefing_recipe or {}
    scope = expert.access_scope or {}
    ui = expert.ui or {}

    return {
        "id": expert.id,
        "version": expert.version,
        "code": expert.code,
        "name": expert.name,
        "domain_label": expert.domain_label,
        "short_description": expert.short_description or "",
        "status": expert.status or "active",
        "persona": {
            "system_prompt": persona.get("system_prompt") or "",
            "voice_examples": list(persona.get("voice_examples") or []),
        },
        "bound_content": {
            "blocks":    list(bound.get("blocks") or []),
            "snapshots": list(bound.get("snapshots") or []),
            "processes": list(bound.get("processes") or []),
        },
        "briefing_recipe": {
            "cache_ttl_seconds": int(recipe.get("cache_ttl_seconds") or 1800),
            # Sections is deeply nested; serialise as YAML text so the form
            # can show it in one textarea. The user edits the YAML chunk.
            "sections_yaml": yaml.safe_dump(
                list(recipe.get("sections") or []),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            ).rstrip("\n"),
        },
        "access_scope": {
            "read": list(scope.get("read") or []),
            "edit": list(scope.get("edit") or []),
        },
        "ui": {
            "accent_color": ui.get("accent_color") or "#6B8AFD",
            "glyph": ui.get("glyph") or "",
        },
    }


@presentations_bp.route("/atolye/uzmanlar/<expert_id>/api/save", methods=["POST"])
@login_required
def uzman_save(expert_id: str):
    """Validate + persist an expert edit.

    Body: ``{"form": {...}}`` from the structured editor (preferred), or
    ``{"yaml": "..."}`` for the legacy raw-YAML path. Auth check (edit
    scope) runs first; on success writes the YAML file + invalidates the
    in-memory LocalExpertStore cache.
    """
    store = _expert_store()
    existing = store.load(expert_id.lower())
    if existing is None:
        return _json({"ok": False, "errors": ["Uzman bulunamadı."]}, status=404)

    dept = getattr(current_user, "department", None) or ""
    edit_scope = existing.access_scope.get("edit") or []
    if "*" not in edit_scope and dept not in edit_scope:
        return _json(
            {"ok": False, "errors": ["Bu uzmanı düzenleme yetkin yok."]},
            status=403,
        )

    body = request.get_json(silent=True) or {}

    if "form" in body and isinstance(body["form"], dict):
        try:
            parsed = _form_to_expert_dict(expert_id, body["form"])
        except ValueError as exc:
            return _json({"ok": False, "errors": [str(exc)]}, status=400)
    else:
        raw = body.get("yaml")
        if not isinstance(raw, str) or not raw.strip():
            return _json({"ok": False, "errors": ["İçerik boş olamaz."]}, status=400)
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return _json({"ok": False, "errors": [f"YAML parse hatası: {exc}"]}, status=400)
        if not isinstance(parsed, dict):
            return _json({"ok": False, "errors": ["YAML kökü bir mapping olmalı."]}, status=400)
        parsed_id = (parsed.get("id") or "").strip().lower()
        if parsed_id and parsed_id != expert_id.lower():
            return _json({
                "ok": False,
                "errors": [f"YAML'daki id ({parsed_id}) URL ile ({expert_id}) uyuşmuyor."],
            }, status=400)
        parsed.setdefault("id", expert_id.lower())

    # Round-trip through the dataclass to surface bad shapes before writing.
    try:
        from prisma_home.experts import Expert
        rebuilt = Expert.from_dict(parsed)
    except Exception as exc:
        return _json({"ok": False, "errors": [f"Uzman şeması hatası: {exc}"]}, status=400)

    # Tek depo arayüzü: LocalExpertStore dosyaya, S3ExpertStore S3'e yazar;
    # her ikisi de save() içinde kendi cache'ini tazeler (prod'da kalıcı).
    if store is None or not hasattr(store, "save"):
        return _json(
            {"ok": False, "errors": ["EXPERT_STORE bu ortamda salt-okunur."]},
            status=400,
        )
    try:
        store.save(rebuilt)
    except Exception as exc:
        log.exception("uzman_save: store.save failed for %s", rebuilt.id)
        return _json({"ok": False, "errors": [f"Kaydedilemedi: {exc}"]}, status=502)

    # Phase 10 reverse-link sync — the briefing engine reads
    # ``snapshot.bound_experts``, NOT ``expert.bound_content.snapshots``.
    # Whenever the user adds/removes a snapshot reference in the Expert form,
    # update the corresponding snapshot's bound_experts list so the briefing
    # actually surfaces those snapshots. See BUG-15.
    _sync_expert_to_snapshot_links(
        expert_id=rebuilt.id,
        old_snapshot_ids=(existing.bound_content or {}).get("snapshots") or [],
        new_snapshot_ids=(rebuilt.bound_content or {}).get("snapshots") or [],
    )

    return _json({
        "ok": True,
        "id": rebuilt.id,
        "version": rebuilt.version,
    })


@presentations_bp.route("/atolye/uzmanlar/<expert_id>", methods=["DELETE"])
@login_required
def uzman_delete(expert_id: str):
    """Delete an expert. Edit-scope gated exactly like uzman_save. Cleans up
    the reverse links so deleted experts stop appearing in snapshot citations."""
    store = _expert_store()
    existing = store.load(expert_id.lower())
    if existing is None:
        return _json({"ok": False, "error": "Uzman bulunamadı."}, status=404)

    dept = getattr(current_user, "department", None) or ""
    edit_scope = existing.access_scope.get("edit") or []
    if "*" not in edit_scope and dept not in edit_scope:
        return _json({"ok": False, "error": "Bu uzmanı silme yetkin yok."}, status=403)

    if not hasattr(store, "delete"):
        return _json({"ok": False, "error": "EXPERT_STORE bu ortamda salt-okunur."}, status=400)

    # Reverse-link cleanup: drop this expert from every snapshot it cited so
    # the landing / briefing engine no longer references a dead expert.
    try:
        _sync_expert_to_snapshot_links(
            expert_id=existing.id,
            old_snapshot_ids=(existing.bound_content or {}).get("snapshots") or [],
            new_snapshot_ids=[],
        )
    except Exception:
        log.warning("uzman_delete: snapshot unlink failed for %s", expert_id, exc_info=True)

    try:
        ok = store.delete(expert_id.lower())
    except Exception as exc:
        log.exception("uzman_delete: store.delete failed for %s", expert_id)
        return _json({"ok": False, "error": f"Silinemedi: {exc}"}, status=502)

    return _json({"ok": ok, "id": expert_id.lower()})


def _sync_expert_to_snapshot_links(
    *, expert_id: str, old_snapshot_ids: list, new_snapshot_ids: list,
) -> None:
    """Keep snapshot.bound_experts ↔ expert.bound_content.snapshots in sync.

    For each snapshot id newly added to the expert form, ensure ``expert_id``
    appears in that snapshot's ``bound_experts``. For each removed id, drop
    ``expert_id`` from the snapshot's list. Missing snapshots are silently
    skipped — the form already accepted them; we don't want a save to fail
    because of a stale reference.
    """
    snap_store = current_app.config.get("SNAPSHOT_STORE")
    if snap_store is None or not hasattr(snap_store, "set_bound_experts"):
        return
    added   = set(new_snapshot_ids) - set(old_snapshot_ids)
    removed = set(old_snapshot_ids) - set(new_snapshot_ids)
    if not (added or removed):
        return
    for sid in added | removed:
        snap = snap_store.load(sid)
        if snap is None:
            log.info(
                "sync expert→snapshot: snapshot %s missing (skipped); expert=%s",
                sid, expert_id,
            )
            continue
        cur = list(snap.get("meta", {}).get("bound_experts") or [])
        if sid in added and expert_id not in cur:
            cur.append(expert_id)
        elif sid in removed and expert_id in cur:
            cur = [e for e in cur if e != expert_id]
        else:
            continue
        try:
            snap_store.set_bound_experts(sid, cur)
            log.info("sync expert→snapshot: snapshot %s bound_experts=%s",
                     sid, cur)
        except Exception:
            log.warning(
                "sync expert→snapshot: set_bound_experts failed for %s",
                sid, exc_info=True,
            )
    # Briefing engine caches the rendered output per expert; the new bindings
    # should appear on next request. Invalidate proactively if engine exposes
    # an invalidate hook.
    engine = current_app.config.get("BRIEFING_ENGINE")
    try:
        if engine is not None and hasattr(engine, "invalidate"):
            engine.invalidate(expert_id)
    except Exception:
        pass


def _form_to_expert_dict(expert_id: str, form: dict) -> dict:
    """Rebuild the Expert dataclass dict from the editor's form payload.

    Mirrors :func:`_expert_to_form` so a round trip is lossless except for
    fields the form deliberately doesn't expose (none right now).
    """
    code = (form.get("code") or "").strip()
    name = (form.get("name") or "").strip()
    domain = (form.get("domain_label") or "").strip()
    if not code:
        raise ValueError("Kod boş olamaz.")
    if not name:
        raise ValueError("İsim boş olamaz.")
    if not domain:
        raise ValueError("Domain boş olamaz.")

    try:
        version = int(form.get("version") or 1)
    except (TypeError, ValueError):
        raise ValueError("Versiyon tamsayı olmalı.")

    persona_in = form.get("persona") or {}
    persona = {
        "system_prompt":  (persona_in.get("system_prompt") or "").strip(),
        "voice_examples": [
            s for s in (persona_in.get("voice_examples") or [])
            if isinstance(s, str) and s.strip()
        ],
    }

    bound_in = form.get("bound_content") or {}
    bound_content = {
        "blocks":    [s for s in (bound_in.get("blocks")    or []) if isinstance(s, str) and s.strip()],
        "snapshots": [s for s in (bound_in.get("snapshots") or []) if isinstance(s, str) and s.strip()],
        "processes": [s for s in (bound_in.get("processes") or []) if isinstance(s, str) and s.strip()],
    }

    recipe_in = form.get("briefing_recipe") or {}
    try:
        ttl = int(recipe_in.get("cache_ttl_seconds") or 1800)
    except (TypeError, ValueError):
        raise ValueError("cache_ttl_seconds tamsayı olmalı.")
    sections_yaml = (recipe_in.get("sections_yaml") or "").strip()
    if sections_yaml:
        try:
            sections = yaml.safe_load(sections_yaml)
        except yaml.YAMLError as exc:
            raise ValueError(f"Brifing reçetesi YAML parse hatası: {exc}")
        if sections is None:
            sections = []
        if not isinstance(sections, list):
            raise ValueError("briefing_recipe.sections bir liste olmalı.")
    else:
        sections = []
    briefing_recipe = {"cache_ttl_seconds": ttl, "sections": sections}

    scope_in = form.get("access_scope") or {}
    access_scope = {
        "read": [s for s in (scope_in.get("read") or []) if isinstance(s, str) and s.strip()],
        "edit": [s for s in (scope_in.get("edit") or []) if isinstance(s, str) and s.strip()],
    }
    if not access_scope["read"]:
        access_scope["read"] = ["*"]  # never produce an unreadable expert

    ui_in = form.get("ui") or {}
    ui = {
        "accent_color": (ui_in.get("accent_color") or "#6B8AFD").strip(),
        "glyph":        (ui_in.get("glyph") or "").strip(),
    }

    return {
        "id": expert_id.lower(),
        "version": version,
        "code": code,
        "name": name,
        "domain_label": domain,
        "short_description": (form.get("short_description") or "").strip(),
        "status": (form.get("status") or "active").strip() or "active",
        "persona": persona,
        "bound_content": bound_content,
        "briefing_recipe": briefing_recipe,
        "access_scope": access_scope,
        "ui": ui,
    }

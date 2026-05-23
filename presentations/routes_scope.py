"""TEMPORARY scope-contract HTTP endpoints (Phase 8.a).

These exist only so scope contracts can be created and inspected before the
Hazırlık UI lands in **8.b** — at which point the UI owns scope authoring and
these routes are expected to be removed / folded into the Hazırlık endpoints.
Do not build new UI against them.

- ``POST /presentations/<pid>/scope``            — validate + save, returns the new version.
- ``GET  /presentations/<pid>/scope``            — latest scope contract.
- ``GET  /presentations/<pid>/scope/<version>``  — a specific version.

Auth via the existing ``@login_required``; the owning user is
``current_user.sicil`` (the S3 key is ``presentations/<sicil>/<pid>/scope_v<N>.yaml``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Response, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from pydantic import ValidationError

from presentations import presentations_bp
from presentations.scope.catalog import AppCatalog
from presentations.scope.fetch import fetch_cached_tables
from presentations.scope.routing import (
    DEFAULT_HARD_CEILING_BYTES,
    DEFAULT_THRESHOLD_BYTES,
    RoutingCeilingError,
    apply_user_override,
    decide_routing,
)
from presentations.scope.schema import (
    ScopeContract,
    load_scope_from_dict,
    scope_to_dict,
)
from presentations.scope.store import ScopeNotFoundError, ScopeStoreError
from presentations.scope.validators import validate_scope

log = logging.getLogger(__name__)


def _json(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        mimetype="application/json",
    )


def _json_default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"object of type {type(o).__name__} is not JSON serializable")


def _scope_store():
    store = current_app.config.get("SCOPE_STORE")
    if store is None:
        raise RuntimeError(
            "SCOPE_STORE not configured. Add a LocalScopeStore / S3ScopeStore "
            "to app.config in your factory."
        )
    return store


def _catalog() -> AppCatalog:
    return AppCatalog(
        table_doc_store=current_app.config.get("TABLE_DOC_STORE"),
        concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
        binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
    )


def _routing_threshold_bytes() -> int:
    return int(current_app.config.get(
        "PRESENTATIONS_ROUTING_THRESHOLD_BYTES", DEFAULT_THRESHOLD_BYTES))


def _routing_hard_ceiling_bytes() -> int:
    return int(current_app.config.get(
        "PRESENTATIONS_ROUTING_HARD_CEILING_BYTES", DEFAULT_HARD_CEILING_BYTES))


def _refresh_routing(scope: ScopeContract, catalog: AppCatalog) -> None:
    """Recompute the routing decision for every system-owned raw basket item.

    User overrides (``decided_by == "user"``) are preserved verbatim. Derived
    items (aggregates) always run inside DuckDB so they're conceptually
    "cached" — their routing field is normalised to that.

    Mutates ``scope`` in place. Called on every Hazırlık page load and on
    build, so the UI badges + the fetch pass always agree on the current
    estimate as pinned filters / projections evolve.
    """
    threshold = _routing_threshold_bytes()
    for item in scope.basket:
        if item.derivation is not None or item.table_ref is None:
            # Derived items run on DuckDB over the source view — there is no
            # Oracle pull to size, so they don't carry a routing decision.
            continue
        if item.routing.decided_by == "user":
            continue
        pinned = scope.pinned_filters_for_alias(item.alias)
        decision = decide_routing(
            table_ref=item.table_ref,
            projection=item.projection,
            pinned_filters=pinned,
            threshold_bytes=threshold,
            catalog=catalog,
        )
        item.routing.decision = decision.decision
        item.routing.decided_by = "system"
        item.routing.estimated_bytes = decision.estimated_bytes
        item.routing.threshold_bytes = decision.threshold_bytes


@presentations_bp.route("/<pid>/scope", methods=["POST"])
@login_required
def save_scope(pid: str):
    """Validate and persist a scope contract for ``pid`` (temporary; see module
    docstring). The URL ``pid`` and the current user are authoritative — they
    overwrite any ``presentation_id`` / ``created_by`` in the body."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "errors": ["body must be a JSON object"]}, status=400)

    try:
        scope: ScopeContract = load_scope_from_dict(body)
    except (ValidationError, ValueError) as exc:
        return _json(
            {"ok": False, "phase": "schema", "errors": _flatten(exc), "warnings": []},
            status=400,
        )

    # URL + auth win over the body.
    scope.presentation_id = pid
    scope.created_by = getattr(current_user, "sicil", None) or scope.created_by
    if scope.created_at is None:  # pragma: no cover — created_at is required by schema
        scope.created_at = datetime.now(timezone.utc)

    result = validate_scope(scope, _catalog())
    if not result.ok:
        return _json(
            {"ok": False, "phase": "validation",
             "errors": result.errors, "warnings": result.warnings},
            status=400,
        )

    try:
        version = _scope_store().save(scope)
    except ScopeStoreError as exc:
        return _json({"ok": False, "phase": "store", "errors": [str(exc)]}, status=409)

    return _json({"ok": True, "presentation_id": pid, "version": version,
                  "warnings": result.warnings})


@presentations_bp.route("/<pid>/scope", methods=["GET"])
@login_required
def get_latest_scope(pid: str):
    scope = _scope_store().load_latest(pid)
    if scope is None:
        return _json({"error": f"no scope contract for {pid}"}, status=404)
    return _json(scope_to_dict(scope))


@presentations_bp.route("/<pid>/scope/<int:version>", methods=["GET"])
@login_required
def get_scope_version(pid: str, version: int):
    try:
        scope = _scope_store().load(pid, version)
    except ScopeNotFoundError as exc:
        return _json({"error": str(exc)}, status=404)
    return _json(scope_to_dict(scope))


def _flatten(exc: ValidationError | ValueError) -> list[str]:
    if isinstance(exc, ValidationError):
        out: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            out.append(f"{loc}: {err.get('msg', 'validation error')}" if loc
                       else err.get("msg", "validation error"))
        return out
    return [str(exc)]


# ════════════════════════════════════════════════════════════════════════
# Phase 8.b — Hazırlık page + "Sunum'a geç" build flow
# ════════════════════════════════════════════════════════════════════════

def _registry():
    return current_app.config["SESSION_REGISTRY"]


def _concepts_payload() -> list[dict[str, Any]]:
    """Concepts the filter editor offers, with per-type op lists + canonical
    values. Read from the live registry."""
    reg = current_app.config.get("CONCEPT_REGISTRY")
    out: list[dict[str, Any]] = []
    if reg is None:
        return out
    for c in reg.all_concepts():
        t = getattr(c, "type", "scalar")
        if t == "time":
            ops = ["between", "last_n_days", "eq"]
        elif t in ("enum", "bucket"):
            ops = ["in", "not_in", "eq"]
        else:
            ops = ["eq", "between"]
        codes = c.canonical_codes() if hasattr(c, "canonical_codes") else []
        out.append({"id": c.id, "label": getattr(c, "name", c.id),
                    "type": t, "ops": ops, "canonical_values": codes})
    out.sort(key=lambda x: x["id"])
    return out


def _distributions_payload(scope: ScopeContract) -> dict[str, list[Any]]:
    """concept_id → distinct value sample, gathered across basket tables
    (Phase 6.5.b distinct_values_sample)."""
    store = current_app.config.get("TABLE_DOC_STORE")
    dist: dict[str, list[Any]] = {}
    if store is None:
        return dist
    for item in scope.basket:
        try:
            doc = store.load(item.table_ref.schema_name, item.table_ref.name)
        except Exception:
            continue
        for _col, cd in (getattr(doc, "columns", {}) or {}).items():
            concept = getattr(cd, "suggested_semantic_tag", None)
            sample = getattr(cd, "distinct_values_sample", None)
            if not concept or not sample:
                continue
            bucket = dist.setdefault(concept, [])
            for v in sample:
                if v not in bucket:
                    bucket.append(v)
    return dist


def _default_draft_scope(pid: str) -> ScopeContract:
    return ScopeContract.model_validate({
        "presentation_id": pid, "version": 1,
        "created_by": getattr(current_user, "sicil", "") or "unknown",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def _load_latest_scope_or_draft(pid: str) -> ScopeContract:
    store = current_app.config.get("SCOPE_STORE")
    if store is not None:
        try:
            sc = store.load_latest(pid)
            if sc is not None:
                return sc
        except Exception:
            log.warning("hazirlik: load_latest failed for %s", pid, exc_info=True)
    return _default_draft_scope(pid)


def _catalog_json() -> dict[str, Any]:
    from presentations.routes import _catalog_path
    try:
        return json.loads(_catalog_path().read_text(encoding="utf-8"))
    except Exception:
        return {"domains": []}


def _catalog_json_enriched() -> dict[str, Any]:
    """Catalog JSON enriched with per-column ``concept`` from the table-doc
    store. The frontend's ``addTableFromCatalog`` reads this so concept
    information survives the catalog → basket transition (without which the
    LLM's ``applies_to: []`` filter footers can't be rendered on the node)."""
    cat = _catalog_json()
    for d in (cat.get("domains") or []):
        for t in (d.get("tables") or []):
            tid = t.get("id") or ""
            if "." not in tid:
                continue
            schema, name = tid.split(".", 1)
            concept_by_col = {c["name"]: c.get("concept") for c in _columns_for(schema, name)}
            for col in (t.get("columns") or []):
                if col.get("name") in concept_by_col and concept_by_col[col["name"]]:
                    col["concept"] = concept_by_col[col["name"]]
    return cat


def _columns_for(schema: str, name: str) -> list[dict[str, Any]]:
    """Per-column metadata for a table node: name, type, concept, FK lookup."""
    store = current_app.config.get("TABLE_DOC_STORE")
    if store is None:
        return []
    try:
        doc = store.load(schema, name)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for col, cd in (getattr(doc, "columns", {}) or {}).items():
        lk = getattr(cd, "lookup", None)
        fr = getattr(cd, "filter_role", None)
        # Join-key candidate: an FK lookup, OR a dimension column, OR a
        # time-axis column (time keys are natural joins for time-series tables).
        # Data team can add an explicit `join_key` flag to the table-doc schema
        # later; until then we derive it from these signals.
        join_key = bool(lk) or fr in ("dimension", "time_axis")
        out.append({
            "name": col,
            "type": getattr(cd, "type", None),
            "concept": getattr(cd, "suggested_semantic_tag", None),
            "filter_role": fr,
            "join_key": join_key,
            "lookup": ({"table": lk.table, "key": lk.key, "display": lk.display} if lk else None),
        })
    return out


def _columns_by_alias(scope: ScopeContract) -> dict[str, list[dict[str, Any]]]:
    return {b.alias: _columns_for(b.table_ref.schema_name, b.table_ref.name) for b in scope.basket}


def _suggested_edges(scope: ScopeContract, cols_by_alias: dict[str, list[dict[str, Any]]]):
    """Auto-suggested join edges between basket aliases (§6R.3): FK ``lookup``
    declarations + columns sharing a concept. The frontend draws these
    (deduped against confirmed scope.joins) and the user can confirm/delete."""
    edges: list[dict[str, Any]] = []
    seen: set = set()
    by_name: dict[str, str] = {}
    for b in scope.basket:
        by_name.setdefault(b.table_ref.name, b.alias)

    def add(la, lc, ra, rc, kind, source):
        if la == ra:
            return
        key = tuple(sorted([(la, lc), (ra, rc)]))
        if key in seen:
            return
        seen.add(key)
        edges.append({"left": {"alias": la, "column": lc},
                      "right": {"alias": ra, "column": rc},
                      "kind": kind, "source": source})

    # (a) FK lookup → solid suggestion.
    for alias, cols in cols_by_alias.items():
        for col in cols:
            lk = col.get("lookup")
            if lk and lk["table"] in by_name:
                add(alias, col["name"], by_name[lk["table"]], lk["key"], "lookup", "catalog_lookup")

    # (b) shared concept → softer suggestion.
    concept_cols: dict[str, dict[str, str]] = {}
    for alias, cols in cols_by_alias.items():
        for col in cols:
            c = col.get("concept")
            if c:
                concept_cols.setdefault(c, {}).setdefault(alias, col["name"])
    for c, per_alias in concept_cols.items():
        items = list(per_alias.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (la, lc), (ra, rc) = items[i], items[j]
                add(la, lc, ra, rc, "inner", f"shared_concept:{c}")
    return edges


@presentations_bp.route("/hazirlik")
@login_required
def hazirlik_new():
    """Menu entry point (R › Hazırlık): start a fresh preparation draft. Mints a
    presentation id and redirects into the per-presentation Hazırlık screen. The
    manifest + scope are only written on 'Sunum'a geç' (build_scope), so an
    abandoned draft leaves nothing behind."""
    import secrets
    pid = "p_" + secrets.token_urlsafe(8)
    return redirect(url_for("presentations.hazirlik", pid=pid))


@presentations_bp.route("/hazirlik/<pid>")
@login_required
def hazirlik(pid: str):
    """The Hazırlık (Stage 2 / Prepare) screen. Renders the React bundle with
    the current scope contract, the table catalog, available concepts, and
    concept value distributions embedded as JSON."""
    scope = _load_latest_scope_or_draft(pid)
    title = pid
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        m = sess.get_manifest()
        if m:
            title = m.get("meta", {}).get("title") or pid
    except Exception:
        pass

    # Recompute routing from the live catalog so the UI badges reflect the
    # latest estimate as filters / projections evolve. User overrides survive.
    try:
        _refresh_routing(scope, _catalog())
    except Exception:
        log.warning("hazirlik: _refresh_routing failed", exc_info=True)

    cols_by_alias = _columns_by_alias(scope)
    payload = {
        "presentation_id": pid,
        "title": title,
        "scope": scope_to_dict(scope)["scope"],
        "catalog": _catalog_json_enriched(),
        "concepts": _concepts_payload(),
        "distributions": _distributions_payload(scope),
        "columns_by_alias": cols_by_alias,
        "suggested_edges": _suggested_edges(scope, cols_by_alias),
        "routing_config": {
            "threshold_bytes": _routing_threshold_bytes(),
            "hard_ceiling_bytes": _routing_hard_ceiling_bytes(),
        },
    }
    return render_template(
        "presentations/hazirlik.html",
        presentation_id=pid,
        title=title,
        hazirlik_json=json.dumps(payload, ensure_ascii=False, default=_json_default),
    )


@presentations_bp.route("/<pid>/scope/build", methods=["POST"])
@login_required
def build_scope(pid: str):
    """'Sunum'a geç': validate → fetch cached tables into DuckDB → persist scope
    (version bump) → write the manifest's scope_ref → return a redirect URL.
    Lazy tables are recorded in status but not fetched (8.d)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "errors": ["body must be a JSON object"]}, status=400)
    try:
        scope = load_scope_from_dict(body)
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "phase": "schema", "errors": _flatten(exc)}, status=400)

    scope.presentation_id = pid
    scope.created_by = getattr(current_user, "sicil", None) or scope.created_by

    catalog = _catalog()
    # Refresh routing so the fetch pass acts on the current estimate, not on
    # whatever the client posted (which is a hint but never authoritative).
    _refresh_routing(scope, catalog)
    result = validate_scope(scope, catalog)
    if not result.ok:
        return _json({"ok": False, "phase": "validation",
                      "errors": result.errors, "warnings": result.warnings}, status=400)

    dc = current_app.config.get("DATA_CLIENT")
    session = _registry().get_or_create(current_user.sicil, pid)
    conn = session.get_duck_conn()
    lazy = [b.alias for b in scope.basket
            if b.table_ref is not None and b.routing.decision == "lazy"]

    try:
        loaded = fetch_cached_tables(
            dc, conn, scope,
            catalog=catalog,
            concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
            binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
        )
    except Exception as exc:
        scope.status.state = "failed"
        scope.status.errors = [str(exc)]
        try:
            current_app.config["SCOPE_STORE"].save(scope)
        except Exception:
            log.warning("build_scope: persist of failed scope failed", exc_info=True)
        return _json({"ok": False, "phase": "fetch", "errors": [str(exc)]}, status=502)

    scope.status.state = "ready"
    scope.status.cached_tables = list(loaded.keys())
    scope.status.lazy_tables = lazy
    scope.status.fetched_at = datetime.now(timezone.utc)

    version = current_app.config["SCOPE_STORE"].save(scope)

    manifest = session.get_manifest() or {
        "id": pid, "version": 0, "owner_id": current_user.sicil,
        "meta": {"title": pid, "eyebrow": "", "date": "", "author_label": current_user.sicil},
        "blocks": [],
    }
    manifest["scope_ref"] = {"presentation_id": pid, "scope_version": version}
    manifest["version"] = int(manifest.get("version", 0)) + 1
    session.set_manifest(manifest)

    return _json({
        "ok": True,
        "scope_version": version,
        "cached_tables": scope.status.cached_tables,
        "lazy_tables": scope.status.lazy_tables,
        "redirect": url_for("presentations.editor", pid=pid),
    })


@presentations_bp.route("/<pid>/scope/recompute-routing", methods=["POST"])
@login_required
def scope_recompute_routing(pid: str):
    """Refresh routing decisions for every system-owned basket item.

    Called by the frontend after a client-side scope mutation that the
    server didn't see (e.g. ``addTableFromCatalog``). User overrides survive.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    if not isinstance(scope_in, dict):
        return _json({"ok": False, "error": "scope required"}, status=400)
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)
    _refresh_routing(scope, _catalog())
    return _json({"ok": True, "scope": scope_to_dict(scope)["scope"]})


@presentations_bp.route("/<pid>/scope/routing-override", methods=["POST"])
@login_required
def scope_routing_override(pid: str):
    """Apply a user override of the system routing decision (§3.4).

    Request: ``{"scope": <draft>, "alias": "...", "forced": "cached"|"lazy"}``
    Response: ``{"ok": true, "scope": <mutated>}`` or
              ``{"ok": false, "error": "...", "estimated_bytes": int, "hard_ceiling_bytes": int}``
    on hard-ceiling refusal.

    The mutated scope echoes back the new ``routing`` for the alias with
    ``decided_by: "user"``; nothing else changes. Persistence is the caller's
    responsibility (the same flow as apply-suggestion — the draft lives in the
    React state and is persisted at build time).
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    alias = (body.get("alias") or "").strip()
    forced = (body.get("forced") or "").strip()
    if not isinstance(scope_in, dict) or not alias or forced not in ("cached", "lazy"):
        return _json({"ok": False, "error": "scope, alias ve forced ('cached'/'lazy') zorunlu"}, status=400)

    # Re-read the draft as a Pydantic model so we can use the routing helpers.
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "error": "scope schema invalid", "errors": _flatten(exc)}, status=400)

    item = next((b for b in scope.basket if b.alias == alias), None)
    if item is None:
        return _json({"ok": False, "error": f"alias '{alias}' basket'te yok"}, status=400)
    if item.derivation is not None or item.table_ref is None:
        return _json({"ok": False, "error": "türetilmiş tablolar için routing override geçerli değil"}, status=400)

    # Compute the current estimate fresh so the ceiling check uses live data
    # rather than a stale value persisted from a previous version.
    fresh = decide_routing(
        table_ref=item.table_ref,
        projection=item.projection,
        pinned_filters=scope.pinned_filters_for_alias(item.alias),
        threshold_bytes=_routing_threshold_bytes(),
        catalog=_catalog(),
    )
    try:
        overridden = apply_user_override(
            fresh, forced,
            hard_ceiling_bytes=_routing_hard_ceiling_bytes(),
        )
    except RoutingCeilingError as exc:
        return _json({
            "ok": False,
            "error": str(exc),
            "estimated_bytes": exc.estimated_bytes,
            "hard_ceiling_bytes": exc.hard_ceiling_bytes,
        }, status=400)

    item.routing.decision = overridden.decision
    item.routing.decided_by = "user"
    item.routing.estimated_bytes = overridden.estimated_bytes
    item.routing.threshold_bytes = overridden.threshold_bytes

    out = scope_to_dict(scope)["scope"]
    return _json({"ok": True, "scope": out})


@presentations_bp.route("/<pid>/scope/preview", methods=["GET"])
@login_required
def scope_preview(pid: str):
    """Sample rows for a table, shown in the Hazırlık preview drawer (§6R.4).
    Query: ?schema=&table=&columns=col1,col2&limit=N. Reads via DataClient with
    an Oracle row cap; never the full table."""
    schema = (request.args.get("schema") or "").strip()
    table = (request.args.get("table") or "").strip()
    if not table:
        return _json({"error": "table param required"}, status=400)
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
    except ValueError:
        limit = 50
    columns = (request.args.get("columns") or "").strip()
    select = columns if columns else "*"
    full = f"{schema}.{table}" if schema else table

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"error": "DATA_CLIENT not configured"}, status=500)
    sql = f"SELECT {select} FROM {full} FETCH FIRST {limit} ROWS ONLY"
    try:
        df = dc.get_data(base_prefix=None, dataset=f"preview::{full}", query=sql, query_params={})
    except Exception as exc:
        return _json({"error": str(exc)}, status=502)

    import pandas as pd
    from presentations import duck
    if df is None:
        df = pd.DataFrame()
    rows = [[duck._jsonable(v) for v in r] for r in df.itertuples(index=False, name=None)]
    return _json({
        "columns": _columns_for(schema, table) or [{"name": str(c)} for c in df.columns],
        "data_columns": [str(c) for c in df.columns],
        "rows": rows,
        "row_count": int(len(df)),
    })


# ── Sunum scope banner (§6.3) ────────────────────────────────────────────────

def load_scope_for_manifest(manifest: dict | None):
    """Load the ScopeContract referenced by a manifest's scope_ref, or None."""
    ref = (manifest or {}).get("scope_ref")
    if not isinstance(ref, dict):
        return None
    store = current_app.config.get("SCOPE_STORE")
    if store is None:
        return None
    try:
        return store.load(ref.get("presentation_id"), int(ref.get("scope_version")))
    except Exception:
        return None


def scope_banner(scope) -> dict | None:
    """Compact read-only banner data for the Sunum scope chip (§6.3)."""
    if scope is None:
        return None
    pinned: list[str] = []
    for f in scope.filters.pinned:
        if f.op == "between":
            pinned.append(f"{f.from_} – {f.to}")
        elif f.values:
            pinned.append(", ".join(str(v) for v in f.values))
        elif f.value is not None:
            pinned.append(str(f.value))
    return {
        "scope_version": scope.version,
        "pinned": pinned,
        "edit_url": url_for("presentations.hazirlik", pid=scope.presentation_id),
    }


# ── Phase 8.f — Hazırlık scope-refinement chat ───────────────────────────────

def _bound_concepts_for_scope(scope_dict: dict, cols_by_alias: dict) -> list[dict]:
    """concept → [alias.column, …] index across the basket — used by the
    Stage-2 LLM to restrict its filter suggestions to concept names that are
    actually bindable in the current scope."""
    out: dict[str, list[str]] = {}
    for b in (scope_dict.get("basket") or []):
        alias = b.get("alias")
        if not alias:
            continue
        for col in (cols_by_alias.get(alias) or []):
            concept = col.get("concept")
            if concept:
                out.setdefault(concept, []).append(f"{alias}.{col['name']}")
    return [{"concept": c, "bound_in": sorted(v)} for c, v in sorted(out.items())]


def _catalog_excerpt_for_basket(scope_dict: dict) -> list[dict]:
    """The basket's catalog metadata, filtered to just the tables in scope.
    Lets the LLM see column names / types / common_values without paying for
    the whole catalog dump."""
    in_basket = set()
    for b in (scope_dict.get("basket") or []):
        ref = b.get("table_ref") or {}
        if ref:
            sid = f"{ref.get('schema','')}.{ref.get('name','')}".strip(".")
            if sid:
                in_basket.add(sid)
    full = _catalog_json() or {"domains": []}
    out: list[dict] = []
    for d in (full.get("domains") or []):
        for t in (d.get("tables") or []):
            if t.get("id") in in_basket:
                out.append(t)
    return out


@presentations_bp.route("/<pid>/scope/chat", methods=["POST"])
@login_required
def scope_chat(pid: str):
    """Phase 8.f — Hazırlık LLM refinement chat.

    Request body:
        {"scope": <draft scope dict>, "message": "...", "history": [...] }

    Response:
        {"explanation": str, "suggestions": [{ "id": "sg_...", "kind": ..., ...}]}

    Each suggestion gets a server-assigned `id` so the frontend's Apply
    round-trip can reference it; the scope itself is not mutated here —
    Apply goes through ``/scope/apply-suggestion``.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope") or {}
    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not user_message:
        return _json({"error": "Mesaj boş olamaz."}, status=400)
    if not isinstance(scope_in, dict):
        return _json({"error": "scope must be a JSON object"}, status=400)

    # Server-side basket → cols_by_alias even on a draft (unvalidated) scope —
    # we don't load_scope_from_dict here because the user may be mid-edit.
    cols_by_alias: dict[str, list[dict]] = {}
    for b in (scope_in.get("basket") or []):
        alias = b.get("alias")
        ref = b.get("table_ref") or {}
        if alias and ref.get("schema") and ref.get("name"):
            cols_by_alias[alias] = _columns_for(ref["schema"], ref["name"])

    bound = _bound_concepts_for_scope(scope_in, cols_by_alias)
    catalog_excerpt = _catalog_excerpt_for_basket(scope_in)

    llm = current_app.config.get("LLM_CLIENT")
    if llm is None:
        return _json({"error": "LLM_CLIENT not configured"}, status=500)

    try:
        result = llm.suggest_scope_refinements(
            scope=scope_in,
            user_message=user_message,
            bound_concepts=bound,
            catalog_excerpt=catalog_excerpt,
            history=history,
        )
    except Exception as exc:
        log.exception("scope_chat: LLM call failed")
        return _json({"error": str(exc)}, status=502)

    # Stamp suggestions with server-side IDs so Apply can round-trip them.
    suggestions = []
    for i, s in enumerate(result.get("suggestions") or []):
        if not isinstance(s, dict):
            continue
        s2 = dict(s)
        s2.setdefault("id", f"sg_{i+1}_{_rand_token(4)}")
        suggestions.append(s2)

    return _json({
        "explanation": result.get("explanation", ""),
        "suggestions": suggestions,
    })


def _rand_token(n: int = 6) -> str:
    import secrets
    import string
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _slug(s: str, maxlen: int = 20) -> str:
    """Lower-case ASCII slug for filter / join IDs. Keeps the regex happy
    (`^pf_[a-z0-9_-]+$`)."""
    import re as _re
    s = (s or "").lower()
    s = _re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return (s or "x")[:maxlen]


@presentations_bp.route("/<pid>/scope/apply-suggestion", methods=["POST"])
@login_required
def apply_scope_suggestion(pid: str):
    """Apply one LLM suggestion against the draft scope sent by the client.

    Each ``kind`` maps to a specific mutation. The mutated scope is run
    through the §2.2 validators; warnings are returned but do not block
    (Apply is non-destructive — the frontend can still let the user reject
    a warning-laden mutation if it wants).

    Request: ``{"scope": <draft>, "suggestion": {"kind": ..., ...}}``
    Response: ``{"ok": true, "scope": <mutated draft>, "warnings": [...]}``
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    sugg = body.get("suggestion")
    if not isinstance(scope_in, dict) or not isinstance(sugg, dict):
        return _json({"ok": False, "error": "scope + suggestion required"}, status=400)

    kind = sugg.get("kind")
    try:
        mutated = _mutate_scope_with_suggestion(scope_in, sugg)
    except _ApplyError as exc:
        return _json({"ok": False, "error": str(exc), "kind": kind}, status=400)

    # Validate the mutated scope so the UI can surface schema/business issues.
    warnings: list[str] = []
    errors: list[str] = []
    try:
        loaded = load_scope_from_dict({"scope": mutated})
    except (ValidationError, ValueError) as exc:
        errors = _flatten(exc)
    else:
        try:
            vres = validate_scope(loaded, _catalog())
            warnings = list(vres.warnings or [])
            errors = list(vres.errors or [])
            # Refresh routing too — a freshly-pinned date filter typically
            # changes the cached/lazy decision (and the badge size).
            if not errors:
                _refresh_routing(loaded, _catalog())
                mutated = scope_to_dict(loaded)["scope"]
        except Exception:
            log.warning("apply_scope_suggestion: validator/routing failed", exc_info=True)

    return _json({
        "ok": not errors,
        "scope": mutated,
        "warnings": warnings,
        "errors": errors,
        "kind": kind,
    })


class _ApplyError(Exception):
    """Raised by the mutators when a suggestion cannot be applied as given."""


def _mutate_scope_with_suggestion(scope: dict, sugg: dict) -> dict:
    """Pure function: returns a deep-copied scope with the suggestion applied.

    The 5 kinds correspond to spec §5.3 + spec §6R aggregate handling.
    """
    import copy
    s = copy.deepcopy(scope)
    s.setdefault("filters", {}).setdefault("pinned", [])
    s["filters"].setdefault("interactive", [])
    s["filters"].setdefault("raw", [])
    s.setdefault("joins", [])
    s.setdefault("basket", [])

    kind = sugg.get("kind")
    if kind == "pin_filter":
        return _apply_pin_filter(s, sugg)
    if kind == "add_filter":
        return _apply_add_filter(s, sugg)
    if kind == "add_projection_column":
        return _apply_add_projection_column(s, sugg)
    if kind == "confirm_join":
        return _apply_confirm_join(s, sugg)
    if kind == "create_aggregate":
        return _apply_create_aggregate(s, sugg)
    raise _ApplyError(f"Bilinmeyen öneri tipi: {kind!r}")


def _apply_pin_filter(s: dict, sugg: dict) -> dict:
    fid = sugg.get("filter_id")
    if not fid:
        raise _ApplyError("pin_filter: filter_id eksik")
    interactive = s["filters"]["interactive"]
    for i, f in enumerate(interactive):
        if f.get("id") == fid:
            # Build pinned from interactive: concept + op + (default_values → values).
            pinned_id = f"pf_{_slug(f.get('concept','x'))}_{_rand_token(4)}"
            pf = {
                "id": pinned_id,
                "concept": f.get("concept"),
                "op": f.get("op"),
                "applies_to": f.get("applies_to") or [],
            }
            vals = f.get("default_values")
            if f.get("op") == "between" and isinstance(vals, list) and len(vals) == 2:
                pf["from"], pf["to"] = vals[0], vals[1]
            elif f.get("op") in ("in", "not_in"):
                pf["values"] = vals or []
            else:
                pf["value"] = (vals or [None])[0]
            s["filters"]["pinned"].append(pf)
            s["filters"]["interactive"].pop(i)
            return s
    raise _ApplyError(f"pin_filter: interactive '{fid}' bulunamadı")


def _apply_add_filter(s: dict, sugg: dict) -> dict:
    mode = sugg.get("mode") or "pinned"
    concept = sugg.get("concept")
    op = sugg.get("op")
    if not concept or not op:
        raise _ApplyError("add_filter: concept ve op zorunlu")
    applies_to = sugg.get("applies_to") or []
    if mode == "pinned":
        f = {
            "id": f"pf_{_slug(concept)}_{_rand_token(4)}",
            "concept": concept,
            "op": op,
            "applies_to": applies_to,
        }
        if op == "between":
            f["from"] = sugg.get("from")
            f["to"] = sugg.get("to")
        elif op in ("in", "not_in"):
            f["values"] = sugg.get("values") or []
        else:
            f["value"] = sugg.get("value")
        s["filters"]["pinned"].append(f)
    else:
        f = {
            "id": f"if_{_slug(concept)}_{_rand_token(4)}",
            "concept": concept,
            "op": op,
            "applies_to": applies_to,
            "default_values": sugg.get("default_values") or sugg.get("values") or [],
            "allowed_values": sugg.get("allowed_values"),
            "label": sugg.get("label"),
        }
        s["filters"]["interactive"].append(f)
    return s


def _apply_add_projection_column(s: dict, sugg: dict) -> dict:
    alias = sugg.get("alias")
    column = sugg.get("column")
    if not alias or not column:
        raise _ApplyError("add_projection_column: alias + column zorunlu")
    for b in s["basket"]:
        if b.get("alias") == alias:
            proj = b.setdefault("projection", {"columns": [], "include_all": False})
            cols = list(proj.get("columns") or [])
            if column not in cols:
                cols.append(column)
            proj["columns"] = cols
            proj["include_all"] = False
            return s
    raise _ApplyError(f"add_projection_column: alias '{alias}' basket'te yok")


def _apply_confirm_join(s: dict, sugg: dict) -> dict:
    la, lc = sugg.get("left_alias"), sugg.get("left_column")
    ra, rc = sugg.get("right_alias"), sugg.get("right_column")
    jkind = sugg.get("kind_of_join") or "inner"
    if not all([la, lc, ra, rc]):
        raise _ApplyError("confirm_join: left/right alias+column zorunlu")
    aliases = {b.get("alias") for b in s["basket"]}
    if la not in aliases or ra not in aliases:
        raise _ApplyError(f"confirm_join: alias basket'te yok ({la} / {ra})")
    # Reject duplicate join on same column pair.
    for j in s["joins"]:
        l = j.get("left") or {}
        r = j.get("right") or {}
        if {(l.get("alias"), l.get("column")), (r.get("alias"), r.get("column"))} == {(la, lc), (ra, rc)}:
            raise _ApplyError("confirm_join: bu join zaten kayıtlı")
    s["joins"].append({
        "id": f"j_{_slug(la)}_{_slug(ra)}_{_rand_token(3)}",
        "left": {"alias": la, "column": lc},
        "right": {"alias": ra, "column": rc},
        "kind": jkind,
    })
    return s


def _apply_create_aggregate(s: dict, sugg: dict) -> dict:
    src = sugg.get("source_alias")
    new_alias = sugg.get("new_alias")
    group_by = list(sugg.get("group_by") or [])
    measures = list(sugg.get("measures") or [])
    if not src or not new_alias:
        raise _ApplyError("create_aggregate: source_alias + new_alias zorunlu")
    aliases = {b.get("alias") for b in s["basket"]}
    if src not in aliases:
        raise _ApplyError(f"create_aggregate: source_alias '{src}' basket'te yok")
    if new_alias in aliases:
        raise _ApplyError(f"create_aggregate: '{new_alias}' alias'ı zaten mevcut")
    # Reject derived-from-derived (matches frontend rule: aggregate only from raw).
    src_item = next(b for b in s["basket"] if b.get("alias") == src)
    if src_item.get("derivation"):
        raise _ApplyError("create_aggregate: kaynak türetilmiş tablo olamaz (sadece raw)")
    if not (group_by or measures):
        raise _ApplyError("create_aggregate: group_by veya measures gerekli")
    s["basket"].append({
        "alias": new_alias,
        "derivation": {
            "kind": "aggregate",
            "source_alias": src,
            "group_by": group_by,
            "measures": [{"column": m["column"], "fn": m["fn"], "as": m.get("as") or f"{m['fn'].upper()}_{m['column']}"} for m in measures],
        },
        "projection": {
            "columns": list(group_by) + [m.get("as") or f"{m['fn'].upper()}_{m['column']}" for m in measures],
            "include_all": False,
        },
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
    })
    return s

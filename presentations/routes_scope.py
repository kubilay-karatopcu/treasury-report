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
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Response, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from pydantic import ValidationError

from presentations import presentations_bp
from presentations.blocks.store import _normalize_team_token
from presentations.catalog.loader import DEFAULT_SCHEMA_DEPARTMENT_MAP
from presentations.scope.catalog import AppCatalog
from presentations.scope.fetch import compile_filter_sql, compose_cached_sql, fetch_cached_tables
from presentations.scope.diff import diff_scopes, serialise_diff
from presentations.scope.impact import compute_affected_blocks, serialise_affected, summarise
from presentations.scope.routing import (
    DEFAULT_HARD_CEILING_BYTES,
    DEFAULT_THRESHOLD_BYTES,
    RoutingCeilingError,
    _bytes_per_row,
    apply_user_override,
    decide_routing,
)
from presentations.scope.size_estimate import (
    SizeEstimateStore,
    estimate_bytes_via_explain,
    fingerprint,
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


def _size_estimate_store() -> SizeEstimateStore:
    """The app-wide refined-size cache (madde 4). Lazily created so tests /
    run_local that don't pre-register one in app.config still work."""
    store = current_app.config.get("SIZE_ESTIMATE_STORE")
    if store is None:
        store = SizeEstimateStore()
        current_app.config["SIZE_ESTIMATE_STORE"] = store
    return store


def _unentitled_tables(scope: ScopeContract) -> list[str]:
    """Return ``SCHEMA.TABLE`` strings the current user may NOT fetch (#31).

    Entitlement is driven by the schema→department map (``PRESENTATIONS_SCHEMA_
    DEPARTMENT_MAP`` override, else the default). A schema is gated only when it
    is in the map AND the caller's department is one the map recognises; for a
    department the map doesn't know (a team not yet onboarded, or DEV's fake
    user) we allow + log rather than lock everyone out. Schemas absent from the
    map (user uploads, ad-hoc) are not gated here. The app runs DataClient under
    a single Oracle service account, so without this gate any authenticated user
    could pull any mapped schema's tables.
    """
    raw_map = (current_app.config.get("PRESENTATIONS_SCHEMA_DEPARTMENT_MAP")
               or DEFAULT_SCHEMA_DEPARTMENT_MAP)
    dept_map = {str(k).upper(): v for k, v in raw_map.items()}
    known_depts = {_normalize_team_token(d) for d in dept_map.values()}
    user_dept = _normalize_team_token(getattr(current_user, "department", "") or "")
    if user_dept not in known_depts:
        log.warning(
            "scope entitlement: department %r (user %s) is not in the schema-dept "
            "map; allowing all schemas. Add it to PRESENTATIONS_SCHEMA_DEPARTMENT_"
            "MAP to enforce.",
            getattr(current_user, "department", None),
            getattr(current_user, "sicil", None),
        )
        return []
    denied: list[str] = []
    for item in scope.basket:
        if item.table_ref is None:
            continue
        owner = dept_map.get(item.table_ref.schema_name.upper())
        if owner is None:
            continue  # schema not gated by the map
        if _normalize_team_token(owner) != user_dept:
            denied.append(f"{item.table_ref.schema_name}.{item.table_ref.name}")
    return denied


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
        # Recompute reverts to the catalog/partition estimate → drop any stale
        # "explain_plan" marker; refine-sizes re-sets it after EXPLAIN PLAN.
        item.routing.estimate_source = None


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
    """Resolve the scope to render on Hazırlık page load. Priority:

    1. session.manifest.draft_scope — the user's latest in-progress edits
       (tables / filters), auto-saved by /scope/save-draft on every change and
       CLEARED on build. A present draft is therefore always newer than the
       last built version (uncommitted edits), so it must win — otherwise the
       built version shadows those edits and the user loses them on the next
       reload or Keşif round-trip (filters silently dropped).
    2. SCOPE_STORE.load_latest(pid) — the last built version (draft cleared).
    3. Empty default — first visit on this presentation.
    """
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        manifest = sess.get_manifest() or {}
        draft = manifest.get("draft_scope")
        if isinstance(draft, dict) and (draft.get("basket") or []):
            return load_scope_from_dict({"scope": draft})
    except Exception:
        log.warning("hazirlik: draft load failed for %s", pid, exc_info=True)
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
    """Return the catalog as ``{domains: [...]}``.

    Source priority:
      1. ``CatalogLoader`` (Phase 9 / Atölye source of truth — reads
         ``examples/table_docs/<SCHEMA>/<TABLE>.yaml``). Tables are
         bucketed by schema so domain labels match Keşif's tree.
      2. Legacy curated ``catalog.json`` — fallback for environments
         that don't have a TableDocStore configured.

    This unification lets Atölye → Keşif → Hazırlık → Sunum share the
    same table universe: the basket items the user picks in Keşif
    actually resolve in Hazırlık.
    """
    # Try the CatalogLoader first.
    try:
        from presentations.catalog.api import _get_loader
        loader = _get_loader()
        sicil = getattr(current_user, "sicil", None)
        entries = loader.load(user_sicil=sicil)
        if entries:
            return _domains_from_catalog_entries(entries)
    except Exception:
        log.warning("_catalog_json: CatalogLoader path failed, falling back to catalog.json",
                    exc_info=True)
    # Fallback to the static catalog.json.
    from presentations.routes import _catalog_path
    try:
        return json.loads(_catalog_path().read_text(encoding="utf-8"))
    except Exception:
        return {"domains": []}


def _domains_from_catalog_entries(entries) -> dict[str, Any]:
    """Build ``{domains: [...]}`` from a list of CatalogLoader ``TableEntry``
    records, bucketing by schema name. Frontend re-groups by schema
    anyway, but emitting them already-grouped means the per-domain
    counts are accurate without a second pass.
    """
    by_schema: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        d = e.model_dump(by_alias=True, mode="json", exclude_none=True)
        schema = d.get("schema") or "Diğer"
        name = d.get("name") or ""
        if not name:
            continue
        tid = f"{schema}.{name}"
        # Translate column shape — TableEntry uses {name, type, concept?}.
        # The Hazırlık frontend expects the same {name, type} pair.
        cols = []
        for c in (d.get("columns") or []):
            col = {
                "name": c.get("name"),
                "type": c.get("type") or c.get("data_type") or "",
            }
            if "concept" in c and c["concept"]:
                col["concept"] = c["concept"]
            if "description" in c and c["description"]:
                col["description"] = c["description"]
            if "nullable" in c:
                col["nullable"] = c["nullable"]
            cols.append(col)
        table_record = {
            "id": tid,
            "desc": d.get("description") or "",
            "engine": d.get("engine") or "oracle",
            "columns": cols,
            "common_filters": d.get("common_filters") or [],
        }
        # Row-count hint, if the YAML provided one.
        row_count = d.get("row_count") or d.get("rows")
        if row_count:
            table_record["rows"] = (
                f"{row_count:,}".replace(",", ".")
                if isinstance(row_count, (int, float))
                else str(row_count)
            )
        by_schema.setdefault(schema, []).append(table_record)
    domains = []
    for schema in sorted(by_schema.keys()):
        domains.append({
            "id": f"schema_{schema}",
            "label": schema,
            "tables": by_schema[schema],
        })
    return {"domains": domains}


def _uploads_domain_from_session(pid: str) -> dict[str, Any] | None:
    """Build the synthetic ``dom_uploads`` domain from the session manifest's
    uploads (mirrors the shape ``GET /<pid>/sources`` produces). Returns
    ``None`` when the user has no uploads on this presentation."""
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        manifest = sess.get_manifest() or {}
    except Exception:
        return None
    uploads = manifest.get("uploads") or []
    if not uploads:
        return None
    tables: list[dict[str, Any]] = []
    for u in uploads:
        for sheet in u.get("sheets") or []:
            table_id = f"upload__{u['id']}__{sheet['name']}"
            tables.append({
                "id": table_id,
                "desc": f"{u.get('filename', '')} — {sheet.get('display_name', sheet['name'])}",
                "rows": f"{sheet.get('row_count', 0):,}".replace(",", "."),
                "engine": "duckdb",
                "columns": [
                    {"name": c["name"], "type": c["type"], "nullable": c.get("nullable", True)}
                    for c in sheet.get("columns", [])
                ],
                "common_filters": [],
                "_upload_id": u["id"],
                "_sheet_name": sheet.get("display_name", sheet["name"]),
            })
    return {
        "id": "dom_uploads", "label": "Yüklenenler",
        "icon": "upload", "engine": "duckdb", "tables": tables,
    }


def _catalog_json_enriched(pid: str | None = None) -> dict[str, Any]:
    """Catalog JSON enriched with per-column ``concept`` from the table-doc
    store. The frontend's ``addTableFromCatalog`` reads this so concept
    information survives the catalog → basket transition (without which the
    LLM's ``applies_to: []`` filter footers can't be rendered on the node).

    When the underlying catalog source is the new CatalogLoader path (which
    strips ``columns`` from list-level entries for compactness), we
    populate columns here from the TABLE_DOC_STORE directly. This keeps
    Hazırlık's sidebar functional regardless of which catalog backend is
    used.

    When ``pid`` is given, the synthetic ``dom_uploads`` domain (built from
    the session manifest's uploads) is appended so Hazırlık picks up
    user-uploaded sheets without a separate /sources round-trip.
    """
    cat = _catalog_json()
    for d in (cat.get("domains") or []):
        for t in (d.get("tables") or []):
            tid = t.get("id") or ""
            if "." not in tid:
                continue
            schema, name = tid.split(".", 1)
            cols_meta = _columns_for(schema, name)
            existing_cols = t.get("columns") or []
            if not existing_cols and cols_meta:
                # Catalog source skipped columns — backfill from table-doc.
                t["columns"] = [
                    {
                        "name": c["name"],
                        "type": c.get("type") or "",
                        "concept": c.get("concept"),
                        "filter_role": c.get("filter_role"),
                    }
                    for c in cols_meta
                ]
            else:
                # Columns already present — only patch in concept tags.
                concept_by_col = {c["name"]: c.get("concept") for c in cols_meta}
                for col in existing_cols:
                    if col.get("name") in concept_by_col and concept_by_col[col["name"]]:
                        col["concept"] = concept_by_col[col["name"]]
    if pid:
        uploads = _uploads_domain_from_session(pid)
        if uploads:
            cat.setdefault("domains", []).append(uploads)
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
    # Phase 7 human-verified bindings override the Phase 6.5.b suggested tag, so
    # a column bound via the documentation UI reads as concept-bound everywhere
    # the frontend uses this: the grid concept chip, agModelToFilters (which
    # routes concept columns to *pinned* filters — the only kind routing can use
    # to shrink a table), and suggested join edges. Matches AppCatalog/compiler.
    bind_concept: dict[str, str] = {}
    bc = current_app.config.get("CONCEPT_BINDING_CATALOG")
    if bc is not None:
        try:
            for b in bc.get_bindings(schema, name):
                if getattr(b, "column", None) and getattr(b, "concept", None):
                    bind_concept[b.column] = b.concept
        except Exception:
            pass
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
            "concept": bind_concept.get(col) or getattr(cd, "suggested_semantic_tag", None),
            "filter_role": fr,
            "join_key": join_key,
            "lookup": ({"table": lk.table, "key": lk.key, "display": lk.display} if lk else None),
        })
    return out


def _manifest_basket_from_scope(scope: ScopeContract) -> list[dict[str, Any]]:
    """Derive the Sunum ``manifest.basket`` (legacy ``{table, ...}`` shape read
    by the editor sidebar) from the authoritative Hazırlık ``scope.basket``.

    Without this, manual-SQL and derived (filter/aggregate) nodes built in
    Hazırlık never reach Sunum's "Veri Kaynakları" list (they live only in
    ``scope.basket``, never the manifest basket Keşif populated). The scope
    reaching build is already active-only (the Hazırlık ``_finalisedScope``
    strips ``inactive_aliases`` before posting), so every basket item here is a
    node the user wants in Sunum. SQL / derivation entries carry a ``source``
    (and ``derivation_kind``) so the sidebar can badge them; their ``table`` is
    the alias (= the DuckDB view name) since there is no Oracle table id.

    ``scope.inactive_aliases`` here lists sources the user PASSİVE'd in Hazırlık
    that were only re-added to the basket because an active derived node needs
    them to materialise. They stay in the scope (so the derived node builds) but
    are HIDDEN from the Sunum sidebar — otherwise a table the user disabled would
    still show up in Sunum as a data source.
    """
    hidden = set(scope.inactive_aliases or [])
    out: list[dict[str, Any]] = []
    for b in scope.basket:
        if b.alias in hidden:
            continue
        if b.table_ref is not None:
            cols = [] if b.projection.include_all else list(b.projection.columns or [])
            out.append({
                "table": f"{b.table_ref.schema_name}.{b.table_ref.name}",
                "alias": b.alias,
                "columns": cols,
                "source": "table",
            })
        elif b.sql is not None:
            out.append({
                "table": b.alias, "alias": b.alias, "columns": [],
                "source": "sql",
            })
        elif b.derivation is not None:
            out.append({
                "table": b.alias, "alias": b.alias, "columns": [],
                "source": "derived", "derivation_kind": b.derivation.kind,
            })
    return out


def _columns_by_alias(scope: ScopeContract) -> dict[str, list[dict[str, Any]]]:
    # Derivation basket items carry no ``table_ref`` (their columns come from a
    # transform, not a table doc); skip them like every other call site does.
    return {
        b.alias: _columns_for(b.table_ref.schema_name, b.table_ref.name)
        for b in scope.basket
        if b.table_ref is not None
    }


def _suggested_edges(scope: ScopeContract, cols_by_alias: dict[str, list[dict[str, Any]]]):
    """Auto-suggested join edges between basket aliases (§6R.3): FK ``lookup``
    declarations + columns sharing a concept. The frontend draws these
    (deduped against confirmed scope.joins) and the user can confirm/delete.

    Each edge carries an ``concept`` field — for FK lookups it's the lookup's
    ``display`` column's concept (when known), for shared-concept edges it's
    the concept that proposed the join. Used by the UI to render a concept
    chip on the edge label so the user understands *why* the join is being
    suggested.
    """
    edges: list[dict[str, Any]] = []
    seen: set = set()
    by_name: dict[str, str] = {}
    for b in scope.basket:
        if b.table_ref is not None:
            by_name.setdefault(b.table_ref.name, b.alias)

    def add(la, lc, ra, rc, kind, source, concept=None):
        if la == ra:
            return
        key = tuple(sorted([(la, lc), (ra, rc)]))
        if key in seen:
            return
        seen.add(key)
        edges.append({
            "left": {"alias": la, "column": lc},
            "right": {"alias": ra, "column": rc},
            "kind": kind, "source": source,
            "concept": concept,
        })

    def _concept_for(alias, col_name):
        for c in (cols_by_alias.get(alias) or []):
            if c.get("name") == col_name:
                return c.get("concept")
        return None

    # (a) FK lookup → solid suggestion.
    for alias, cols in cols_by_alias.items():
        for col in cols:
            lk = col.get("lookup")
            if not lk or lk["table"] not in by_name:
                continue
            # The lookup's "concept" is the source column's concept (if any) —
            # both sides should share it after the join lands.
            concept = col.get("concept") or _concept_for(by_name[lk["table"]], lk["key"])
            add(alias, col["name"], by_name[lk["table"]], lk["key"],
                "lookup", "catalog_lookup", concept=concept)

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
                add(la, lc, ra, rc, "inner", f"shared_concept:{c}", concept=c)
    return edges


@presentations_bp.route("/hazirlik")
@login_required
def hazirlik_new():
    """Menu entry point (R › Hazırlık).

    Behaviour:
      - ``?new=1`` always mints a fresh pid (use it from the menu when the
        user wants a clean slate).
      - Otherwise redirect to the user's most recently-touched presentation
        — including drafts auto-saved into the session manifest. This way
        every time the user opens "Hazırlık" they land back on what they
        were working on, with the basket + connections already rendered.
      - If the user has no presentations at all, fall through to a freshly
        minted pid.
    """
    import secrets
    if request.args.get("new") != "1":
        try:
            sicil = getattr(current_user, "sicil", None)
            recent = _registry().list_user_presentations(sicil) if sicil else []
            # Find the newest presentation that actually has a non-empty
            # basket (either a built scope or an auto-saved draft). A pid
            # with only a placeholder manifest gives the user an empty
            # canvas, which defeats the redirect's whole purpose.
            for item in recent:
                pid = item.get("id")
                if not pid:
                    continue
                try:
                    sess = _registry().get_or_create(sicil, pid)
                    manifest = sess.get_manifest() or {}
                except Exception:
                    continue
                draft = manifest.get("draft_scope")
                if isinstance(draft, dict) and (draft.get("basket") or []):
                    return redirect(url_for("presentations.hazirlik", pid=pid))
                # Built scope counts too — check the scope store.
                try:
                    store = current_app.config.get("SCOPE_STORE")
                    sc = store.load_latest(pid) if store else None
                    if sc is not None and sc.basket:
                        return redirect(url_for("presentations.hazirlik", pid=pid))
                except Exception:
                    pass
        except Exception:
            log.warning("hazirlik_new: list_user_presentations failed", exc_info=True)
    pid = "p_" + secrets.token_urlsafe(8)
    return redirect(url_for("presentations.hazirlik", pid=pid))


def _seed_basket_from_query(scope: ScopeContract, seed_param: str) -> None:
    """Append basket items for any catalog table IDs in ``seed_param``
    that aren't already in the scope. Used by the ``?seed=ID1,ID2``
    deeplink that lets Phase 9 (Keşif) drop the user into Hazırlık with
    a starter basket. Unknown IDs are silently skipped (avoids breaking
    a deeplink when the catalog reshuffles).

    Idempotent: re-loading the same URL doesn't create duplicate basket
    entries. Skip is by ``table_ref`` identity (schema + name), not by
    alias — the alias is derived from the name and would otherwise just
    get a ``_2`` suffix on each reload."""
    if not seed_param:
        return
    ids = [s.strip() for s in seed_param.split(",") if s.strip()]
    if not ids:
        return
    have_aliases = {b.alias for b in scope.basket}
    have_tables = {
        f"{b.table_ref.schema_name}.{b.table_ref.name}"
        for b in scope.basket if b.table_ref is not None
    }
    cat = _catalog_json()
    table_by_id: dict[str, dict] = {}
    for d in (cat.get("domains") or []):
        for t in (d.get("tables") or []):
            tid = t.get("id")
            if tid:
                table_by_id[tid] = t
    for tid in ids:
        t = table_by_id.get(tid)
        if t is None or "." not in tid:
            continue
        # Idempotency: a table already in the basket is a no-op.
        if tid in have_tables:
            continue
        schema, name = tid.split(".", 1)
        # Columns may be empty on the catalog list payload (CatalogLoader
        # strips them for compactness). Fall back to the TableDocStore
        # directly so the seeded basket has all known columns by default.
        cat_cols = [c.get("name") for c in (t.get("columns") or []) if c.get("name")]
        if not cat_cols:
            cat_cols = [c["name"] for c in _columns_for(schema, name) if c.get("name")]
        base = re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "t"
        if not re.match(r"^[a-z]", base):
            base = "t_" + base
        base = base[:40]
        alias = base
        i = 2
        while alias in have_aliases:
            alias = f"{base}_{i}"[:40]
            i += 1
        have_aliases.add(alias)
        have_tables.add(tid)
        from presentations.scope.schema import (
            BasketItem as _BI, Projection as _P, Routing as _R, TableRef as _TR,
        )
        scope.basket.append(_BI(
            alias=alias,
            table_ref=_TR(schema=schema, name=name),
            projection=_P(columns=cat_cols, include_all=False),
            routing=_R(decision="cached", decided_by="system", estimated_bytes=0),
        ))


@presentations_bp.route("/hazirlik/<pid>")
@login_required
def hazirlik(pid: str):
    """The Hazırlık (Stage 2 / Prepare) screen. Renders the React bundle with
    the current scope contract, the table catalog, available concepts, and
    concept value distributions embedded as JSON.

    Supports ``?seed=EDW.X,EDW.Y`` so external flows (Phase 9 Keşif, link-
    shares) can deeplink with a starter basket. Tables already in the
    persisted scope are skipped.
    """
    scope = _load_latest_scope_or_draft(pid)
    seed_param = request.args.get("seed") or ""
    if seed_param:
        before = len(scope.basket)
        _seed_basket_from_query(scope, seed_param)
        # Persist immediately so a reload (without ?seed) keeps the basket.
        # The frontend auto-save kicks in for subsequent mutations.
        if len(scope.basket) > before:
            try:
                sess = _registry().get_or_create(current_user.sicil, pid)
                manifest = sess.get_manifest() or {}
                manifest["draft_scope"] = scope_to_dict(scope)["scope"]
                sess.set_manifest(manifest)
            except Exception:
                log.warning("hazirlik: seed-draft persist failed", exc_info=True)
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
    # Phase 11.hazirlik-polish: surface the manifest's library-block basket
    # items (kind="block") so the Hazırlık sidebar can show them next to
    # the table list. Blocks don't participate in scope (they're already
    # rendered React components), but the user expects "my basket" to
    # include both flavors.
    library_blocks: list[dict] = []
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        m = sess.get_manifest() or {}
        for it in (m.get("basket") or []):
            if (it or {}).get("kind") != "block":
                continue
            library_blocks.append({
                "library_id": it.get("library_id") or "",
                "name": it.get("name") or it.get("library_id") or "",
                "block_type": it.get("block_type") or "",
                "owner_id": it.get("owner_id") or "",
                "tags": it.get("tags") or [],
            })
    except Exception:
        log.warning("hazirlik: library_blocks lookup failed", exc_info=True)

    payload = {
        "presentation_id": pid,
        "title": title,
        "scope": scope_to_dict(scope)["scope"],
        "catalog": _catalog_json_enriched(pid),
        "concepts": _concepts_payload(),
        "distributions": _distributions_payload(scope),
        "columns_by_alias": cols_by_alias,
        "suggested_edges": _suggested_edges(scope, cols_by_alias),
        "routing_config": {
            "threshold_bytes": _routing_threshold_bytes(),
            "hard_ceiling_bytes": _routing_hard_ceiling_bytes(),
        },
        "library_blocks": library_blocks,
    }
    return render_template(
        "presentations/hazirlik.html",
        presentation_id=pid,
        title=title,
        hazirlik_json=json.dumps(payload, ensure_ascii=False, default=_json_default),
    )


def _parent_scope(pid: str):
    """The most-recently-built scope for ``pid``, used to (a) anchor
    parent_version and (b) compute a re-entry diff. ``None`` when this is a
    first-time build."""
    store = current_app.config.get("SCOPE_STORE")
    if store is None:
        return None
    try:
        return store.load_latest(pid)
    except Exception:
        log.warning("_parent_scope: load_latest failed for %s", pid, exc_info=True)
        return None


@presentations_bp.route("/<pid>/scope/preview-build", methods=["POST"])
@login_required
def preview_scope_build(pid: str):
    """Dry-run for 'Sunum'a geç': validates the proposed scope + computes a
    diff vs the persisted parent + identifies affected manifest blocks, but
    does NOT save or fetch. The Hazırlık warning modal calls this before the
    real build so the user sees what will change (§3.6 step g).

    Response:
        {
          "ok": bool,
          "diff": {...},                     # serialise_diff() output
          "affected_blocks": [...],          # serialise_affected() output
          "summary": {breaking, warning, total},
          "parent_version": int | null,
          "errors": [...], "warnings": [...] # validator output
        }
    """
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
    _refresh_routing(scope, catalog)
    vres = validate_scope(scope, catalog)

    parent = _parent_scope(pid)
    diff = diff_scopes(parent, scope)

    # Manifest impact — pull from the active session's manifest (the
    # in-progress one) rather than the stored scope. That's what the user
    # is actually editing in Sunum.
    manifest = None
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        manifest = sess.get_manifest()
    except Exception:
        log.warning("preview_scope_build: get_manifest failed", exc_info=True)

    affected = compute_affected_blocks(diff, manifest)

    return _json({
        "ok": vres.ok,
        "diff": serialise_diff(diff),
        "affected_blocks": serialise_affected(affected),
        "summary": summarise(affected),
        "parent_version": parent.version if parent is not None else None,
        "errors": list(vres.errors or []),
        "warnings": list(vres.warnings or []),
    })


def _prepare_build(pid: str):
    """Build öncesi ortak hazırlık: body parse + entitlement + routing refresh +
    validasyon + parent/diff. Başarıda ``((scope, parent, diff), None)``,
    hatada ``(None, <Flask Response>)`` döner. Sync /scope/build ve async
    /scope/build-async aynı hazırlığı paylaşır."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, _json({"ok": False, "errors": ["body must be a JSON object"]}, status=400)
    try:
        scope = load_scope_from_dict(body)
    except (ValidationError, ValueError) as exc:
        return None, _json({"ok": False, "phase": "schema", "errors": _flatten(exc)}, status=400)

    scope.presentation_id = pid
    scope.created_by = getattr(current_user, "sicil", None) or scope.created_by

    # #31 — table entitlement: refuse to fetch schemas the caller isn't entitled
    # to (the fetch below runs under the app's Oracle service account).
    denied = _unentitled_tables(scope)
    if denied:
        return None, _json({
            "ok": False, "phase": "entitlement",
            "errors": [f"Bu tablolara erişim yetkin yok: {', '.join(denied)}"],
        }, status=403)

    catalog = _catalog()
    # Refresh routing so the fetch pass acts on the current estimate, not on
    # whatever the client posted (which is a hint but never authoritative).
    _refresh_routing(scope, catalog)
    result = validate_scope(scope, catalog)
    if not result.ok:
        return None, _json({"ok": False, "phase": "validation",
                            "errors": result.errors, "warnings": result.warnings}, status=400)

    # Re-entry diff against the persisted parent.
    parent = _parent_scope(pid)
    if parent is not None:
        scope.parent_version = parent.version
    diff = diff_scopes(parent, scope)
    return (scope, parent, diff), None


def _run_build_core(pid: str, scope: ScopeContract, user_sicil: str,
                    parent, diff, *, on_progress=None) -> dict[str, Any]:
    """Fetch → status → SCOPE_STORE.save → manifest güncellemesi.

    Sync build endpoint'i ile async build worker'ının ORTAK çekirdeği. Flask
    app context içinde çağrılmalı; request context GEREKMEZ (url_for yok —
    redirect'i çağıran taraf üretir, worker thread'de request yoktur).
    ``on_progress(alias)`` her dataset hazır olduğunda çağrılır (overlay listesi).
    """
    dc = current_app.config.get("DATA_CLIENT")
    session = _registry().get_or_create(user_sicil, pid)
    lazy = [b.alias for b in scope.basket
            if b.table_ref is not None and b.routing.decision == "lazy"]

    # Partial refresh planning. First build (no parent) → full fetch.
    # Otherwise: refetch only aliases the diff flags as added or changed +
    # any cached alias targeted by a changed pinned filter. Drop views for
    # aliases the new scope no longer carries.
    refetch_only = None
    drop_aliases = None
    if parent is not None:
        refetch_only = diff.affected_aliases
        drop_aliases = set(diff.removed_aliases)

    # Build-time one-shot parquet materialize: fetch sırasında elimizdeki
    # DataFrame aynı anda S3 parquet'e yazılır → Sunum / pod-restart parquet'ten
    # okur (load_into_duck), kind=manual dataset'ler de ilk andan görünür
    # (önceden yalnız cron, yalnız scheduled yazıyordu). Best-effort: S3
    # hıçkırığı build'i düşürmez — oturum DuckDB'sinde veri zaten var.
    from presentations.scope.materialize import write_dataset

    def _on_dataset(alias: str, df, sql: str) -> None:
        try:
            if df is not None and len(df.columns) > 0:
                write_dataset(dc, pid, alias, df, sql=sql)
        except Exception:
            log.warning("build: parquet materialize failed for %s", alias, exc_info=True)
        if on_progress is not None:
            try:
                on_progress(alias)
            except Exception:
                pass

    # Passive ("pasif") nodes are stripped from the basket by the Hazırlık
    # _finalisedScope before build. The only exception is a passive source that
    # an ACTIVE derived node needs to materialise — it stays in the basket but is
    # listed in scope.inactive_aliases so _manifest_basket_from_scope hides it
    # from the Sunum sidebar (the fetch pass still materialises it for the node).
    try:
        with session.duck_conn() as conn:
            loaded = fetch_cached_tables(
                dc, conn, scope,
                catalog=_catalog(),
                concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
                binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
                refetch_only=refetch_only,
                drop_aliases=drop_aliases,
                on_dataset=_on_dataset,
            )
    except Exception as exc:
        scope.status.state = "failed"
        scope.status.errors = [str(exc)]
        try:
            current_app.config["SCOPE_STORE"].save(scope)
        except Exception:
            log.warning("build: persist of failed scope failed", exc_info=True)
        return {"ok": False, "phase": "fetch", "errors": [str(exc)]}

    scope.status.state = "ready"
    # cached_tables includes every cached alias in the new scope — the ones
    # we refetched + the ones inherited from the parent. We trust the
    # session's existing views for the latter.
    inherited_cached: list[str] = []
    if parent is not None:
        new_aliases = {b.alias for b in scope.basket
                       if b.table_ref is not None and b.routing.decision == "cached"}
        prev_cached = set(parent.status.cached_tables or [])
        inherited_cached = sorted(new_aliases & prev_cached - set(loaded))
    scope.status.cached_tables = sorted(set(loaded.keys()) | set(inherited_cached))
    scope.status.lazy_tables = lazy
    scope.status.fetched_at = datetime.now(timezone.utc)

    version = current_app.config["SCOPE_STORE"].save(scope)

    manifest = session.get_manifest() or {
        "id": pid, "version": 0, "owner_id": user_sicil,
        "meta": {"title": pid, "eyebrow": "", "date": "", "author_label": user_sicil},
        "blocks": [],
    }
    manifest["scope_ref"] = {"presentation_id": pid, "scope_version": version}
    manifest["version"] = int(manifest.get("version", 0)) + 1
    # Rebuild the Sunum sidebar basket from the prepared scope so manual-SQL
    # and derived nodes (and only *visible* nodes) actually surface there.
    # Library blocks (kind="block") live in the manifest basket too — preserve
    # them alongside the regenerated table entries.
    blocks_in_basket = [
        b for b in (manifest.get("basket") or [])
        if (b or {}).get("kind") == "block"
    ]
    manifest["basket"] = _manifest_basket_from_scope(scope) + blocks_in_basket
    # Data is already in DuckDB via fetch_cached_tables above; prime the
    # signature so the legacy duckdb_preview refetch path (populate_basket)
    # doesn't re-pull these (and never tries to Oracle-fetch a sql/derived
    # alias, which has no real table).
    try:
        session._last_basket_signature = session.basket_signature(manifest["basket"])
    except Exception:
        log.warning("build: priming basket signature failed", exc_info=True)
    # Built → this version is authoritative. Drop the in-progress draft so the
    # next Hazırlık load uses the build, not a now-stale draft. (Draft-first
    # priority in _load_latest_scope_or_draft relies on this clear.)
    manifest.pop("draft_scope", None)
    session.set_manifest(manifest)

    return {
        "ok": True,
        "scope_version": version,
        "parent_version": parent.version if parent is not None else None,
        "cached_tables": scope.status.cached_tables,
        "lazy_tables": scope.status.lazy_tables,
        "refetched": sorted(loaded.keys()),
        "inherited": inherited_cached,
    }


@presentations_bp.route("/<pid>/scope/build", methods=["POST"])
@login_required
def build_scope(pid: str):
    """'Sunum'a geç': validate → fetch cached tables into DuckDB → persist scope
    (version bump) → write the manifest's scope_ref → return a redirect URL.
    Lazy tables are recorded in status but not fetched (8.d).

    Re-entry (§3.6, §8.e): when a previous scope (``parent``) exists, the
    new scope's ``parent_version`` is set to it. The fetch pass is partial —
    only aliases that the diff says actually changed get re-pulled from
    Oracle; unchanged cached aliases keep their existing DuckDB views.
    """
    prep, err = _prepare_build(pid)
    if err is not None:
        return err
    scope, parent, diff = prep
    payload = _run_build_core(pid, scope, current_user.sicil, parent, diff)
    if not payload.get("ok"):
        return _json(payload, status=502)
    payload["redirect"] = url_for("presentations.editor", pid=pid)
    return _json(payload)


# Async build job registry — pod-lokal, kısa ömürlü ({job_id: durum}). Worker
# thread'i yazar (done listesi / phase / result), status endpoint'i okur.
_BUILD_JOBS: dict[str, dict[str, Any]] = {}
_BUILD_JOBS_LOCK = threading.Lock()
_BUILD_JOB_TTL_SECONDS = 900


def _build_jobs_gc() -> None:
    cutoff = time.time() - _BUILD_JOB_TTL_SECONDS
    with _BUILD_JOBS_LOCK:
        for k in [k for k, v in _BUILD_JOBS.items() if v.get("ts", 0.0) < cutoff]:
            _BUILD_JOBS.pop(k, None)


@presentations_bp.route("/<pid>/scope/build-async", methods=["POST"])
@login_required
def build_scope_async(pid: str):
    """'Sunum'a geç' (async): validasyon senkron yapılır (hata aynı yanıtla
    döner), fetch + persist arka plan thread'inde koşar. Yanıt ``{job_id}``;
    Hazırlık overlay'i ``/scope/build-status/<job_id>``'yi poll'layıp dataset
    bazında ilerlemeyi gösterir, bitince redirect'e gider. (SSE değil polling —
    OpenShift proxy buffering'inden etkilenmez.)"""
    prep, err = _prepare_build(pid)
    if err is not None:
        return err
    scope, parent, diff = prep

    import uuid as _uuid
    job_id = "bj_" + _uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "phase": "fetch", "done": [], "error": None, "result": None,
        "redirect": url_for("presentations.editor", pid=pid),
        "ts": time.time(),
    }
    with _BUILD_JOBS_LOCK:
        _BUILD_JOBS[job_id] = job
    _build_jobs_gc()

    app = current_app._get_current_object()
    sicil = current_user.sicil

    def _worker():
        with app.app_context():
            try:
                payload = _run_build_core(
                    pid, scope, sicil, parent, diff,
                    on_progress=lambda alias: job["done"].append(alias),
                )
            except Exception as exc:   # beklenmedik — core kendi hatasını dict döner
                log.exception("build-async worker crashed for %s", pid)
                payload = {"ok": False, "errors": [str(exc)]}
            job["result"] = payload
            if payload.get("ok"):
                job["phase"] = "done"
            else:
                job["phase"] = "failed"
                job["error"] = " · ".join(payload.get("errors") or ["Bilinmeyen hata"])

    threading.Thread(target=_worker, daemon=True,
                     name=f"scope-build-{pid}").start()
    return _json({"ok": True, "job_id": job_id})


@presentations_bp.route("/<pid>/scope/build-status/<job_id>", methods=["GET"])
@login_required
def build_status(pid: str, job_id: str):
    job = _BUILD_JOBS.get(job_id)
    if job is None:
        return _json({"ok": False, "error": "İş bulunamadı (süresi dolmuş olabilir)."},
                     status=404)
    out: dict[str, Any] = {
        "ok": True, "phase": job["phase"],
        "done": list(job["done"]), "error": job["error"],
    }
    if job["phase"] == "done":
        out["redirect"] = job["redirect"]
        res = job.get("result") or {}
        out["scope_version"] = res.get("scope_version")
    return _json(out)


@presentations_bp.route("/<pid>/scope/projection-update", methods=["POST"])
@login_required
def scope_projection_update(pid: str):
    """Update a basket alias's projection (selected columns) — 8.c.

    Request: ``{"scope": <draft>, "alias": "...", "columns": [...], "include_all": false}``

    Validates that:
      - the alias exists and isn't a derived item (derived projections are
        computed from the derivation, not user-editable);
      - every requested column appears in the table's catalog;
      - no confirmed join references a column being removed (returns 400 with
        the list of affected join ids so the UI can offer to drop them).

    Response on success: ``{"ok": true, "scope": <mutated>}``.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    alias = (body.get("alias") or "").strip()
    columns = body.get("columns")
    include_all = bool(body.get("include_all", False))
    if not isinstance(scope_in, dict) or not alias:
        return _json({"ok": False, "error": "scope ve alias zorunlu"}, status=400)
    if not include_all and not isinstance(columns, list):
        return _json({"ok": False, "error": "columns array gerekli (veya include_all=true)"}, status=400)

    import copy
    s = copy.deepcopy(scope_in)
    item = next((b for b in (s.get("basket") or []) if b.get("alias") == alias), None)
    if item is None:
        return _json({"ok": False, "error": f"alias '{alias}' basket'te yok"}, status=400)
    if item.get("derivation") is not None or not item.get("table_ref"):
        return _json({"ok": False,
                      "error": "türetilmiş tablolar için projection kullanıcı tarafından düzenlenemez"},
                     status=400)

    # Validate every requested column against the catalog.
    schema = item["table_ref"]["schema"]
    name = item["table_ref"]["name"]
    catalog_cols = {c["name"] for c in (_columns_for(schema, name) or [])}
    # Fall back to the catalog JSON (table-doc may be absent for raw catalog
    # entries) so the picker isn't gated on a missing table-doc.
    if not catalog_cols:
        full = _catalog_json()
        for d in (full.get("domains") or []):
            for t in (d.get("tables") or []):
                if t.get("id") == f"{schema}.{name}":
                    catalog_cols = {c.get("name") for c in (t.get("columns") or []) if c.get("name")}
                    break
    if not include_all:
        unknown = [c for c in columns if c not in catalog_cols]
        if unknown:
            return _json({"ok": False,
                          "error": f"Catalog'da olmayan kolon(lar): {', '.join(unknown)}"},
                         status=400)
        if not columns:
            return _json({"ok": False, "error": "Projection en az 1 kolon içermeli"}, status=400)

    # Reject if a confirmed join references a column being dropped.
    if not include_all:
        new_set = set(columns)
        affected: list[dict[str, Any]] = []
        for j in (s.get("joins") or []):
            for side in ("left", "right"):
                p = j.get(side) or {}
                if p.get("alias") == alias and p.get("column") not in new_set:
                    affected.append({"join_id": j.get("id"), "side": side, "column": p.get("column")})
        if affected:
            return _json({
                "ok": False,
                "error": "Bu kolonları kaldıramazsın — kayıtlı bir join'e referans veriyorlar.",
                "blocked_by_joins": affected,
            }, status=400)

    item.setdefault("projection", {})
    item["projection"]["include_all"] = include_all
    item["projection"]["columns"] = list(columns) if not include_all else []

    # Routing depends on projection (bytes/row estimate) — refresh.
    try:
        loaded = load_scope_from_dict({"scope": s})
        _refresh_routing(loaded, _catalog())
        s = scope_to_dict(loaded)["scope"]
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)

    return _json({"ok": True, "scope": s})


@presentations_bp.route("/<pid>/scope/save-draft", methods=["POST"])
@login_required
def scope_save_draft(pid: str):
    """Persist an in-progress scope to the session manifest so a page reload
    doesn't lose the user's basket + filters before they click "Sunum'a geç".

    The draft lives at ``manifest.draft_scope`` and is read by
    :func:`_load_latest_scope_or_draft` when SCOPE_STORE has no built
    version yet. Schema is NOT validated here — drafts may be transiently
    inconsistent while the user is mid-edit; the build endpoint does the
    real validation.

    Frontend calls this debounced (~500ms after last mutation) so the
    server doesn't see every keystroke.
    """
    body = request.get_json(silent=True) or {}
    scope = body.get("scope")
    if not isinstance(scope, dict):
        return _json({"ok": False, "error": "scope required"}, status=400)
    try:
        sess = _registry().get_or_create(current_user.sicil, pid)
        manifest = sess.get_manifest() or {}
        manifest["draft_scope"] = scope
        sess.set_manifest(manifest)
    except Exception as exc:
        log.warning("scope_save_draft: persist failed", exc_info=True)
        return _json({"ok": False, "error": str(exc)}, status=502)
    return _json({"ok": True})


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


@presentations_bp.route("/<pid>/scope/refine-sizes", methods=["POST"])
@login_required
def scope_refine_sizes(pid: str):
    """Filter-aware size refinement via EXPLAIN PLAN (madde 4).

    The catalog-only estimator (``recompute-routing``) only shrinks a table for
    a pinned date range on its partition column. This endpoint refines the byte
    estimate for *every* raw basket table using the Oracle optimizer's
    cardinality, so non-partition filters (status / currency / segment …) move
    the node size badge too.

    For each raw table we compose the same projected, filtered SELECT the fetch
    pass would run, fingerprint it, and:

    - return a **fresh cached** estimate immediately (``estimates[alias]``); or
    - **enqueue** a background EXPLAIN PLAN job (deduped per fingerprint on the
      shared :class:`RefreshDispatcher`) and report the alias as ``pending``.

    The frontend applies the returned estimates to the node badge and re-polls
    while ``pending`` is non-empty. The optimizer call never scans data, but the
    dedicated-connection setup is why this is background, not inline. User
    routing overrides aren't touched here — only the size number is refined; the
    cached/lazy decision is recomputed client-side from the refined bytes for
    system-decided tables.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    if not isinstance(scope_in, dict):
        return _json({"ok": False, "error": "scope required"}, status=400)
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)

    catalog = _catalog()
    registry = current_app.config.get("CONCEPT_REGISTRY")
    bindings = current_app.config.get("CONCEPT_BINDING_CATALOG")
    dc = current_app.config.get("DATA_CLIENT")
    store = _size_estimate_store()
    dispatcher = current_app.config.get("LIBRARY_REFRESH_DISPATCHER")
    can_explain = dc is not None and hasattr(dc, "get_connection")

    estimates: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for item in scope.basket:
        # Raw Oracle table → compose_cached_sql; filter-node (Faz R1) →
        # compile_filter_sql (re-queries the source Oracle table); manuel SQL
        # dataset → the authored query as an inline view. All three are
        # EXPLAIN-PLAN-able. aggregate/calculated run on DuckDB → skip here.
        is_filter = item.derivation is not None and item.derivation.kind == "filter"
        is_sql = item.sql is not None
        if not (is_filter or is_sql) and (item.derivation is not None or item.table_ref is None):
            continue
        src = None
        try:
            if is_sql:
                # Whitelist again before handing to EXPLAIN (defence in depth —
                # save/build validate too). EXPLAIN never scans data.
                from presentations.sql.validator import validate_sql
                if not validate_sql(item.sql).ok:
                    continue
                sql, binds = f"SELECT * FROM (\n{item.sql}\n)", {}
                src_ref = None
            elif is_filter:
                sql, binds = compile_filter_sql(
                    scope, item, catalog,
                    concept_registry=registry, binding_catalog=bindings,
                )
                # bytes/row from the SOURCE table's metadata (same columns).
                src = scope.basket_item(item.derivation.source_alias)
                src_ref = src.table_ref if src is not None else None
            else:
                sql, binds = compose_cached_sql(
                    scope, item, catalog,
                    concept_registry=registry, binding_catalog=bindings,
                )
                src_ref = item.table_ref
        except Exception:
            log.warning("refine-sizes: compose failed for alias %s", item.alias,
                        exc_info=True)
            continue
        key = fingerprint(sql, binds)
        cached = store.get(key)
        if cached is not None:
            estimates[item.alias] = {
                "estimated_bytes": cached["estimated_bytes"],
                "rows": cached["rows"],
                "source": cached["source"],
            }
            continue
        if not (can_explain and dispatcher is not None):
            continue
        tm = catalog.table_meta(src_ref.schema_name, src_ref.name) if src_ref else None
        proj = item.projection if (item.projection and item.projection.columns) else (
            src.projection if is_filter and src is not None else item.projection)
        if tm is not None:
            bpr = _bytes_per_row(tm, proj)
        elif is_sql and item.projection and item.projection.columns:
            # SQL dataset has no table meta — width from its output column count.
            bpr = max(50, 16 * len(item.projection.columns))
        else:
            bpr = 50

        def _job(_sql=sql, _binds=binds, _bpr=bpr):
            return estimate_bytes_via_explain(dc, _sql, _binds, _bpr)

        def _on_success(res, _key=key):
            if res is not None:
                store.put(_key, rows=res["rows"],
                          estimated_bytes=res["estimated_bytes"], source="explain_plan")

        dispatcher.enqueue(cache_key=f"size::{pid}::{key}", fetch=_job, on_success=_on_success)
        pending.append(item.alias)

    return _json({"ok": True, "estimates": estimates, "pending": pending})


@presentations_bp.route("/<pid>/scope/preview-sql", methods=["POST"])
@login_required
def scope_preview_sql(pid: str):
    """Validate + run a free-form dataset SQL at AUTHORING time so the Hazırlık
    'Manuel SQL Tablo' modal can confirm the query and surface its columns
    before adding it to the basket. This is a *design-time* trigger (the only
    place besides cron where a query runs). Whitelist-gated (SELECT/WITH only,
    no DDL/DML); result is row-capped for a quick sample.
    """
    body = request.get_json(silent=True) or {}
    sql = (body.get("sql") or "").strip()
    if not sql:
        return _json({"ok": False, "errors": ["SQL boş olamaz."]}, status=400)

    from presentations.sql.validator import validate_sql
    chk = validate_sql(sql)
    if not chk.ok:
        return _json({"ok": False, "phase": "sql", "errors": chk.errors,
                      "warnings": chk.warnings}, status=400)

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"ok": False, "errors": ["DATA_CLIENT yapılandırılmamış."]}, status=500)

    from presentations.aggregation_gate import validate_and_wrap, GateError
    from presentations import duck
    import pandas as pd
    try:
        gate = validate_and_wrap(sql)
        df = dc.get_data(base_prefix=None, dataset=f"scope-preview::{pid}",
                         query=gate.sql, query_params={})
    except GateError as exc:
        return _json({"ok": False, "phase": "gate", "errors": [str(exc)]}, status=400)
    except Exception as exc:
        msg = str(exc).strip().splitlines()[0][:240]
        return _json({"ok": False, "phase": "oracle", "errors": [msg]}, status=502)

    if df is None:
        df = pd.DataFrame()
    cols = [str(c) for c in df.columns]
    rows = [[duck._jsonable(v) for v in r]
            for r in df.head(50).itertuples(index=False, name=None)]
    # `data_columns` is what the Hazırlık preview drawer's grid binds to; the
    # SqlDatasetModal reads `columns`. Return both so one endpoint serves both.
    # Gate bilgisi (truncated/cap/reason) modalda gösterilir — kullanıcı örneğin
    # 5000 satırlık kapıya takıldığını GÖRMELİ ("önizleme = tüm veri" sanmasın).
    return _json({"ok": True, "columns": cols, "data_columns": cols, "rows": rows,
                  "row_count": int(len(df)),
                  "truncated": bool(gate.truncated), "cap": gate.cap,
                  "gate_reason": gate.reason,
                  "warnings": chk.warnings})


@presentations_bp.route("/<pid>/scope/explain-sql", methods=["POST"])
@login_required
def scope_explain_sql(pid: str):
    """Manuel SQL modalında 'Önizle' ile PARALEL çağrılan ucuz maliyet tahmini.

    EXPLAIN PLAN veriyi taramaz (saniye altı): optimizer'ın satır tahminini
    döner; modal "~N satır — uzun sürebilir" uyarısını canlı gösterir. DEV stub
    (get_connection'sız DataClient) ya da herhangi bir hata → ``rows: null``
    (best effort, önizlemeyi asla bloklamaz).
    """
    body = request.get_json(silent=True) or {}
    sql = (body.get("sql") or "").strip()
    if not sql:
        return _json({"ok": False, "errors": ["SQL boş olamaz."]}, status=400)
    from presentations.sql.validator import validate_sql
    chk = validate_sql(sql)
    if not chk.ok:
        return _json({"ok": False, "phase": "sql", "errors": chk.errors}, status=400)
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"ok": True, "rows": None, "estimated_bytes": None})
    from presentations.scope.size_estimate import explain_plan_rows
    rows = explain_plan_rows(dc, f"SELECT * FROM (\n{sql}\n)", {})
    est = None if rows is None else int(rows) * 50   # kaba genişlik — bilgi amaçlı
    return _json({"ok": True, "rows": rows, "estimated_bytes": est})


@presentations_bp.route("/<pid>/scope/filter-preview", methods=["POST"])
@login_required
def scope_filter_preview(pid: str):
    """Faz R1/F3 — preview a filter-derivation node + return its generated source
    SQL. Compiles ``compile_filter_sql`` (Oracle SELECT against the source main
    table with the node's embedded filters), runs a row-capped sample, and
    returns rows + the SQL so the Hazırlık drawer can show a "Kaynak Query" tab.

    Request:  ``{"scope": <draft>, "alias": "<filter node>"}``
    Response: ``{ok, columns, data_columns, rows, row_count, sql}``.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    alias = (body.get("alias") or "").strip()
    if not isinstance(scope_in, dict) or not alias:
        return _json({"ok": False, "errors": ["scope ve alias zorunlu"]}, status=400)
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)

    item = scope.basket_item(alias)
    if item is None or item.derivation is None or item.derivation.kind != "filter":
        return _json({"ok": False, "errors": [f"'{alias}' bir filter-node değil"]}, status=400)

    try:
        # The DISPLAYED "Kaynak Query" is the real cache query (no row cap) —
        # FETCH FIRST is a preview-only concern. Pretty-print it for readability.
        # (The capped RUN is handled by _preview_sample_into_duck below.)
        display_sql, _ = compile_filter_sql(
            scope, item, _catalog(),
            concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
            binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
            max_rows=None,
        )
    except Exception as exc:
        return _json({"ok": False, "phase": "compile", "errors": [str(exc)]}, status=400)

    try:
        import sqlparse
        pretty_sql = sqlparse.format(display_sql, reindent=True, keyword_case="upper")
    except Exception:
        pretty_sql = display_sql

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        # No Oracle (DEV stub) — still return the SQL so the query tab works.
        return _json({"ok": True, "columns": [], "data_columns": [], "rows": [],
                      "row_count": 0, "sql": pretty_sql})

    from presentations import duck
    conn = duck.connect_duckdb(":memory:")
    try:
        # _preview_sample_into_duck materialises the filter node whether its source
        # is an Oracle main (capped Oracle filter query) OR a DERIVED node
        # (union/join/aggregate/…): it recursively samples the source into DuckDB
        # and runs the filter there. The old path always sent the compiled SQL to
        # Oracle, so a filter on a union/join silently returned 0 rows (the DuckDB
        # SQL references a view that doesn't exist in Oracle).
        _preview_sample_into_duck(
            conn, scope, alias, dc, pid,
            catalog=_catalog(),
            registry=current_app.config.get("CONCEPT_REGISTRY"),
            bindings=current_app.config.get("CONCEPT_BINDING_CATALOG"),
            registered=set())
        df = conn.execute(f'SELECT * FROM "{alias}" LIMIT 200').fetchdf()
    except Exception as exc:
        msg = str(exc).strip().splitlines()[0][:240]
        # SQL still useful even if the sample run failed.
        return _json({"ok": True, "columns": [], "data_columns": [], "rows": [],
                      "row_count": 0, "sql": pretty_sql, "warnings": [msg]})
    finally:
        try:
            conn.close()
        except Exception:
            pass

    cols = [str(c) for c in df.columns]
    rows = [[duck._jsonable(v) for v in r]
            for r in df.head(200).itertuples(index=False, name=None)]
    return _json({"ok": True, "columns": cols, "data_columns": cols, "rows": rows,
                  "row_count": int(len(df)), "sql": pretty_sql})


@presentations_bp.route("/<pid>/scope/resolve-sql", methods=["POST"])
@login_required
def scope_resolve_sql(pid: str):
    """Faz R4/#1 — "Çözümle": parse a free-form query into a node plan.

    Whitelist-validate → extract the source tables (FROM/JOIN via
    ``derive_source_tables``) → report which are documented (table-doc store) and
    surface their columns + concept bindings. The Hazırlık UI uses this to add the
    source tables as MAIN nodes and the query result as a derived (sql) node bound
    to them, and to warn about undocumented tables (limited concept inference).

    Request:  ``{"sql": "<query>"}``
    Response: ``{ok, sql, source_tables: [{schema, name, id, documented, columns:
                 [{name, type, concept}]}], warnings}``.
    """
    body = request.get_json(silent=True) or {}
    sql = (body.get("sql") or "").strip()
    if not sql:
        return _json({"ok": False, "errors": ["SQL boş olamaz."]}, status=400)

    from presentations.sql.validator import validate_sql
    chk = validate_sql(sql)
    if not chk.ok:
        return _json({"ok": False, "phase": "sql", "errors": chk.errors,
                      "warnings": chk.warnings}, status=400)

    from presentations.concepts.integration import derive_source_tables
    tables = derive_source_tables({"query": sql})

    store = current_app.config.get("TABLE_DOC_STORE")
    bc = current_app.config.get("CONCEPT_BINDING_CATALOG")
    source_tables: list[dict[str, Any]] = []
    warnings = list(chk.warnings or [])
    for (schema, name) in tables:
        doc = None
        try:
            doc = store.load(schema, name) if store is not None else None
        except Exception:
            doc = None
        columns: list[dict[str, Any]] = []
        if doc is not None:
            bind_concept: dict[str, str] = {}
            if bc is not None:
                try:
                    for b in bc.get_bindings(schema, name):
                        if getattr(b, "column", None) and getattr(b, "concept", None):
                            bind_concept[b.column] = b.concept
                except Exception:
                    pass
            for col, cd in (getattr(doc, "columns", {}) or {}).items():
                columns.append({
                    "name": col,
                    "type": getattr(cd, "type", None),
                    "concept": bind_concept.get(col) or getattr(cd, "suggested_semantic_tag", None),
                })
        else:
            warnings.append(
                f"{schema}.{name} dökümante değil — concept çıkarımı sınırlı. "
                "Önce Tablolar'dan dökümante et."
            )
        source_tables.append({
            "schema": schema, "name": name, "id": f"{schema}.{name}",
            "documented": doc is not None, "columns": columns,
        })

    return _json({"ok": True, "sql": sql, "source_tables": source_tables,
                  "warnings": warnings})


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


def _preview_sample_into_duck(conn, scope, alias, dc, pid, *,
                              catalog, registry, bindings, registered, depth=0):
    """Recursively materialise a row-capped SAMPLE of ``alias`` into a DuckDB
    view for the design-time derivation preview. Handles main tables, sql
    datasets AND nested derivations (filter / aggregate / calculated / join /
    union) — so a join or union of filter nodes previews correctly (the old
    shallow loop rejected derived sources with "iç içe türetme … desteklenmiyor"
    / "Kaynak … basket'te yok"). Oracle pulls are capped; DuckDB steps run over
    the already-registered (capped) source views. Mirrors materialize.py's
    recursion but sampled, not full.
    """
    import pandas as pd
    from presentations import duck
    from presentations.scope.fetch import (
        compile_aggregate_sql, compile_calculated_sql, compile_join_sql, compile_union_sql,
    )
    if alias in registered:
        return
    if depth > 10:
        raise ValueError(f"önizleme: '{alias}' türetme zinciri çok derin")
    src = next((b for b in scope.basket if b.alias == alias), None)
    if src is None:
        raise ValueError(f"Kaynak '{alias}' basket'te yok")
    SAMPLE = 5000

    if src.derivation is None and src.table_ref is not None:
        full = f"{src.table_ref.schema_name}.{src.table_ref.name}"
        df = dc.get_data(base_prefix=None, dataset=f"preview-deriv::{pid}::{alias}",
                         query=f"SELECT * FROM {full} FETCH FIRST {SAMPLE} ROWS ONLY", query_params={})
    elif src.derivation is None and getattr(src, "sql", None):
        df = dc.get_data(base_prefix=None, dataset=f"preview-deriv::{pid}::{alias}",
                         query=f"SELECT * FROM ({src.sql}) _src FETCH FIRST {SAMPLE} ROWS ONLY", query_params={})
    elif src.derivation is not None:
        d = src.derivation
        if d.kind == "filter":
            base = scope.basket_item(d.source_alias)
            if base is not None and base.table_ref is not None:
                # filter on an Oracle main → run the (capped) Oracle filter query.
                compiled, binds = compile_filter_sql(
                    scope, src, catalog, concept_registry=registry,
                    binding_catalog=bindings, max_rows=SAMPLE)
                df = dc.get_data(base_prefix=None, dataset=f"preview-deriv::{pid}::{alias}",
                                 query=compiled, query_params=binds)
            else:
                # filter on a derived source → register the source, filter in DuckDB.
                _preview_sample_into_duck(conn, scope, d.source_alias, dc, pid,
                    catalog=catalog, registry=registry, bindings=bindings,
                    registered=registered, depth=depth + 1)
                compiled, binds = compile_filter_sql(
                    scope, src, catalog, concept_registry=registry, binding_catalog=bindings)
                df = conn.execute(compiled, binds).fetchdf() if binds else conn.execute(compiled).fetchdf()
        else:
            srcs = (list(d.source_aliases) if d.kind in ("calculated", "join", "union")
                    else ([d.source_alias] if d.source_alias else []))
            for s in srcs:
                _preview_sample_into_duck(conn, scope, s, dc, pid,
                    catalog=catalog, registry=registry, bindings=bindings,
                    registered=registered, depth=depth + 1)
            if d.kind == "aggregate":
                compiled = compile_aggregate_sql(src)
            elif d.kind == "join":
                lc = list(conn.execute(f'SELECT * FROM "{d.source_aliases[0]}" LIMIT 0').fetchdf().columns)
                rc = list(conn.execute(f'SELECT * FROM "{d.source_aliases[1]}" LIMIT 0').fetchdf().columns)
                compiled = compile_join_sql(src, lc, rc)
            elif d.kind == "union":
                compiled = compile_union_sql(src)
            else:
                compiled = compile_calculated_sql(src)
            df = conn.execute(compiled).fetchdf()
    else:
        raise ValueError(f"Kaynak '{alias}' önizlenemiyor")

    duck.register_dataframe(conn, alias, df if df is not None else pd.DataFrame())
    registered.add(alias)


@presentations_bp.route("/<pid>/scope/preview-derivation", methods=["POST"])
@login_required
def scope_preview_derivation(pid: str):
    """Sample a derived (aggregate / calculated) basket item at design time.

    The Hazırlık drawer can aggregate a single source in-browser, but it can't
    preview a ``calculated`` derivation (window functions, multi-source joins).
    We fetch a bounded sample of each source from Oracle, register them into an
    in-memory DuckDB under their aliases, and run the *same* SQL the fetch pass
    compiles (``compile_calculated_sql`` / ``compile_aggregate_sql``). The
    result is illustrative — Sunum re-runs the derivation over the full pull.

    Request:  ``{"scope": <draft>, "alias": "..."}``
    Response: ``{"ok": true, "data_columns": [...], "rows": [...],
                 "row_count": int, "derived": true}``.
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    alias = (body.get("alias") or "").strip()
    if not isinstance(scope_in, dict) or not alias:
        return _json({"ok": False, "errors": ["scope ve alias zorunlu"]}, status=400)
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)

    item = next((b for b in scope.basket if b.alias == alias), None)
    if item is None or item.derivation is None:
        return _json({"ok": False, "errors": [f"'{alias}' türetilmiş bir tablo değil"]}, status=400)

    d = item.derivation
    src_aliases = [d.source_alias] if d.kind == "aggregate" else list(d.source_aliases)

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"ok": False, "errors": ["DATA_CLIENT yapılandırılmamış."]}, status=500)

    from presentations import duck
    from presentations.scope.fetch import (
        compile_aggregate_sql, compile_calculated_sql, compile_join_sql, compile_union_sql,
    )
    import pandas as pd

    catalog = _catalog()
    registry = current_app.config.get("CONCEPT_REGISTRY")
    bindings = current_app.config.get("CONCEPT_BINDING_CATALOG")
    conn = duck.connect_duckdb(":memory:")
    registered: set[str] = set()
    cols_by_src: dict[str, list[str]] = {}
    try:
        # Sample each source (recursively for nested derivations: a join/union of
        # filter nodes materialises its filter sources first) into DuckDB views.
        for sa in src_aliases:
            try:
                _preview_sample_into_duck(conn, scope, sa, dc, pid,
                    catalog=catalog, registry=registry, bindings=bindings, registered=registered)
            except ValueError as exc:
                return _json({"ok": False, "errors": [str(exc)]}, status=400)
            except Exception as exc:
                msg = str(exc).strip().splitlines()[0][:240]
                return _json({"ok": False, "phase": "oracle", "errors": [f"{sa}: {msg}"]}, status=502)
            cols_by_src[sa] = list(conn.execute(f'SELECT * FROM "{sa}" LIMIT 0').fetchdf().columns)

        if d.kind == "aggregate":
            compiled = compile_aggregate_sql(item)
        elif d.kind == "join":
            compiled = compile_join_sql(item, cols_by_src.get(d.source_aliases[0], []),
                                        cols_by_src.get(d.source_aliases[1], []))
        elif d.kind == "union":
            compiled = compile_union_sql(item)
        else:  # calculated
            compiled = compile_calculated_sql(item)
        try:
            out_df = conn.execute(f"SELECT * FROM ({compiled}) AS _d LIMIT 200").fetchdf()
        except Exception as exc:
            msg = str(exc).strip().splitlines()[0][:240]
            return _json({"ok": False, "phase": "duckdb", "errors": [msg], "sql": compiled}, status=400)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    cols = [str(c) for c in out_df.columns]
    rows = [[duck._jsonable(v) for v in r] for r in out_df.itertuples(index=False, name=None)]
    # `sql` → the Hazırlık drawer's "Kaynak Query" tab (join/union/calculated too,
    # not just filter). Pretty-print for readability.
    try:
        import sqlparse
        pretty = sqlparse.format(compiled, reindent=True, keyword_case="upper")
    except Exception:
        pretty = compiled
    return _json({"ok": True, "columns": [{"name": c} for c in cols],
                  "data_columns": cols, "rows": rows,
                  "row_count": int(len(out_df)), "derived": True, "sql": pretty})


@presentations_bp.route("/<pid>/scope/preview-python", methods=["POST"])
@login_required
def scope_preview_python(pid: str):
    """Faz P — bir python node'unu tasarım anında çalıştır + örnek satır döndür.

    Source node'un (tek giriş) sınırlı bir örneği DuckDB'ye yüklenir, DataFrame
    olarak çekilip ``input_node_df`` adıyla AST-whitelist + subprocess sandbox'a
    verilir. Script ``output_node_df`` üretirse örnek satırlar döner. Sonuç
    illüstratiftir — build/cron tam veriyle yeniden koşar.

    Request:  ``{"scope": <draft>, "source_alias": "...", "python_code": "..."}``
    Response (ok):   ``{"ok": true, "data_columns": [...], "columns": [...],
                        "rows": [...], "row_count": int, "derived": true}``
    Response (hata): ``{"ok": false, "phase": "validate|oracle|python",
                        "errors": [...], "detail": "..."}``
    """
    body = request.get_json(silent=True) or {}
    scope_in = body.get("scope")
    source_alias = (body.get("source_alias") or "").strip()
    python_code = body.get("python_code") or ""
    if not isinstance(scope_in, dict) or not source_alias:
        return _json({"ok": False, "errors": ["scope ve source_alias zorunlu"]}, status=400)
    try:
        scope = load_scope_from_dict({"scope": scope_in})
    except (ValidationError, ValueError) as exc:
        return _json({"ok": False, "errors": _flatten(exc)}, status=400)

    if not any(b.alias == source_alias for b in scope.basket):
        return _json({"ok": False, "errors": [f"'{source_alias}' basket'te yok"]}, status=400)

    from presentations.python_runtime import validate_python, run_python_transform

    # Hızlı geri-bildirim: önce statik denetim (Oracle örneklemeden önce).
    check = validate_python(python_code)
    if not check.ok:
        return _json({"ok": False, "phase": "validate", "errors": check.errors}, status=400)

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"ok": False, "errors": ["DATA_CLIENT yapılandırılmamış."]}, status=500)

    from presentations import duck

    catalog = _catalog()
    registry = current_app.config.get("CONCEPT_REGISTRY")
    bindings = current_app.config.get("CONCEPT_BINDING_CATALOG")
    conn = duck.connect_duckdb(":memory:")
    registered: set[str] = set()
    try:
        try:
            _preview_sample_into_duck(conn, scope, source_alias, dc, pid,
                catalog=catalog, registry=registry, bindings=bindings, registered=registered)
        except ValueError as exc:
            return _json({"ok": False, "phase": "oracle", "errors": [str(exc)]}, status=400)
        except Exception as exc:
            msg = str(exc).strip().splitlines()[0][:240]
            return _json({"ok": False, "phase": "oracle",
                          "errors": [f"{source_alias}: {msg}"]}, status=502)
        input_df = conn.execute(f'SELECT * FROM "{source_alias}"').fetchdf()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    result = run_python_transform(python_code, input_df)
    if not result.ok:
        return _json({"ok": False, "phase": "python",
                      "errors": [result.error or "Bilinmeyen hata"],
                      "detail": result.detail, "stdout": result.stdout}, status=400)

    out_df = result.df.head(200)
    cols = [str(c) for c in out_df.columns]
    rows = [[duck._jsonable(v) for v in r] for r in out_df.itertuples(index=False, name=None)]
    return _json({"ok": True, "columns": [{"name": c} for c in cols],
                  "data_columns": cols, "rows": rows,
                  "row_count": int(result.row_count or 0), "derived": True,
                  "output_columns": result.columns or cols, "stdout": result.stdout})


@presentations_bp.route("/<pid>/scope/distinct", methods=["GET"])
@login_required
def scope_distinct(pid: str):
    """Distinct values for a string column flagged ``get_distinct`` in the table
    doc — feeds the Filtreleme tab's checkbox list. Prefers the nightly
    ``distinct_values_sample`` (no Oracle hit); otherwise runs a capped live
    ``SELECT DISTINCT``. Guarded: only ``get_distinct`` columns are served, and
    SQL uses the doc's validated ALL_CAPS identifiers (never raw request args).
    """
    schema = (request.args.get("schema") or "").strip()
    table = (request.args.get("table") or "").strip()
    column = (request.args.get("column") or "").strip()
    if not table or not column:
        return _json({"ok": False, "error": "table ve column zorunlu"}, status=400)
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    except ValueError:
        limit = 200

    store = current_app.config.get("TABLE_DOC_STORE")
    doc = None
    if store is not None:
        try:
            doc = store.load(schema, table)
        except Exception:
            doc = None
    col_doc = (getattr(doc, "columns", {}) or {}).get(column) if doc is not None else None
    if col_doc is None or not getattr(col_doc, "get_distinct", False):
        return _json({"ok": False, "error": "Bu kolon için get_distinct etkin değil."}, status=400)

    from presentations import duck

    sample = getattr(col_doc, "distinct_values_sample", None)
    if sample:
        return _json({"ok": True, "source": "sample",
                      "values": [duck._jsonable(v) for v in sample[:limit]]})

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return _json({"ok": False, "error": "DATA_CLIENT yapılandırılmamış."}, status=500)
    # Use the doc's validated identifiers (col_doc exists ⇒ `column` is a real
    # ALL_CAPS column key; schema_name/table are ALL_CAPS-validated by the doc).
    safe_col = column
    full = f"{getattr(doc, 'schema_name', schema)}.{getattr(doc, 'table', table)}"
    sql = (f"SELECT DISTINCT {safe_col} AS V FROM {full} "
           f"WHERE {safe_col} IS NOT NULL FETCH FIRST {limit} ROWS ONLY")
    try:
        df = dc.get_data(base_prefix=None, dataset=f"distinct::{full}::{safe_col}",
                         query=sql, query_params={})
    except Exception as exc:
        msg = str(exc).strip().splitlines()[0][:240]
        return _json({"ok": False, "error": msg}, status=502)
    import pandas as pd
    if df is None:
        df = pd.DataFrame()
    vals = [duck._jsonable(r[0]) for r in df.itertuples(index=False, name=None)]
    return _json({"ok": True, "source": "live", "values": vals})


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


def hydrate_block_datasets(dc, conn, scope, sql: str) -> None:
    """Make sure every scope dataset the block ``sql`` references is registered
    as a DuckDB view in ``conn`` before the block runs.

    Two tiers:
    1. Fast path — ``load_into_duck`` registers materialised datasets from their
       S3 parquet (scheduled/cron-warmed datasets), no Oracle round-trip.
    2. Fallback — if a referenced basket alias is still missing (non-scheduled
       dataset, freshly built scope whose cron hasn't run, or a recreated/
       evicted connection where the build-time in-memory views are gone), fetch
       the scope on-demand into ``conn`` via ``fetch_cached_tables`` (the same
       Oracle → DuckDB path build uses). A full fetch is used so derivations get
       their source aliases too.
    """
    from presentations import duck
    from presentations.scope.materialize import load_into_duck

    try:
        load_into_duck(dc, conn, scope)
    except Exception:
        log.warning("hydrate_block_datasets: load_into_duck failed", exc_info=True)

    referenced = duck.find_view_refs(sql, [b.alias for b in scope.basket])
    if not referenced:
        return
    present = set(duck.list_views(conn))
    if all(a in present for a in referenced):
        return
    try:
        fetch_cached_tables(
            dc, conn, scope,
            catalog=_catalog(),
            concept_registry=current_app.config.get("CONCEPT_REGISTRY"),
            binding_catalog=current_app.config.get("CONCEPT_BINDING_CATALOG"),
        )
    except Exception:
        log.warning("hydrate_block_datasets: on-demand fetch failed", exc_info=True)


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
    selected_alias = (body.get("selected_alias") or "").strip() or None
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

    # Faz P — node-scope: seçili node'un kolonlarını LLM bağlamına ver. Türetilmiş
    # node'lar cols_by_alias'ta olmayabilir (yalnız raw main'ler dolduruldu); o
    # zaman projection.columns'a düş.
    selected_columns: list[str] | None = None
    if selected_alias:
        meta = cols_by_alias.get(selected_alias)
        if meta:
            selected_columns = [c.get("name") for c in meta if c.get("name")]
        else:
            sel = next((b for b in (scope_in.get("basket") or [])
                        if b.get("alias") == selected_alias), None)
            if sel is not None:
                selected_columns = list((sel.get("projection") or {}).get("columns") or [])

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
            selected_alias=selected_alias,
            selected_columns=selected_columns,
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
    if kind == "create_calculation":
        return _apply_create_calculation(s, sugg)
    if kind == "create_python_node":
        return _apply_create_python_node(s, sugg)
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


def _apply_create_calculation(s: dict, sugg: dict) -> dict:
    """Apply a `create_calculation` suggestion → add a kind:"calculated"
    derivation to the basket (Polish-5).

    Suggestion shape:
      {
        "kind": "create_calculation",
        "new_alias": "rate_gap",
        "source_aliases": ["deposits_daily", "competitor_rates"],
        "join_keys": [
          {"left_alias":"deposits_daily","left_column":"BRANCH_CODE",
           "right_alias":"competitor_rates","right_column":"BRANCH_CODE"}
        ],
        "columns": [
          {"name":"RATE_GAP","expr":"deposits_daily.INTEREST_RATE - competitor_rates.RATE"}
        ]
      }
    """
    new_alias = sugg.get("new_alias")
    src_aliases = list(sugg.get("source_aliases") or [])
    join_keys = list(sugg.get("join_keys") or [])
    columns = list(sugg.get("columns") or [])

    if not new_alias:
        raise _ApplyError("create_calculation: new_alias zorunlu")
    if not src_aliases:
        raise _ApplyError("create_calculation: en az bir source_aliases gerekli")
    if not columns:
        raise _ApplyError("create_calculation: en az bir output column gerekli")
    aliases_in_basket = {b.get("alias") for b in s["basket"]}
    if new_alias in aliases_in_basket:
        raise _ApplyError(f"create_calculation: '{new_alias}' alias'ı zaten mevcut")
    for src in src_aliases:
        if src not in aliases_in_basket:
            raise _ApplyError(
                f"create_calculation: source_alias '{src}' basket'te yok"
            )
    if len(src_aliases) > 1 and not join_keys:
        raise _ApplyError(
            "create_calculation: çoklu source_aliases için join_keys gerekli"
        )
    # Normalise column shape (verbatim through to the schema layer for validation).
    norm_cols = [{"name": c["name"], "expr": c["expr"],
                  **({"type_hint": c["type_hint"]} if c.get("type_hint") else {})}
                 for c in columns]
    norm_jks = [{
        "left_alias": j["left_alias"], "left_column": j["left_column"],
        "right_alias": j["right_alias"], "right_column": j["right_column"],
    } for j in join_keys]

    s["basket"].append({
        "alias": new_alias,
        "derivation": {
            "kind": "calculated",
            "source_aliases": src_aliases,
            "join_keys": norm_jks,
            "columns": norm_cols,
        },
        "projection": {
            "columns": [c["name"] for c in norm_cols],
            "include_all": False,
        },
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
    })
    return s


def _apply_create_python_node(s: dict, sugg: dict) -> dict:
    """Faz P — apply a `create_python_node` suggestion → add a kind:"python"
    derivation. Tek-girişli: source_alias = input_node_df. Script
    ``validate_python`` whitelist'inden geçmeli (LLM kötü/kaçışlı kod ürettiyse
    reddedilir — kullanıcı sonra düzenleyip Çalıştır'la deneyebilir).

    Suggestion shape:
      {"kind": "create_python_node", "source_alias": "deposits",
       "new_alias": "deposits_py", "python_code": "output_node_df = ..."}
    """
    from presentations.python_runtime import validate_python

    source_alias = sugg.get("source_alias")
    if not source_alias:
        raise _ApplyError("create_python_node: source_alias zorunlu")
    aliases_in_basket = {b.get("alias") for b in s["basket"]}
    if source_alias not in aliases_in_basket:
        raise _ApplyError(f"create_python_node: source_alias '{source_alias}' basket'te yok")

    new_alias = sugg.get("new_alias") or f"{source_alias}_py"
    # Çakışıyorsa benzersizleştir (_2, _3 …) — Apply'ı bloklamak yerine.
    base, n = new_alias, 2
    while new_alias in aliases_in_basket:
        new_alias = f"{base}_{n}"
        n += 1

    code = sugg.get("python_code") or ""
    check = validate_python(code)
    if not check.ok:
        raise _ApplyError("create_python_node: script reddedildi: " + "; ".join(check.errors))

    s["basket"].append({
        "alias": new_alias,
        "derivation": {
            "kind": "python",
            "source_alias": source_alias,
            "python_code": code,
            "output_columns": [],
        },
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 0},
    })
    return s

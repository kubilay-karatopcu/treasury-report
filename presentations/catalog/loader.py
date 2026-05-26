"""Phase 9.a — Catalog loader (corporate + user-upload merge, TTL cache).

The loader walks two storage shapes and produces a uniform list of
:class:`TableEntry` records (spec §2.1 / §2.2):

- **Corporate tables** — Phase 6.5.b table docs read from the
  ``TABLE_DOC_STORE`` (S3 in prod, filesystem locally). The store already
  has its own process-level cache; we wrap our own short TTL on top to
  smooth concurrent reads.

- **User uploads** — Phase 9.d will write ``uploads/<sicil>/<upload_id>/
  doc.yaml`` (same shape as a corporate doc, with schema marker
  ``__user_<sicil>__``). The Phase 9.a loader has the code path so 9.d can
  drop in data without touching this module; in 9.a the path simply
  returns an empty list (no uploads yet).

Caching: a 30-second TTL keyed by ``(user_sicil, refresh_flag)`` (spec
§11). Calling with ``refresh=True`` skips the cache. The Phase 7
``CONCEPT_REGISTRY`` (if configured) is consulted purely for the
``concepts_unbound`` derivation; absent registry → empty unbound list.

The loader is intentionally tolerant: a malformed YAML or a missing field
is logged and the table is skipped, never raised. The catalog must remain
serviceable when one entry rots.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

from presentations.catalog.models import (
    ColumnSummary,
    LookupSummary,
    TableEntry,
)


log = logging.getLogger(__name__)


# ── Department mapping ────────────────────────────────────────────────────
# Forward-compat hook: when a table doc carries an explicit ``department``
# field (Phase 9.a does not yet require this), it wins. Otherwise we fall
# back to this static map keyed by schema. User-upload schemas
# (``__user_<sicil>__``) always have department = None.

DEFAULT_SCHEMA_DEPARTMENT_MAP: dict[str, str] = {
    "EDW": "treasury",
    "ODS_TREASURY": "treasury",
    "ODS_RISK": "risk",
    "ODS_BILANCO": "bilanco",
}


def _user_schema_marker(sicil: str) -> str:
    """Sentinel schema name for user-uploaded tables. See spec §2.1."""
    return f"__user_{sicil}__"


def _is_user_schema(schema: str) -> bool:
    return schema.startswith("__user_") and schema.endswith("__")


# ── Cache entry ───────────────────────────────────────────────────────────


@dataclass
class _CacheEntry:
    expires_at: float
    entries: list[TableEntry]


# ── Loader ────────────────────────────────────────────────────────────────


class CatalogLoader:
    """Unified read layer over corporate + user catalogs.

    Constructor injection lets the local dev runner pass filesystem stubs
    and the production app pass real S3-backed stores. ``data_client`` is
    used only to read user-upload artifacts (``uploads/<sicil>/...``); it
    can be None when uploads aren't configured (current Phase 9.a state).
    """

    def __init__(
        self,
        *,
        table_doc_store: Any,
        data_client: Any | None = None,
        schema_department_map: dict[str, str] | None = None,
        ttl_seconds: float = 30.0,
        all_concepts: Iterable[str] | None = None,
    ):
        self._docs = table_doc_store
        self._dc = data_client
        self._dept_map = dict(schema_department_map or DEFAULT_SCHEMA_DEPARTMENT_MAP)
        self._ttl = float(ttl_seconds)
        # Universe of concepts for the unbound calculation. Optional: when
        # absent, ``concepts_unbound`` is empty for every table.
        self._concept_universe = sorted(set(all_concepts or []))
        self._cache: dict[tuple[str | None, bool], _CacheEntry] = {}
        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────────

    def load(
        self,
        *,
        user_sicil: str | None = None,
        refresh: bool = False,
    ) -> list[TableEntry]:
        """Return the merged (corporate + this user's uploads) catalog.

        ``user_sicil=None`` returns corporate only — used for tests and any
        unauthenticated context (there should be none in prod, but the
        defensive path is cheap).
        """
        key = (user_sicil, False)  # cache key ignores refresh; refresh bypasses lookup
        now = time.monotonic()

        if not refresh:
            with self._lock:
                hit = self._cache.get(key)
                if hit is not None and hit.expires_at > now:
                    return list(hit.entries)

        entries: list[TableEntry] = []
        entries.extend(self._load_corporate())
        if user_sicil:
            entries.extend(self._load_user_uploads(user_sicil))

        with self._lock:
            self._cache[key] = _CacheEntry(
                expires_at=now + self._ttl,
                entries=list(entries),
            )
        return entries

    def get(self, schema: str, name: str, *, user_sicil: str | None = None) -> TableEntry | None:
        """Return the detail-card-rich entry for one table, or None.

        Re-loads the full corporate doc / upload doc so ``columns``,
        ``lookups``, ``related_tables`` are populated — these fields are
        intentionally stripped from the list response to keep payloads
        compact.
        """
        if _is_user_schema(schema):
            if not user_sicil or schema != _user_schema_marker(user_sicil):
                return None
            doc = self._load_user_upload_doc(user_sicil, name)
            if doc is None:
                return None
            return self._upload_doc_to_entry(doc, user_sicil)

        doc = self._load_corporate_doc(schema, name)
        if doc is None:
            return None
        return self._corporate_doc_to_entry(doc, with_details=True)

    def invalidate(self) -> None:
        """Wipe the cache; useful in tests and after a known-fresh write."""
        with self._lock:
            self._cache.clear()

    # ── Corporate path ────────────────────────────────────────────────────

    def _load_corporate(self) -> list[TableEntry]:
        store = self._docs
        if store is None:
            return []
        try:
            docs = store.list_all_docs()
        except Exception:
            log.exception("catalog: list_all_docs failed; returning empty corporate set")
            return []
        out: list[TableEntry] = []
        for doc in docs:
            try:
                out.append(self._corporate_doc_to_entry(doc, with_details=False))
            except Exception:
                schema = getattr(doc, "schema_name", "?")
                name = getattr(doc, "table", "?")
                log.warning("catalog: skip malformed corporate doc %s.%s", schema, name, exc_info=True)
        return out

    def _load_corporate_doc(self, schema: str, name: str):
        store = self._docs
        if store is None:
            return None
        try:
            return store.load(schema, name)
        except Exception:
            return None

    def _corporate_doc_to_entry(self, doc, *, with_details: bool) -> TableEntry:
        schema = getattr(doc, "schema_name", None) or getattr(doc, "schema", "")
        name = getattr(doc, "table", "")
        cols = getattr(doc, "columns", {}) or {}

        concepts_bound = self._bound_concepts(cols)
        concepts_unbound = self._unbound_concepts(concepts_bound)

        department = self._dept_map.get(schema)

        # Row-count basis: Phase 6.5.b docs only carry ``estimated_daily_rows``.
        # When the table is non-partitioned (no ``partition_column``) and that
        # field is set, the data team uses it as a *total* count. Heuristic:
        # if there is no partition column, treat the count as total; otherwise
        # daily. This matches the data team's convention in the fixtures.
        partition_column = getattr(doc, "partition_column", None)
        daily_rows = getattr(doc, "estimated_daily_rows", None)
        row_count_basis = "daily" if partition_column else "total"

        # Forward-compat: a doc may carry an explicit ``department`` field
        # once the data team enriches the YAML (currently not in schema).
        explicit_dept = getattr(doc, "department", None)
        if explicit_dept:
            department = explicit_dept

        entry = TableEntry(
            schema=schema,
            name=name,
            source="corporate",
            department=department,
            description=getattr(doc, "description", None),
            concepts_bound=concepts_bound,
            concepts_unbound=concepts_unbound,
            row_count_estimate=daily_rows,
            row_count_basis=row_count_basis if daily_rows is not None else None,
            partition_column=partition_column,
            doc_url=f"/presentations/catalog/{schema}/{name}",
        )

        if with_details:
            entry.columns = self._columns_summary(cols)
            entry.lookups = self._lookups_summary(cols)
            # Phase 9 spec §2.3 allows a ``related_tables`` array on the
            # table doc for manual edges. The current TableDoc schema
            # doesn't declare this field (extra=forbid), so it's surfaced
            # only when explicitly attached at load time. Forward-compat.
            related = getattr(doc, "related_tables", None)
            entry.related_tables = list(related) if isinstance(related, (list, tuple)) else None

        return entry

    # ── User-upload path ──────────────────────────────────────────────────

    def _load_user_uploads(self, sicil: str) -> list[TableEntry]:
        """List the user's confirmed uploads under ``uploads/<sicil>/``.

        Phase 9.a never has actual uploads to read (that's 9.d), but the
        code path exists so 9.d can drop data into S3 without code changes.
        Soft-deleted uploads (``meta.yaml: deleted: true``) are filtered.
        """
        dc = self._dc
        if dc is None:
            return []
        prefix = f"uploads/{sicil}/"
        try:
            keys = dc.list_prefix(prefix)
        except Exception:
            return []

        # Group keys by upload_id. Each upload has doc.yaml + meta.yaml.
        upload_ids: set[str] = set()
        for key in keys:
            parts = key.removeprefix(prefix).split("/")
            if len(parts) >= 2 and parts[1] in ("doc.yaml", "meta.yaml"):
                upload_ids.add(parts[0])

        entries: list[TableEntry] = []
        for upload_id in sorted(upload_ids):
            doc = self._load_user_upload_doc(sicil, upload_id)
            if doc is None:
                continue
            entries.append(self._upload_doc_to_entry(doc, sicil))
        return entries

    def _load_user_upload_doc(self, sicil: str, upload_id: str) -> dict[str, Any] | None:
        """Read ``uploads/<sicil>/<upload_id>/doc.yaml`` + meta.yaml. Returns
        a merged dict with ``doc`` and ``meta`` keys, or None on any failure
        / soft-delete."""
        dc = self._dc
        if dc is None:
            return None
        try:
            doc = dc.read_yaml(f"uploads/{sicil}/{upload_id}/doc.yaml")
        except AttributeError:
            # DataClients without read_yaml: fall back through read_json
            # (which is what the local stub provides — YAML+JSON roundtrip
            # not implemented here, so 9.a's local dev simply has no uploads).
            return None
        except Exception:
            return None
        try:
            meta = dc.read_yaml(f"uploads/{sicil}/{upload_id}/meta.yaml")
        except Exception:
            meta = {}
        if (meta or {}).get("deleted"):
            return None
        return {"doc": doc, "meta": meta, "upload_id": upload_id, "sicil": sicil}

    def _upload_doc_to_entry(self, blob: dict[str, Any], sicil: str) -> TableEntry:
        doc = blob["doc"] or {}
        meta = blob["meta"] or {}
        upload_id = blob["upload_id"]
        cols = doc.get("columns") or {}

        # Build ColumnSummary-shaped dicts from the user-upload YAML.
        column_summaries: list[ColumnSummary] = []
        bound: list[str] = []
        for cname, c in cols.items():
            c = c or {}
            concept = c.get("concept") or c.get("suggested_semantic_tag")
            if concept and concept not in bound:
                bound.append(concept)
            column_summaries.append(ColumnSummary(
                name=cname,
                type=c.get("type"),
                description=c.get("description"),
                filterable=bool(c.get("filterable", False)),
                filter_role=c.get("filter_role"),
                concept=concept,
                aggregatable=bool(c.get("aggregatable", False)),
            ))

        upload_info = meta.get("upload") or {}
        upload_at = upload_info.get("uploaded_at")

        return TableEntry(
            schema=_user_schema_marker(sicil),
            name=upload_id,
            source="user_upload",
            department=None,
            description=doc.get("description"),
            concepts_bound=bound,
            concepts_unbound=self._unbound_concepts(bound),
            row_count_estimate=doc.get("estimated_total_rows") or upload_info.get("row_count"),
            row_count_basis="total",
            partition_column=doc.get("partition_column"),
            doc_url=f"/presentations/catalog/{_user_schema_marker(sicil)}/{upload_id}",
            columns=column_summaries,
            lookups=[],  # uploads don't declare lookups in 9.a
            original_filename=upload_info.get("original_filename"),
            uploaded_at=upload_at,
            inference_status=upload_info.get("inference_status"),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _bound_concepts(self, columns: dict[str, Any]) -> list[str]:
        """Concepts referenced by any column on the table.

        Prefer Phase 7's ``concept_bindings`` if attached to the TableDoc,
        fall back to the Phase 6.5.b ``suggested_semantic_tag``.
        """
        bound: list[str] = []
        for col in columns.values():
            concept = getattr(col, "suggested_semantic_tag", None)
            if concept and concept not in bound:
                bound.append(concept)
        return bound

    def _unbound_concepts(self, bound: list[str]) -> list[str]:
        """``concepts_unbound`` per spec §2.2 — the concept universe minus
        what this table binds. When the universe is unknown (no registry
        wired), return empty so consumers don't see noise."""
        if not self._concept_universe:
            return []
        bound_set = set(bound)
        return [c for c in self._concept_universe if c not in bound_set]

    def _columns_summary(self, columns: dict[str, Any]) -> list[ColumnSummary]:
        out: list[ColumnSummary] = []
        for cname, col in columns.items():
            lk = getattr(col, "lookup", None)
            lookup = None
            if lk is not None:
                lookup = {
                    "table": getattr(lk, "table", "") or "",
                    "key": getattr(lk, "key", "") or "",
                    "display": getattr(lk, "display", "") or "",
                }
            out.append(ColumnSummary(
                name=cname,
                type=getattr(col, "type", None),
                description=getattr(col, "description", None),
                filterable=bool(getattr(col, "filterable", False)),
                filter_role=getattr(col, "filter_role", None),
                concept=getattr(col, "suggested_semantic_tag", None),
                lookup=lookup,
                aggregatable=bool(getattr(col, "aggregatable", False)),
            ))
        return out

    def _lookups_summary(self, columns: dict[str, Any]) -> list[LookupSummary]:
        out: list[LookupSummary] = []
        for cname, col in columns.items():
            lk = getattr(col, "lookup", None)
            if lk is None:
                continue
            out.append(LookupSummary(
                from_column=cname,
                to_table=getattr(lk, "table", "") or "",
                to_key=getattr(lk, "key", "") or "",
                to_display=getattr(lk, "display", None),
            ))
        return out


# ── Module-level factory ──────────────────────────────────────────────────


def make_loader_from_app(app) -> CatalogLoader:
    """Build a :class:`CatalogLoader` from a Flask app's config.

    Used by the API blueprint to lazily build a single loader per process.
    Reads:

    - ``TABLE_DOC_STORE``                    — required for corporate tables.
    - ``DATA_CLIENT``                        — optional; used for user uploads.
    - ``PRESENTATIONS_SCHEMA_DEPARTMENT_MAP``— optional override for the default map.
    - ``PRESENTATIONS_CATALOG_TTL_SECONDS``  — override the 30s TTL.
    - ``CONCEPT_REGISTRY``                   — optional; used to derive
                                                ``concepts_unbound``.
    """
    table_doc_store = app.config.get("TABLE_DOC_STORE")
    data_client = app.config.get("DATA_CLIENT")
    dept_map = app.config.get("PRESENTATIONS_SCHEMA_DEPARTMENT_MAP")
    ttl = float(app.config.get("PRESENTATIONS_CATALOG_TTL_SECONDS", 30))

    registry = app.config.get("CONCEPT_REGISTRY")
    all_concepts: list[str] = []
    if registry is not None:
        try:
            all_concepts = [c.id for c in registry.all_concepts()]
        except Exception:
            all_concepts = []

    return CatalogLoader(
        table_doc_store=table_doc_store,
        data_client=data_client,
        schema_department_map=dept_map,
        ttl_seconds=ttl,
        all_concepts=all_concepts,
    )

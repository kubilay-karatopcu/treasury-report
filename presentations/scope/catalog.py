"""Catalog metadata views consumed by scope validators and routing.

The validators (concept existence, projection-column existence, concept
coverage) and the routing estimator (bytes/row, partition column, daily rows)
both need read-only catalog metadata. Phase 8.a deliberately keeps this an
abstraction so it can be driven from:

- a self-contained fixture dict (``examples/phase_8/sample_table_catalog_excerpt.yaml``)
  via :class:`DictCatalog` — this is what the unit tests use, and what makes
  8.a fast and testable **without Oracle access**; and
- the production stores via :class:`AppCatalog` — the Phase 6.5.b
  ``TABLE_DOC_STORE`` for table/column metadata and the Phase 7
  ``CONCEPT_REGISTRY`` for concept existence + canonical values.

A table that is absent from the catalog yields ``table_meta() -> None``;
callers must treat that as "cannot verify" (skip the check / warn), never as
an error. This mirrors Phase 7's concept-blind tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


# ── Metadata records ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColumnMeta:
    type: str | None = None
    avg_bytes: int | None = None
    concept: str | None = None


@dataclass(frozen=True)
class TableMeta:
    schema_name: str
    name: str
    partition_column: str | None = None
    estimated_daily_rows: int | None = None
    estimated_total_rows: int | None = None
    columns: dict[str, ColumnMeta] = field(default_factory=dict)

    def has_column(self, col: str) -> bool:
        return col in self.columns

    def column_concept(self, col: str) -> str | None:
        c = self.columns.get(col)
        return c.concept if c else None


# ── Catalog protocol ────────────────────────────────────────────────────────

class Catalog(Protocol):
    def concept_exists(self, concept_id: str) -> bool: ...
    def concept_canonical_codes(self, concept_id: str) -> list[str] | None: ...
    def table_meta(self, schema: str, name: str) -> TableMeta | None: ...
    def table_binds_concept(self, schema: str, name: str, concept_id: str) -> bool | None: ...


# ── Fixture-backed catalog (tests) ─────────────────────────────────────────

class DictCatalog:
    """Catalog built from the ``examples/phase_8`` excerpt shape.

    ``tables`` is keyed by table name; each entry has ``schema``,
    ``partition_column``, ``estimated_daily_rows`` / ``estimated_total_rows``
    and ``columns`` (each ``{type, avg_bytes, concept}``). ``concepts`` is
    keyed by concept id with ``{type, canonical_values?, ops?}``.
    """

    def __init__(self, tables: dict[str, TableMeta], concepts: dict[str, dict[str, Any]]):
        self._tables = tables
        self._concepts = concepts

    @classmethod
    def from_excerpt(cls, raw: dict[str, Any]) -> "DictCatalog":
        tables: dict[str, TableMeta] = {}
        for name, t in (raw.get("tables") or {}).items():
            cols: dict[str, ColumnMeta] = {}
            for cname, c in (t.get("columns") or {}).items():
                c = c or {}
                cols[cname] = ColumnMeta(
                    type=c.get("type"),
                    avg_bytes=c.get("avg_bytes"),
                    concept=c.get("concept"),
                )
            tables[name] = TableMeta(
                schema_name=t.get("schema") or t.get("schema_name") or "",
                name=name,
                partition_column=t.get("partition_column"),
                estimated_daily_rows=t.get("estimated_daily_rows"),
                estimated_total_rows=t.get("estimated_total_rows"),
                columns=cols,
            )
        concepts = dict(raw.get("concepts") or {})
        return cls(tables, concepts)

    def concept_exists(self, concept_id: str) -> bool:
        return concept_id in self._concepts

    def concept_canonical_codes(self, concept_id: str) -> list[str] | None:
        c = self._concepts.get(concept_id)
        if not c:
            return None
        vals = c.get("canonical_values")
        if not vals:
            return None
        return [str(v) for v in vals]

    def table_meta(self, schema: str, name: str) -> TableMeta | None:
        tm = self._tables.get(name)
        if tm is None:
            return None
        # If a schema is recorded, require it to match (defensive against name
        # collisions across schemas).
        if tm.schema_name and schema and tm.schema_name != schema:
            return None
        return tm

    def table_binds_concept(self, schema: str, name: str, concept_id: str) -> bool | None:
        tm = self.table_meta(schema, name)
        if tm is None:
            return None
        return any(c.concept == concept_id for c in tm.columns.values())


# ── Production catalog (Flask) ──────────────────────────────────────────────

class AppCatalog:
    """Catalog backed by the live ``TABLE_DOC_STORE`` + ``CONCEPT_REGISTRY``.

    Defensive by design: anything the stores can't answer degrades to "cannot
    verify" (``None``) rather than raising — so the temporary ``/scope``
    endpoint validates a contract that references tables not yet onboarded
    into the catalog (warnings, not errors).

    Column→concept mapping uses the Phase 7 ``concept_bindings`` when a binding
    catalog is supplied, otherwise the Phase 6.5.b ``suggested_semantic_tag``
    on the table doc column. Either is sufficient for the concept-coverage
    *warning* (rule 3); the hard checks (rules 1/2/4/5/6/7) don't depend on it.
    """

    def __init__(self, table_doc_store, concept_registry, binding_catalog=None):
        self._docs = table_doc_store
        self._registry = concept_registry
        self._bindings = binding_catalog

    def concept_exists(self, concept_id: str) -> bool:
        reg = self._registry
        if reg is None:
            return False
        try:
            return bool(reg.has(concept_id))
        except Exception:
            return False

    def concept_canonical_codes(self, concept_id: str) -> list[str] | None:
        reg = self._registry
        if reg is None:
            return None
        try:
            concept = reg.get(concept_id)
        except Exception:
            return None
        if concept is None:
            return None
        codes = getattr(concept, "canonical_values", None)
        if not codes:
            return None
        try:
            return [cv.code for cv in concept.canonical_values]
        except Exception:
            return None

    def _load_doc(self, schema: str, name: str):
        store = self._docs
        if store is None:
            return None
        for loader in ("load", "get", "load_doc"):
            fn = getattr(store, loader, None)
            if fn is None:
                continue
            try:
                return fn(schema, name)
            except TypeError:
                try:
                    return fn(f"{schema}.{name}")
                except Exception:
                    continue
            except Exception:
                continue
        return None

    def table_meta(self, schema: str, name: str) -> TableMeta | None:
        doc = self._load_doc(schema, name)
        if doc is None:
            return None
        cols: dict[str, ColumnMeta] = {}
        doc_cols = getattr(doc, "columns", {}) or {}
        for cname, c in doc_cols.items():
            cols[cname] = ColumnMeta(
                type=getattr(c, "type", None),
                avg_bytes=None,  # Phase 6.5.b docs carry type, not avg_bytes.
                concept=getattr(c, "suggested_semantic_tag", None),
            )
        # Overlay Phase 7 human-verified bindings: a column bound to a concept
        # wins over the Phase 6.5.b suggested tag, so the routing partition
        # estimate (routing.estimate_post_scope_size) and concept coverage see
        # the same column→concept mapping the compiler pushes down.
        if self._bindings is not None:
            try:
                for b in self._bindings.get_bindings(schema, name):
                    col, concept = getattr(b, "column", None), getattr(b, "concept", None)
                    if not col or not concept:
                        continue
                    prev = cols.get(col)
                    cols[col] = ColumnMeta(
                        type=prev.type if prev else None,
                        avg_bytes=prev.avg_bytes if prev else None,
                        concept=concept,
                    )
            except Exception:
                pass
        return TableMeta(
            schema_name=getattr(doc, "schema_name", schema) or schema,
            name=getattr(doc, "table", name) or name,
            partition_column=getattr(doc, "partition_column", None),
            estimated_daily_rows=getattr(doc, "estimated_daily_rows", None),
            estimated_total_rows=None,
            columns=cols,
        )

    def table_binds_concept(self, schema: str, name: str, concept_id: str) -> bool | None:
        # Phase 7 human-verified bindings are authoritative: they are exactly
        # what the filter compiler consults (concepts/compiler.get_binding, via
        # scope/fetch._concept_pushdown), so a filter "has effect" on a table
        # iff a usable binding exists. The old code called a non-existent
        # `concepts_for_table`, swallowed the AttributeError, and fell through
        # to the Phase 6.5.b tag — mis-warning "not bound" for bind-doc tables
        # whose column carried no suggested_semantic_tag.
        if self._bindings is not None:
            try:
                if self._bindings.get_binding(schema, name, concept_id) is not None:
                    return True
                if self._bindings.get_doc(schema, name) is not None:
                    return False  # onboarded to Phase 7, but not this concept
            except Exception:
                pass
        # Legacy / not-yet-onboarded tables: fall back to the Phase 6.5.b
        # suggested_semantic_tag carried on the column.
        tm = self.table_meta(schema, name)
        if tm is None:
            return None
        return any(c.concept == concept_id for c in tm.columns.values())

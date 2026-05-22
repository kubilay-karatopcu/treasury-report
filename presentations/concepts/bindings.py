"""Column-binding catalog loader (Phase 7.b).

Reads per-table concept bindings from the hand-authored table docs under
``presentations/catalog/tables/<SCHEMA>/<TABLE>.yaml`` and indexes them by
``(schema, table)`` so the filter compiler can ask "how does table T realize
concept C?".

Two key behaviours:

- **Tolerant parse.** Table docs carry far more than bindings (columns,
  descriptions, filter hints from Phase 6.5.b). :class:`TableBindingDoc` uses
  ``extra="ignore"`` so it picks out only ``schema`` / ``table`` /
  ``primary_time_concept`` / ``concept_bindings`` and ignores the rest. This
  keeps the binding catalog decoupled from the full Phase 6.5.b TableDoc
  schema.

- **Confidence gating.** :meth:`BindingCatalog.get_binding` returns only
  ``human_verified`` bindings by default (locked decision §10.4). Inferred /
  llm_proposed bindings load into the catalog but are filtered out of the
  compiler path until an operator approves them.

Like the concept registry, the catalog hot-reloads on file mtime change so the
data team can edit table docs in dev without a restart.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from presentations.concepts.registry import _load_yaml  # bool-safe YAML loader
from presentations.concepts.schema import ColumnBinding


log = logging.getLogger(__name__)

_ORACLE_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")


class TableBindingDoc(BaseModel):
    """The binding-relevant projection of a table doc.

    ``extra="ignore"`` lets a full Phase 6.5.b table-doc YAML (with columns,
    descriptions, filter hints) parse cleanly — we only read the four fields
    that matter for concept compilation.
    """

    model_config = ConfigDict(extra="ignore")

    table: str
    schema_name: str = Field(alias="schema")
    primary_time_concept: str | None = None
    concept_bindings: list[ColumnBinding] = Field(default_factory=list)

    @field_validator("table", "schema_name")
    @classmethod
    def _check_ident(cls, v: str) -> str:
        if not _ORACLE_IDENT_RE.match(v):
            raise ValueError(f"identifier {v!r} must be an ALL_CAPS Oracle identifier")
        return v

    def key(self) -> tuple[str, str]:
        return (self.schema_name, self.table)


def load_table_binding_doc(raw: dict[str, Any]) -> TableBindingDoc:
    return TableBindingDoc.model_validate(raw)


class BindingCatalog:
    """Immutable index of ``(schema, table) → TableBindingDoc``."""

    def __init__(self, docs: list[TableBindingDoc]):
        self._by_key: dict[tuple[str, str], TableBindingDoc] = {
            d.key(): d for d in docs
        }

    # ── Constructors ─────────────────────────────────────────────────────

    @classmethod
    def from_dicts(cls, raws: list[dict[str, Any]]) -> "BindingCatalog":
        return cls([load_table_binding_doc(r) for r in raws])

    @classmethod
    def from_dir(cls, directory: str | Path) -> "BindingCatalog":
        """Walk ``<dir>/<SCHEMA>/<TABLE>.yaml`` recursively."""
        directory = Path(directory)
        docs: list[TableBindingDoc] = []
        if directory.exists():
            for path in sorted(directory.rglob("*.yaml")):
                try:
                    raw = _load_yaml(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.error("table doc %s failed to parse: %s", path, exc)
                    raise
                if not isinstance(raw, dict):
                    continue
                # Only treat files that look like table docs (have a table key).
                if "table" not in raw:
                    continue
                docs.append(load_table_binding_doc(raw))
        return cls(docs)

    @classmethod
    def empty(cls) -> "BindingCatalog":
        return cls([])

    # ── Read API ─────────────────────────────────────────────────────────

    def get_doc(self, schema: str, table: str) -> TableBindingDoc | None:
        return self._by_key.get((schema, table))

    def primary_time_concept(self, schema: str, table: str) -> str | None:
        doc = self._by_key.get((schema, table))
        return doc.primary_time_concept if doc else None

    def get_bindings(
        self, schema: str, table: str, *, verified_only: bool = True
    ) -> list[ColumnBinding]:
        doc = self._by_key.get((schema, table))
        if doc is None:
            return []
        if verified_only:
            return [b for b in doc.concept_bindings if b.is_usable]
        return list(doc.concept_bindings)

    def get_binding(
        self, schema: str, table: str, concept: str, *, verified_only: bool = True
    ) -> ColumnBinding | None:
        """Return the binding for ``concept`` on ``(schema, table)``, or None.

        If multiple bindings target the same concept (e.g. a table with two
        time columns both bound to distinct time concepts is fine, but two
        bindings for the *same* concept is ambiguous), the first usable one
        wins. Returns None when the table is concept-blind for this concept.
        """
        for b in self.get_bindings(schema, table, verified_only=verified_only):
            if b.concept == concept:
                return b
        return None

    def all_keys(self) -> list[tuple[str, str]]:
        return list(self._by_key.keys())

    def __len__(self) -> int:
        return len(self._by_key)


class CachedBindingCatalog:
    """Directory-backed catalog that hot-reloads on file change.

    Mirrors :class:`presentations.concepts.registry.CachedConceptRegistry`.
    """

    def __init__(self, directory: str | Path, *, check_interval_s: float = 2.0):
        self._dir = Path(directory)
        self._interval = float(check_interval_s)
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._sig: frozenset[tuple[str, int, int]] = frozenset()
        self._snapshot = BindingCatalog.empty()
        self._load()

    def _signature(self) -> frozenset[tuple[str, int, int]]:
        if not self._dir.exists():
            return frozenset()
        out: set[tuple[str, int, int]] = set()
        for p in self._dir.rglob("*.yaml"):
            try:
                st = p.stat()
            except OSError:
                continue
            out.add((str(p.relative_to(self._dir)), st.st_mtime_ns, st.st_size))
        return frozenset(out)

    def _load(self) -> None:
        self._sig = self._signature()
        self._snapshot = BindingCatalog.from_dir(self._dir)
        log.info("binding catalog loaded: %d tables from %s",
                 len(self._snapshot), self._dir)

    def _maybe_reload(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self._interval:
            return
        with self._lock:
            self._last_check = now
            sig = self._signature()
            if sig != self._sig:
                log.info("binding catalog change detected — reloading")
                try:
                    self._load()
                except Exception:
                    log.exception("binding catalog reload failed; keeping previous snapshot")

    @property
    def snapshot(self) -> BindingCatalog:
        self._maybe_reload()
        return self._snapshot

    def reload(self) -> None:
        with self._lock:
            self._last_check = time.monotonic()
            self._load()

    # Delegated read API.
    def get_doc(self, schema: str, table: str):
        return self.snapshot.get_doc(schema, table)

    def primary_time_concept(self, schema: str, table: str):
        return self.snapshot.primary_time_concept(schema, table)

    def get_bindings(self, schema: str, table: str, *, verified_only: bool = True):
        return self.snapshot.get_bindings(schema, table, verified_only=verified_only)

    def get_binding(self, schema: str, table: str, concept: str, *, verified_only: bool = True):
        return self.snapshot.get_binding(schema, table, concept, verified_only=verified_only)

    def all_keys(self):
        return self.snapshot.all_keys()

    def __len__(self) -> int:
        return len(self.snapshot)

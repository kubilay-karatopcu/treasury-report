"""Phase 6.5.b — Table documentation persistence.

Two backends, same Protocol:

- :class:`S3TableDocStore` — production, reads/writes
  ``prisma-treasury/table_docs/<schema>/<table>.yaml`` via the existing
  ``DataClient`` S3 helpers.
- :class:`LocalTableDocStore` — DEV_MODE and the offline runner, walks
  a local directory tree mirroring the same layout.

The store is read-mostly: writes happen from one place (the nightly
``jobs/sample_distinct_values.py`` cron) plus the data team's manual
migration PRs. There is no UI for editing TableDocs in v0; they live in
git for system/dept catalog tables and in S3 for samples written by the
cron.

The loader caches parsed TableDocs in process memory keyed by
``(schema, table)``. Cache invalidation is process-restart only — fine for
the nightly write cadence.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Optional, Protocol

import yaml

from presentations.table_docs.schema import TableDoc, load_table_doc_from_dict


log = logging.getLogger(__name__)


S3_PREFIX = "prisma-treasury/table_docs"


# ── Errors ────────────────────────────────────────────────────────────────

class TableDocStoreError(Exception):
    """Base."""


class TableDocNotFoundError(TableDocStoreError):
    """Requested (schema, table) is not in the store."""


# ── Key helpers ───────────────────────────────────────────────────────────

_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")


def _check_ident(schema: str, table: str) -> None:
    if not _IDENT_RE.match(schema):
        raise TableDocStoreError(f"invalid schema {schema!r}")
    if not _IDENT_RE.match(table):
        raise TableDocStoreError(f"invalid table {table!r}")


def table_key(schema: str, table: str) -> str:
    _check_ident(schema, table)
    return f"{S3_PREFIX}/{schema}/{table}.yaml"


def schema_prefix(schema: str) -> str:
    if not _IDENT_RE.match(schema):
        raise TableDocStoreError(f"invalid schema {schema!r}")
    return f"{S3_PREFIX}/{schema}/"


# ── Protocol ──────────────────────────────────────────────────────────────

class TableDocStore(Protocol):
    def load(self, schema: str, table: str) -> TableDoc: ...
    def save(self, doc: TableDoc) -> TableDoc: ...
    def list_tables(self, schema: str | None = None) -> list[tuple[str, str]]: ...
    def list_all_docs(self) -> list[TableDoc]: ...
    def exists(self, schema: str, table: str) -> bool: ...
    def delete(self, schema: str, table: str) -> bool: ...


# ── Serialisation ─────────────────────────────────────────────────────────

def _serialise(doc: TableDoc) -> bytes:
    payload = doc.to_yaml_shape()
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")


def _parse_bytes(data: bytes | str) -> TableDoc:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    parsed = yaml.safe_load(data)
    if not isinstance(parsed, dict):
        raise TableDocStoreError("table doc YAML must parse to a mapping")
    return load_table_doc_from_dict(parsed)


# ── Local filesystem backend ──────────────────────────────────────────────

class LocalTableDocStore:
    """Filesystem-backed store. Layout: ``<base>/<schema>/<table>.yaml``."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, schema: str, table: str) -> Path:
        _check_ident(schema, table)
        return self.base_dir / schema / f"{table}.yaml"

    def exists(self, schema: str, table: str) -> bool:
        try:
            return self._path(schema, table).is_file()
        except TableDocStoreError:
            return False

    def load(self, schema: str, table: str) -> TableDoc:
        p = self._path(schema, table)
        if not p.exists():
            raise TableDocNotFoundError(f"table doc {schema}.{table} not found")
        return _parse_bytes(p.read_bytes())

    def save(self, doc: TableDoc) -> TableDoc:
        p = self._path(doc.schema_name, doc.table)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_serialise(doc))
        log.info("local table doc saved: %s.%s", doc.schema_name, doc.table)
        return doc

    def delete(self, schema: str, table: str) -> bool:
        try:
            p = self._path(schema, table)
        except TableDocStoreError:
            return False
        if not p.exists():
            return False
        p.unlink()
        log.info("local table doc deleted: %s.%s", schema, table)
        return True

    def list_tables(self, schema: str | None = None) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        if not self.base_dir.exists():
            return results
        for schema_dir in self.base_dir.iterdir():
            if not schema_dir.is_dir():
                continue
            if schema and schema_dir.name != schema:
                continue
            for f in schema_dir.glob("*.yaml"):
                results.append((schema_dir.name, f.stem))
        results.sort()
        return results

    def list_all_docs(self) -> list[TableDoc]:
        docs: list[TableDoc] = []
        for schema, table in self.list_tables():
            try:
                docs.append(self.load(schema, table))
            except Exception as exc:
                log.warning("skip unreadable table doc %s.%s: %s", schema, table, exc)
        return docs


# ── S3 backend ────────────────────────────────────────────────────────────

class S3TableDocStore:
    """S3-backed store via ``DataClient`` helpers."""

    def __init__(self, dc):
        self.dc = dc

    def exists(self, schema: str, table: str) -> bool:
        try:
            data = self.dc.read_bytes(table_key(schema, table))
            return bool(data)
        except Exception:
            return False

    def load(self, schema: str, table: str) -> TableDoc:
        key = table_key(schema, table)
        try:
            data = self.dc.read_bytes(key)
        except Exception as exc:
            raise TableDocNotFoundError(f"failed to read {key}: {exc}") from exc
        if not data:
            raise TableDocNotFoundError(f"empty or missing {key}")
        return _parse_bytes(data)

    def save(self, doc: TableDoc) -> TableDoc:
        key = table_key(doc.schema_name, doc.table)
        self.dc._upload_bytes(key, _serialise(doc), content_type="application/x-yaml")
        log.info("s3 table doc saved: %s.%s", doc.schema_name, doc.table)
        return doc

    def delete(self, schema: str, table: str) -> bool:
        if not self.exists(schema, table):
            return False
        self.dc.delete_file(table_key(schema, table))
        log.info("s3 table doc deleted: %s.%s", schema, table)
        return True

    def list_tables(self, schema: str | None = None) -> list[tuple[str, str]]:
        prefix = f"{S3_PREFIX}/" + (f"{schema}/" if schema else "")
        try:
            keys = self.dc.list_prefix(prefix)
        except Exception as exc:
            log.warning("list_tables failed for %s: %s", prefix, exc)
            return []
        results: list[tuple[str, str]] = []
        for k in keys:
            m = re.search(
                rf"^{re.escape(S3_PREFIX)}/([A-Z_][A-Z0-9_$#]*)/([A-Z_][A-Z0-9_$#]*)\.yaml$",
                k,
            )
            if m:
                results.append((m.group(1), m.group(2)))
        results.sort()
        return results

    def list_all_docs(self) -> list[TableDoc]:
        docs: list[TableDoc] = []
        for schema, table in self.list_tables():
            try:
                docs.append(self.load(schema, table))
            except Exception as exc:
                log.warning("skip unreadable %s.%s: %s", schema, table, exc)
        return docs


# ── Process-level cache ──────────────────────────────────────────────────

class CachedTableDocStore:
    """Wrap any TableDocStore with an in-memory dict cache.

    Cache invalidation: process restart only. The cron writes back at most
    once a day, so a stale cache for one tick is acceptable.
    """

    def __init__(self, inner: TableDocStore):
        self._inner = inner
        self._cache: dict[tuple[str, str], TableDoc] = {}
        self._listed_all = False

    def exists(self, schema: str, table: str) -> bool:
        if (schema, table) in self._cache:
            return True
        return self._inner.exists(schema, table)

    def load(self, schema: str, table: str) -> TableDoc:
        key = (schema, table)
        if key in self._cache:
            return self._cache[key]
        doc = self._inner.load(schema, table)
        self._cache[key] = doc
        return doc

    def save(self, doc: TableDoc) -> TableDoc:
        saved = self._inner.save(doc)
        self._cache[(saved.schema_name, saved.table)] = saved
        return saved

    def delete(self, schema: str, table: str) -> bool:
        ok = self._inner.delete(schema, table)
        self._cache.pop((schema, table), None)
        return ok

    def list_tables(self, schema: str | None = None) -> list[tuple[str, str]]:
        return self._inner.list_tables(schema)

    def list_all_docs(self) -> list[TableDoc]:
        if self._listed_all:
            return list(self._cache.values())
        docs = self._inner.list_all_docs()
        for doc in docs:
            self._cache[(doc.schema_name, doc.table)] = doc
        self._listed_all = True
        return docs

    def clear(self) -> None:
        self._cache.clear()
        self._listed_all = False

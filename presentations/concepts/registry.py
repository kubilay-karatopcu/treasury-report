"""Concept registry loader + cache (spec §3.1, §11.a).

The registry is an in-memory index of concepts keyed by id, built from a set
of per-scope YAML files (``global.yaml``, ``<dept>.yaml``, ...). Two flavours:

- :class:`ConceptRegistry` — immutable snapshot. Build via ``from_dir`` /
  ``from_files`` / ``from_dicts``. Pure; no I/O after construction.
- :class:`CachedConceptRegistry` — directory-backed wrapper that reloads when
  any YAML's mtime/size changes (checked at most every ``check_interval_s``).
  This is what the Flask app injects into ``config["CONCEPT_REGISTRY"]`` so
  the data team can edit YAMLs in dev without a restart.

Scope precedence (locked decision §10.5): ``global`` > ``dept:*`` > ``user``.
Concept ids are globally unique across the registry. A lower-precedence file
that tries to redefine an id owned by a higher-precedence scope is **rejected**
(logged, not merged) — user/dept concepts are extension-only. A same-scope
duplicate id is a hard error.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import yaml

from presentations.concepts.schema import (
    Concept,
    ConceptFile,
    load_concept_file_from_dict,
)


log = logging.getLogger(__name__)


class _ConceptYamlLoader(yaml.SafeLoader):
    """SafeLoader that does NOT treat YAML 1.1 booleans (on/off/yes/no) as
    bools. Concept codes like ``ON`` (overnight) or ``NO`` must load as the
    strings "ON" / "NO", not ``True`` / ``False``. Only ``true``/``false``
    (any case) remain boolean — matching the YAML 1.2 core schema.
    """


# Rebuild the implicit resolvers: drop the bool resolver, re-add a 1.2-style
# one. PyYAML keys implicit resolvers by first character; rebuild the whole map.
_ConceptYamlLoader.yaml_implicit_resolvers = {
    ch: [(tag, regexp) for (tag, regexp) in resolvers
         if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_ConceptYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _load_yaml(text: str):
    """Parse concept YAML with the bool-safe loader."""
    return yaml.load(text, Loader=_ConceptYamlLoader)


def _scope_rank(scope: str | None) -> int:
    """Lower rank = higher precedence."""
    if scope == "global":
        return 0
    if scope and scope.startswith("dept:"):
        return 1
    if scope == "user":
        return 2
    return 99


def _apply_precedence(files: Iterable[ConceptFile]) -> list[Concept]:
    """Flatten concept files into a single id-unique list, honouring scope
    precedence. Higher-precedence scopes win id collisions; lower-precedence
    redefinitions are dropped with a warning. Same-rank collisions raise."""
    # Process highest precedence first so collisions are clean rejections.
    ordered = sorted(files, key=lambda f: _scope_rank(f.scope))
    chosen: dict[str, Concept] = {}
    for f in ordered:
        for c in f.concepts:
            existing = chosen.get(c.id)
            if existing is None:
                chosen[c.id] = c
                continue
            r_new, r_old = _scope_rank(c.scope), _scope_rank(existing.scope)
            if r_new == r_old:
                raise ValueError(
                    f"duplicate concept id {c.id!r} within scope {c.scope!r}"
                )
            # r_new > r_old (lower precedence) — extension-only violation.
            log.warning(
                "concept %r in scope %r ignored: cannot redefine %r-scoped concept",
                c.id, c.scope, existing.scope,
            )
    return list(chosen.values())


class ConceptRegistry:
    """Immutable concept index."""

    def __init__(self, concepts: list[Concept]):
        self._by_id: dict[str, Concept] = {c.id: c for c in concepts}

    # ── Constructors ─────────────────────────────────────────────────────

    @classmethod
    def from_files(cls, files: Iterable[ConceptFile]) -> "ConceptRegistry":
        return cls(_apply_precedence(files))

    @classmethod
    def from_dicts(cls, raw_files: Iterable[dict[str, Any]]) -> "ConceptRegistry":
        return cls.from_files([load_concept_file_from_dict(r) for r in raw_files])

    @classmethod
    def from_dir(cls, directory: str | Path) -> "ConceptRegistry":
        directory = Path(directory)
        files: list[ConceptFile] = []
        if directory.exists():
            for path in sorted(directory.glob("*.yaml")):
                try:
                    raw = _load_yaml(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.error("concept file %s failed to parse: %s", path, exc)
                    raise
                if raw is None:
                    continue
                files.append(load_concept_file_from_dict(raw))
        return cls.from_files(files)

    @classmethod
    def empty(cls) -> "ConceptRegistry":
        return cls([])

    # ── Read API ─────────────────────────────────────────────────────────

    def get(self, concept_id: str) -> Concept | None:
        return self._by_id.get(concept_id)

    def has(self, concept_id: str) -> bool:
        return concept_id in self._by_id

    def all_concepts(self) -> list[Concept]:
        return list(self._by_id.values())

    def all_ids(self) -> set[str]:
        return set(self._by_id.keys())

    def by_scope(self, scope: str) -> list[Concept]:
        """Concepts whose scope exactly matches (e.g. 'global', 'dept:treasury')."""
        return [c for c in self._by_id.values() if c.scope == scope]

    def resolve_value(self, concept_id: str, value: Any) -> str | None:
        """Resolve a value to its canonical code via the named concept.
        Returns None if the concept is unknown."""
        concept = self._by_id.get(concept_id)
        if concept is None:
            return None
        return concept.resolve_value(value)

    def __len__(self) -> int:
        return len(self._by_id)


class CachedConceptRegistry:
    """Directory-backed registry that hot-reloads on file change.

    Exposes the same read API as :class:`ConceptRegistry`, delegating to a
    current snapshot. Reloads when the set of ``*.yaml`` files (name + mtime +
    size) changes, checked at most every ``check_interval_s`` seconds.
    """

    def __init__(self, directory: str | Path, *, check_interval_s: float = 2.0):
        self._dir = Path(directory)
        self._interval = float(check_interval_s)
        self._lock = threading.Lock()
        self._last_check = 0.0
        self._sig: frozenset[tuple[str, int, int]] = frozenset()
        self._snapshot = ConceptRegistry.empty()
        self._load()

    # ── internals ────────────────────────────────────────────────────────

    def _signature(self) -> frozenset[tuple[str, int, int]]:
        if not self._dir.exists():
            return frozenset()
        out: set[tuple[str, int, int]] = set()
        for p in self._dir.glob("*.yaml"):
            try:
                st = p.stat()
            except OSError:
                continue
            out.add((p.name, st.st_mtime_ns, st.st_size))
        return frozenset(out)

    def _load(self) -> None:
        self._sig = self._signature()
        self._snapshot = ConceptRegistry.from_dir(self._dir)
        log.info("concept registry loaded: %d concepts from %s",
                 len(self._snapshot), self._dir)

    def _maybe_reload(self) -> None:
        now = time.monotonic()
        if now - self._last_check < self._interval:
            return
        with self._lock:
            self._last_check = now
            sig = self._signature()
            if sig != self._sig:
                log.info("concept registry change detected — reloading")
                try:
                    self._load()
                except Exception:
                    log.exception("concept registry reload failed; keeping previous snapshot")

    @property
    def snapshot(self) -> ConceptRegistry:
        self._maybe_reload()
        return self._snapshot

    def reload(self) -> None:
        """Force an immediate reload (bypasses the interval guard)."""
        with self._lock:
            self._last_check = time.monotonic()
            self._load()

    # ── delegated read API ────────────────────────────────────────────────

    def get(self, concept_id: str) -> Concept | None:
        return self.snapshot.get(concept_id)

    def has(self, concept_id: str) -> bool:
        return self.snapshot.has(concept_id)

    def all_concepts(self) -> list[Concept]:
        return self.snapshot.all_concepts()

    def all_ids(self) -> set[str]:
        return self.snapshot.all_ids()

    def by_scope(self, scope: str) -> list[Concept]:
        return self.snapshot.by_scope(scope)

    def resolve_value(self, concept_id: str, value: Any) -> str | None:
        return self.snapshot.resolve_value(concept_id, value)

    def __len__(self) -> int:
        return len(self.snapshot)


class S3ConceptRegistry:
    """S3-backed concept registry (prod parity with the other PRISMA stores).

    One concept-file YAML per scope under ``<prefix>/<scope>.yaml``
    (``global.yaml``, ``treasury.yaml``, …). A TTL cache holds an in-memory
    :class:`ConceptRegistry` snapshot (the hot filter-compile path stays
    in-memory); the cache is invalidated on :meth:`save_file`. If S3 is empty
    on first boot and a git ``fixtures_dir`` is given, the curated concepts are
    seeded to S3 once — so prod ships them without a manual migration and the
    local dir is no longer the source of truth.
    """

    def __init__(self, dc, *, prefix: str = "prisma-treasury/concepts",
                 fixtures_dir: str | Path | None = None, ttl_seconds: int = 30):
        self._dc = dc
        self._prefix = prefix.rstrip("/")
        self._ttl = max(0, int(ttl_seconds))
        self._lock = threading.Lock()
        self._loaded_at: float | None = None
        self._snapshot = ConceptRegistry.empty()
        if fixtures_dir is not None:
            self._seed_if_empty(Path(fixtures_dir))
        self._load()

    def _key(self, name: str) -> str:
        if not name.endswith(".yaml"):
            name += ".yaml"
        return f"{self._prefix}/{name}"

    def _list_keys(self) -> list[str]:
        try:
            keys = self._dc.list_prefix(f"{self._prefix}/") or []
        except Exception:
            log.warning("S3ConceptRegistry: list_prefix failed", exc_info=True)
            return []
        return [str(k) for k in keys if str(k).endswith(".yaml")]

    def _seed_if_empty(self, fixtures_dir: Path) -> None:
        if self._list_keys() or not fixtures_dir.exists():
            return
        n = 0
        for p in sorted(fixtures_dir.glob("*.yaml")):
            try:
                self._dc._upload_bytes(self._key(p.name), p.read_bytes(),
                                       content_type="application/x-yaml")
                n += 1
            except Exception:
                log.warning("S3ConceptRegistry seed failed for %s", p, exc_info=True)
        if n:
            log.info("S3ConceptRegistry seeded %d concept file(s) from %s", n, fixtures_dir)

    def _load(self) -> None:
        raws: list[dict] = []
        for key in self._list_keys():
            try:
                raw = _load_yaml(self._dc.read_bytes(key).decode("utf-8"))
            except Exception:
                log.error("S3ConceptRegistry: parse failed for %s", key, exc_info=True)
                continue
            if raw is not None:
                raws.append(raw)
        self._snapshot = ConceptRegistry.from_dicts(raws)
        self._loaded_at = time.monotonic()
        log.info("S3ConceptRegistry loaded: %d concepts (%d file(s)) from s3://%s",
                 len(self._snapshot), len(raws), self._prefix)

    def _maybe_reload(self) -> None:
        if self._loaded_at is not None and (time.monotonic() - self._loaded_at) < self._ttl:
            return
        with self._lock:
            if self._loaded_at is not None and (time.monotonic() - self._loaded_at) < self._ttl:
                return
            try:
                self._load()
            except Exception:
                log.exception("S3ConceptRegistry reload failed; keeping previous snapshot")

    @property
    def snapshot(self) -> ConceptRegistry:
        self._maybe_reload()
        return self._snapshot

    def reload(self) -> None:
        with self._lock:
            self._load()

    def save_file(self, scope_name: str, raw_dict: dict) -> None:
        """Persist a whole concept-file (one scope) YAML to S3 and reload."""
        body = yaml.safe_dump(raw_dict, allow_unicode=True, sort_keys=False,
                              default_flow_style=False).encode("utf-8")
        self._dc._upload_bytes(self._key(scope_name), body, content_type="application/x-yaml")
        with self._lock:
            self._load()

    # ── delegated read API (mirrors ConceptRegistry) ──────────────────────
    def get(self, concept_id: str) -> Concept | None:
        return self.snapshot.get(concept_id)

    def has(self, concept_id: str) -> bool:
        return self.snapshot.has(concept_id)

    def all_concepts(self) -> list[Concept]:
        return self.snapshot.all_concepts()

    def all_ids(self) -> set[str]:
        return self.snapshot.all_ids()

    def by_scope(self, scope: str) -> list[Concept]:
        return self.snapshot.by_scope(scope)

    def resolve_value(self, concept_id: str, value: Any) -> str | None:
        return self.snapshot.resolve_value(concept_id, value)

    def __len__(self) -> int:
        return len(self.snapshot)

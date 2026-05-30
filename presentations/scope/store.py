"""Scope contract persistence — S3-backed, version-bumping, immutable.

Layout (spec §2.1):
    prisma-treasury/presentations/<user>/<presentation_id>/scope_v<N>.yaml

Rules:
- Scope contracts are **immutable per version**. :meth:`save` always writes a
  *new* version ``N+1`` (or ``1`` for the first); callers do not pass a version
  number — the store owns version bumping. A write that would clobber an
  existing version is refused (defensive; the bump should make this
  impossible).
- ``<user>`` in the key is the contract's ``created_by`` (the sicil).
- Reads address a contract by ``presentation_id`` (+ optional version). The
  owning ``<user>`` is resolved by scanning the prefix — a linear walk, fine at
  the current scale (mirrors the snapshot/dashboard stores). When this gets
  slow, add a per-presentation index file.

Mirrors the DataClient helper surface used by :mod:`presentations.store`
(``_upload_bytes`` / ``read_text`` / ``list_prefix`` / ``delete_file``).
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Protocol

from presentations.scope.schema import ScopeContract, dump_scope_yaml, load_scope_yaml

log = logging.getLogger(__name__)


SCOPE_S3_PREFIX = "prisma-treasury/presentations"
_SCOPE_FILE_RE = re.compile(r"/scope_v(\d+)\.yaml$")


class ScopeStoreError(RuntimeError):
    """Base class for scope store failures."""


class ScopeNotFoundError(ScopeStoreError):
    def __init__(self, presentation_id: str, version: int | None = None):
        self.presentation_id = presentation_id
        self.version = version
        if version is None:
            super().__init__(f"no scope contract for presentation {presentation_id!r}")
        else:
            super().__init__(
                f"scope_v{version} not found for presentation {presentation_id!r}"
            )


class ScopeVersionExistsError(ScopeStoreError):
    """Refused to overwrite an existing (immutable) scope version."""

    def __init__(self, presentation_id: str, version: int):
        self.presentation_id = presentation_id
        self.version = version
        super().__init__(
            f"scope_v{version} already exists for {presentation_id!r}; "
            "scope versions are immutable"
        )


def _scope_key(user: str, pid: str, version: int) -> str:
    return f"{SCOPE_S3_PREFIX}/{user}/{pid}/scope_v{version}.yaml"


def _stamp_lineage(scope: ScopeContract, versions: list[int]) -> int:
    """Set the contract's ``version`` (and ``parent_version``) from the existing
    versions — the store owns version lineage (spec §2.1, §3.6). First version
    is ``1`` with no parent; each later save is ``max+1`` parented to the prior
    latest. Returns the assigned version."""
    if versions:
        parent = max(versions)
        scope.version = parent + 1
        scope.parent_version = parent
    else:
        scope.version = 1
        scope.parent_version = None
    return scope.version


# ── Protocol ────────────────────────────────────────────────────────────────

class ScopeStore(Protocol):
    def save(self, scope: ScopeContract) -> int: ...
    def load(self, presentation_id: str, version: int) -> ScopeContract: ...
    def load_latest(self, presentation_id: str) -> Optional[ScopeContract]: ...
    def list_versions(self, presentation_id: str) -> list[int]: ...
    def list_presentations(self) -> list[str]: ...


# ── S3 backend ───────────────────────────────────────────────────────────────

class S3ScopeStore:
    """Scope store backed by S3 via DataClient."""

    def __init__(self, dc):
        self.dc = dc

    def _index(self, presentation_id: str) -> dict[int, str]:
        """Map ``version -> key`` for a presentation by scanning the prefix."""
        try:
            keys = self.dc.list_prefix(SCOPE_S3_PREFIX + "/")
        except Exception as exc:
            log.warning("scope index: S3 list failed: %s", exc)
            return {}
        needle = f"/{presentation_id}/scope_v"
        out: dict[int, str] = {}
        for key in keys:
            if needle not in key:
                continue
            m = _SCOPE_FILE_RE.search(key)
            if m:
                out[int(m.group(1))] = key
        return out

    def list_versions(self, presentation_id: str) -> list[int]:
        return sorted(self._index(presentation_id).keys())

    def list_presentations(self) -> list[str]:
        """Distinct presentation ids across all owners (for the dataset cron)."""
        try:
            keys = self.dc.list_prefix(SCOPE_S3_PREFIX + "/")
        except Exception as exc:
            log.warning("scope list_presentations: S3 list failed: %s", exc)
            return []
        pids: set[str] = set()
        for key in keys:
            m = re.search(r"/([^/]+)/scope_v\d+\.yaml$", key)
            if m:
                pids.add(m.group(1))
        return sorted(pids)

    def save(self, scope: ScopeContract) -> int:
        next_v = _stamp_lineage(scope, self.list_versions(scope.presentation_id))
        key = _scope_key(scope.created_by, scope.presentation_id, next_v)
        # Immutability guard — the bump should make collisions impossible, but
        # never silently overwrite a version that already exists.
        if key in self._index(scope.presentation_id).values():
            raise ScopeVersionExistsError(scope.presentation_id, next_v)
        self.dc._upload_bytes(
            key,
            dump_scope_yaml(scope).encode("utf-8"),
            content_type="application/x-yaml",
        )
        log.info("scope saved: %s/%s scope_v%d (owner=%s)",
                 scope.created_by, scope.presentation_id, next_v, scope.created_by)
        return next_v

    def load(self, presentation_id: str, version: int) -> ScopeContract:
        key = self._index(presentation_id).get(version)
        if key is None:
            raise ScopeNotFoundError(presentation_id, version)
        return self._read(key, presentation_id, version)

    def load_latest(self, presentation_id: str) -> Optional[ScopeContract]:
        idx = self._index(presentation_id)
        if not idx:
            return None
        v = max(idx)
        return self._read(idx[v], presentation_id, v)

    def _read(self, key: str, pid: str, version: int) -> ScopeContract:
        try:
            text = self.dc.read_text(key)
        except Exception as exc:
            log.warning("scope read failed for %s: %s", key, exc)
            raise ScopeNotFoundError(pid, version) from exc
        return load_scope_yaml(text)


# ── Local filesystem backend (dev / tests) ───────────────────────────────────

class LocalScopeStore:
    """Filesystem-backed scope store — DEV_MODE / tests.

    Layout: ``<base_dir>/<user>/<presentation_id>/scope_v<N>.yaml``.
    """

    def __init__(self, base_dir):
        from pathlib import Path
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _index(self, presentation_id: str) -> dict[int, "object"]:
        out: dict[int, object] = {}
        if not self.base_dir.exists():
            return out
        for user_dir in self.base_dir.iterdir():
            if not user_dir.is_dir():
                continue
            pres_dir = user_dir / presentation_id
            if not pres_dir.is_dir():
                continue
            for f in pres_dir.glob("scope_v*.yaml"):
                m = _SCOPE_FILE_RE.search("/" + f.name)
                if m:
                    out[int(m.group(1))] = f
        return out

    def list_versions(self, presentation_id: str) -> list[int]:
        return sorted(self._index(presentation_id).keys())

    def list_presentations(self) -> list[str]:
        out: set[str] = set()
        if not self.base_dir.exists():
            return []
        for user_dir in self.base_dir.iterdir():
            if not user_dir.is_dir():
                continue
            for pres_dir in user_dir.iterdir():
                if pres_dir.is_dir() and any(pres_dir.glob("scope_v*.yaml")):
                    out.add(pres_dir.name)
        return sorted(out)

    def save(self, scope: ScopeContract) -> int:
        next_v = _stamp_lineage(scope, self.list_versions(scope.presentation_id))
        pres_dir = self.base_dir / scope.created_by / scope.presentation_id
        pres_dir.mkdir(parents=True, exist_ok=True)
        path = pres_dir / f"scope_v{next_v}.yaml"
        if path.exists():
            raise ScopeVersionExistsError(scope.presentation_id, next_v)
        path.write_text(dump_scope_yaml(scope), encoding="utf-8")
        log.info("local scope saved: %s", path)
        return next_v

    def load(self, presentation_id: str, version: int) -> ScopeContract:
        path = self._index(presentation_id).get(version)
        if path is None:
            raise ScopeNotFoundError(presentation_id, version)
        return load_scope_yaml(path.read_text(encoding="utf-8"))

    def load_latest(self, presentation_id: str) -> Optional[ScopeContract]:
        idx = self._index(presentation_id)
        if not idx:
            return None
        v = max(idx)
        return load_scope_yaml(idx[v].read_text(encoding="utf-8"))

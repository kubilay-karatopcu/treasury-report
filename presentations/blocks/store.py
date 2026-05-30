"""Phase 6.5 block store — S3-backed persistence at
``blocks/<team>/<block_id>/<version>.yaml``.

Provides two backends:

- :class:`S3BlockStore` — production, talks to the existing ``DataClient``.
- :class:`LocalBlockStore` — DEV_MODE / offline runner, filesystem.

Both implement the :class:`BlockStore` Protocol. The Flask app reads
``current_app.config["BLOCK_STORE"]`` at request time.

Versioning rules (spec §2.4):

- Each ``(team, block_id, version)`` triple is **immutable on disk**. Calling
  :meth:`BlockStore.save` for an existing key raises
  :class:`BlockAlreadyExistsError` — bumps must go through :meth:`save_new_version`.
- Soft delete sets ``deprecated: true`` on the latest version; older
  versions remain readable. Hard delete is not supported in v0.
- The library MVP (Phase 6.5.d) lists blocks via :meth:`list_blocks` with
  optional filters by team, tag, viz type, and a free-text search.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

import yaml

from presentations.blocks.schema import (
    Block,
    BlockDocument,
    block_to_dict,
    load_block_from_dict,
)


log = logging.getLogger(__name__)


S3_PREFIX = "prisma-treasury/v2-blocks"


# ── Errors ────────────────────────────────────────────────────────────────

class BlockStoreError(Exception):
    """Base for block store failures."""


class BlockNotFoundError(BlockStoreError):
    """Raised when a requested block / version is not in the store."""


class BlockAlreadyExistsError(BlockStoreError):
    """Raised when ``save`` would overwrite an existing version.

    Use :meth:`BlockStore.save_new_version` instead to bump the version.
    """


# ── Listing structure ─────────────────────────────────────────────────────

@dataclass
class BlockSummary:
    """Lightweight projection for library listings."""

    team: str
    id: str
    version: int
    title: str
    description: str
    tags: list[str]
    visualization_type: str
    owner: str
    created_at: str
    updated_at: str | None
    deprecated: bool

    @classmethod
    def from_block(cls, block: Block) -> "BlockSummary":
        return cls(
            team=block.team,
            id=block.id,
            version=block.version,
            title=block.title,
            description=block.description or "",
            tags=list(block.tags),
            visualization_type=block.visualization.type,
            owner=block.owner,
            created_at=block.created_at.isoformat(),
            updated_at=block.updated_at.isoformat() if block.updated_at else None,
            deprecated=bool(block.deprecated),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "id": self.id,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "visualization_type": self.visualization_type,
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deprecated": self.deprecated,
        }


# ── Protocol ──────────────────────────────────────────────────────────────

class BlockStore(Protocol):
    def save(self, block: Block) -> Block: ...
    def save_new_version(self, block: Block) -> Block: ...
    def load(self, team: str, block_id: str, version: int) -> Block: ...
    def load_latest(self, team: str, block_id: str) -> Block: ...
    def list_versions(self, team: str, block_id: str) -> list[int]: ...
    def list_blocks(
        self,
        *,
        team: str | None = None,
        tag: str | None = None,
        viz_type: str | None = None,
        search: str | None = None,
        include_deprecated: bool = False,
    ) -> list[BlockSummary]: ...
    def soft_delete(self, team: str, block_id: str) -> Block: ...


# ── Key helpers ───────────────────────────────────────────────────────────

_TEAM_OK = re.compile(r"^[a-z0-9_]+$")
_ID_OK = re.compile(r"^[a-z0-9_]+$")


def _check_identifiers(team: str, block_id: str) -> None:
    if not _TEAM_OK.match(team):
        raise BlockStoreError(f"invalid team id {team!r}")
    if not _ID_OK.match(block_id):
        raise BlockStoreError(f"invalid block id {block_id!r}")


def block_key(team: str, block_id: str, version: int) -> str:
    """S3 key for a block YAML."""
    _check_identifiers(team, block_id)
    return f"{S3_PREFIX}/{team}/{block_id}/v{int(version):04d}.yaml"


def block_prefix(team: str, block_id: str) -> str:
    _check_identifiers(team, block_id)
    return f"{S3_PREFIX}/{team}/{block_id}/"


# ── Serialisation ─────────────────────────────────────────────────────────

def _serialise_block(block: Block) -> bytes:
    """Validate the Block round-trips through Pydantic, then emit YAML.

    Uses ``BlockDocument`` so the on-disk shape carries the ``block:`` root
    wrapper expected by the YAML parser.
    """
    doc = BlockDocument(block=block).model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(
        doc,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")


def _parse_block_bytes(data: bytes | str) -> Block:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    parsed = yaml.safe_load(data)
    if not isinstance(parsed, dict):
        raise BlockStoreError("block YAML must parse to a mapping")
    return load_block_from_dict(parsed)


# ── Version bump (compare-and-swap) ─────────────────────────────────────────

_SAVE_NEW_VERSION_MAX_RETRIES = 5


def _save_new_version(store: "BlockStore", block: Block) -> Block:
    """Atomic version bump shared by both backends.

    ``save`` is an *atomic create* (``O_EXCL`` on disk, conditional PUT on S3),
    so when two callers race to bump the same block they compute the same next
    version and the loser gets :class:`BlockAlreadyExistsError`. We re-read the
    version list and retry; this converges because each retry sees the winner's
    freshly-written version. Replaces the previous unguarded read-modify-write
    that silently lost one writer's update.
    """
    last_exc: BlockAlreadyExistsError | None = None
    for _ in range(_SAVE_NEW_VERSION_MAX_RETRIES):
        existing = store.list_versions(block.team, block.id)
        next_version = max([*existing, block.version - 1]) + 1
        bumped = block.model_copy(update={
            "version": next_version,
            "updated_at": datetime.now(timezone.utc),
        })
        try:
            return store.save(bumped)
        except BlockAlreadyExistsError as exc:
            last_exc = exc  # someone took this version; re-read and retry
    raise BlockStoreError(
        f"save_new_version for {block.team}/{block.id}: concurrent writers "
        f"exhausted {_SAVE_NEW_VERSION_MAX_RETRIES} retries"
    ) from last_exc


# ── S3 conditional-write error classification ───────────────────────────────

def _s3_precondition_failed(exc: Exception) -> bool:
    """True when a conditional PUT was rejected because the key already exists
    (HTTP 412 PreconditionFailed) — i.e. a genuine version collision."""
    try:
        from botocore.exceptions import ClientError
    except Exception:
        return False
    if not isinstance(exc, ClientError):
        return False
    resp = getattr(exc, "response", {}) or {}
    if (resp.get("Error", {}) or {}).get("Code") in ("PreconditionFailed", "412"):
        return True
    return (resp.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") == 412


def _s3_conditional_unsupported(exc: Exception) -> bool:
    """True when the backend or the installed botocore cannot honour
    ``IfNoneMatch`` at all, so the caller should fall back to a plain PUT rather
    than mistaking the failure for a collision."""
    try:
        from botocore.exceptions import ClientError, ParamValidationError
    except Exception:
        return False
    if isinstance(exc, ParamValidationError):
        return True  # botocore model predates conditional writes
    if isinstance(exc, ClientError):
        resp = getattr(exc, "response", {}) or {}
        if (resp.get("Error", {}) or {}).get("Code") == "NotImplemented":
            return True
        return (resp.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") == 501
    return False


def _normalize_team_token(s: str) -> str:
    """Slug normalization that mirrors the editor UI's ``slugify`` exactly.

    Team slugs are produced client-side (SaveBlockModal.jsx) as::

        toLowerCase → NFD, strip combining marks → [^a-z0-9_]+ → "_"
                    → strip/collapse "_"

    We reproduce that algorithm byte-for-byte here so server-side team
    comparisons (fuzzy library search *and* the block-write auth gate) agree
    with how teams were actually stored. In particular Turkish capital ``İ``
    lowercases to ``i`` + U+0307 (a combining dot); stripping combining marks —
    rather than the old per-letter folding — keeps Python and JS in lockstep
    (the old code turned that dot into a spurious ``_`` and mapped dotless ``ı``
    to ``i`` while JS drops it, so the two diverged).
    """
    s = (s or "").lower()
    s = "".join(
        ch for ch in unicodedata.normalize("NFD", s)
        if not unicodedata.combining(ch)
    )
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    return s.strip("_")


def _block_matches_filters(
    block: Block,
    *,
    team: str | None,
    tag: str | None,
    viz_type: str | None,
    search: str | None,
) -> bool:
    if team:
        # Fuzzy team match: substring on normalized form. Lets the user
        # type "Finansal Yapay Zeka" and match "finansal_yapay_zeka_uygulamalari".
        needle_team = _normalize_team_token(team)
        if needle_team and needle_team not in _normalize_team_token(block.team):
            return False
    if tag and tag not in block.tags:
        return False
    if viz_type and block.visualization.type != viz_type:
        return False
    if search:
        needle = search.lower()
        haystack_parts = [block.title, block.description or "", " ".join(block.tags)]
        if block.documentation is not None:
            haystack_parts.extend([
                block.documentation.purpose or "",
                block.documentation.business_context or "",
                block.documentation.decision_support or "",
                block.documentation.known_limitations or "",
            ])
        haystack = " ".join(haystack_parts).lower()
        if needle not in haystack:
            return False
    return True


# ── Local filesystem backend (DEV_MODE) ───────────────────────────────────

class LocalBlockStore:
    """Filesystem-backed block store — used in DEV_MODE and offline runner.

    Layout mirrors S3: ``<base_dir>/<team>/<block_id>/v<NNNN>.yaml``.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── Private helpers ───────────────────────────────────────────────
    def _block_path(self, team: str, block_id: str, version: int) -> Path:
        _check_identifiers(team, block_id)
        return self.base_dir / team / block_id / f"v{int(version):04d}.yaml"

    def _block_dir(self, team: str, block_id: str) -> Path:
        _check_identifiers(team, block_id)
        return self.base_dir / team / block_id

    # ── Public API ────────────────────────────────────────────────────
    def save(self, block: Block) -> Block:
        path = self._block_path(block.team, block.id, block.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 'xb' = O_CREAT | O_EXCL: atomic create that fails if the version
            # already exists, so two concurrent writers can't clobber each other.
            with open(path, "xb") as fh:
                fh.write(_serialise_block(block))
        except FileExistsError:
            raise BlockAlreadyExistsError(
                f"block {block.team}/{block.id} version {block.version} already exists. "
                "Use save_new_version() to bump."
            )
        log.info("local block saved: %s/%s v%d", block.team, block.id, block.version)
        return block

    def save_new_version(self, block: Block) -> Block:
        """Persist ``block`` at the next free version (compare-and-swap retry)."""
        return _save_new_version(self, block)

    def load(self, team: str, block_id: str, version: int) -> Block:
        path = self._block_path(team, block_id, version)
        if not path.exists():
            raise BlockNotFoundError(
                f"block {team}/{block_id} v{version} not found"
            )
        return _parse_block_bytes(path.read_bytes())

    def load_latest(self, team: str, block_id: str) -> Block:
        versions = self.list_versions(team, block_id)
        if not versions:
            raise BlockNotFoundError(
                f"no versions found for {team}/{block_id}"
            )
        return self.load(team, block_id, max(versions))

    def list_versions(self, team: str, block_id: str) -> list[int]:
        directory = self._block_dir(team, block_id)
        if not directory.exists():
            return []
        out: list[int] = []
        for p in directory.glob("v*.yaml"):
            m = re.match(r"^v(\d+)\.yaml$", p.name)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def list_blocks(
        self,
        *,
        team: str | None = None,
        tag: str | None = None,
        viz_type: str | None = None,
        search: str | None = None,
        include_deprecated: bool = False,
    ) -> list[BlockSummary]:
        results: list[BlockSummary] = []
        if not self.base_dir.exists():
            return results
        # Walk: base_dir/<team>/<id>/v*.yaml — load latest per id.
        for team_dir in self.base_dir.iterdir():
            if not team_dir.is_dir():
                continue
            if team and team_dir.name != team:
                continue
            for block_dir in team_dir.iterdir():
                if not block_dir.is_dir():
                    continue
                versions = []
                for p in block_dir.glob("v*.yaml"):
                    m = re.match(r"^v(\d+)\.yaml$", p.name)
                    if m:
                        versions.append((int(m.group(1)), p))
                if not versions:
                    continue
                versions.sort()
                _, latest_path = versions[-1]
                try:
                    block = _parse_block_bytes(latest_path.read_bytes())
                except Exception as exc:
                    log.warning("skip unreadable block %s: %s", latest_path, exc)
                    continue
                if block.deprecated and not include_deprecated:
                    continue
                if not _block_matches_filters(
                    block, team=team, tag=tag, viz_type=viz_type, search=search,
                ):
                    continue
                results.append(BlockSummary.from_block(block))
        results.sort(key=lambda s: (s.created_at, s.team, s.id), reverse=True)
        return results

    def soft_delete(self, team: str, block_id: str) -> Block:
        block = self.load_latest(team, block_id)
        if block.deprecated:
            return block
        deprecated = block.model_copy(update={
            "deprecated": True,
            "updated_at": datetime.now(timezone.utc),
        })
        path = self._block_path(team, block_id, block.version)
        path.write_bytes(_serialise_block(deprecated))
        log.info("local block soft-deleted: %s/%s v%d", team, block_id, block.version)
        return deprecated


# ── S3 backend ────────────────────────────────────────────────────────────

class S3BlockStore:
    """Block store backed by S3 via ``DataClient``.

    Uses the same helper surface as the existing snapshot store:
    ``_upload_bytes``, ``read_bytes``, ``delete_file``, ``list_prefix``.
    """

    def __init__(self, dc):
        self.dc = dc

    # ── Private helpers ───────────────────────────────────────────────
    def _read_block(self, key: str) -> Block:
        data = self.dc.read_bytes(key)
        if not data:
            raise BlockNotFoundError(f"empty or missing S3 object {key}")
        return _parse_block_bytes(data)

    def _exists(self, key: str) -> bool:
        try:
            data = self.dc.read_bytes(key)
            return bool(data)
        except Exception:
            return False

    # ── Public API ────────────────────────────────────────────────────
    def save(self, block: Block) -> Block:
        key = block_key(block.team, block.id, block.version)
        body = _serialise_block(block)
        try:
            # Atomic conditional create — fails with 412 if the version exists.
            self.dc._upload_bytes(
                key, body, content_type="application/x-yaml", if_none_match=True,
            )
        except BlockAlreadyExistsError:
            raise
        except Exception as exc:
            if _s3_precondition_failed(exc):
                raise BlockAlreadyExistsError(
                    f"block {block.team}/{block.id} version {block.version} already "
                    "exists. Use save_new_version() to bump."
                ) from exc
            if _s3_conditional_unsupported(exc):
                # Backend / botocore can't honour IfNoneMatch — degrade to the
                # legacy best-effort check-then-write (a small race window
                # remains; logged so it's visible in prod).
                log.warning(
                    "S3 conditional write unsupported (%s); falling back to "
                    "non-atomic check-then-write for %s", exc, key,
                )
                if self._exists(key):
                    raise BlockAlreadyExistsError(
                        f"block {block.team}/{block.id} version {block.version} "
                        "already exists. Use save_new_version() to bump."
                    ) from exc
                self.dc._upload_bytes(key, body, content_type="application/x-yaml")
            else:
                raise
        log.info("s3 block saved: %s/%s v%d", block.team, block.id, block.version)
        return block

    def save_new_version(self, block: Block) -> Block:
        return _save_new_version(self, block)

    def load(self, team: str, block_id: str, version: int) -> Block:
        return self._read_block(block_key(team, block_id, version))

    def load_latest(self, team: str, block_id: str) -> Block:
        versions = self.list_versions(team, block_id)
        if not versions:
            raise BlockNotFoundError(f"no versions found for {team}/{block_id}")
        return self.load(team, block_id, max(versions))

    def list_versions(self, team: str, block_id: str) -> list[int]:
        prefix = block_prefix(team, block_id)
        try:
            keys = self.dc.list_prefix(prefix)
        except Exception as exc:
            log.warning("list_versions failed for %s: %s", prefix, exc)
            return []
        out: list[int] = []
        for k in keys:
            # k could be full key or just the file name depending on backend.
            name = k.rsplit("/", 1)[-1]
            m = re.match(r"^v(\d+)\.yaml$", name)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def list_blocks(
        self,
        *,
        team: str | None = None,
        tag: str | None = None,
        viz_type: str | None = None,
        search: str | None = None,
        include_deprecated: bool = False,
    ) -> list[BlockSummary]:
        prefix = f"{S3_PREFIX}/" + (f"{team}/" if team else "")
        try:
            keys = self.dc.list_prefix(prefix)
        except Exception as exc:
            log.warning("list_blocks failed for %s: %s", prefix, exc)
            return []

        # Group keys by (team, block_id), pick the highest version per group.
        groups: dict[tuple[str, str], int] = {}
        for raw in keys:
            m = re.search(
                rf"^{re.escape(S3_PREFIX)}/([a-z0-9_]+)/([a-z0-9_]+)/v(\d+)\.yaml$",
                raw,
            )
            if not m:
                continue
            t, bid, ver = m.group(1), m.group(2), int(m.group(3))
            existing = groups.get((t, bid))
            if existing is None or ver > existing:
                groups[(t, bid)] = ver

        results: list[BlockSummary] = []
        for (t, bid), ver in groups.items():
            try:
                block = self.load(t, bid, ver)
            except Exception as exc:
                log.warning("skip unreadable block %s/%s v%d: %s", t, bid, ver, exc)
                continue
            if block.deprecated and not include_deprecated:
                continue
            if not _block_matches_filters(
                block, team=team, tag=tag, viz_type=viz_type, search=search,
            ):
                continue
            results.append(BlockSummary.from_block(block))
        results.sort(key=lambda s: (s.created_at, s.team, s.id), reverse=True)
        return results

    def soft_delete(self, team: str, block_id: str) -> Block:
        block = self.load_latest(team, block_id)
        if block.deprecated:
            return block
        deprecated = block.model_copy(update={
            "deprecated": True,
            "updated_at": datetime.now(timezone.utc),
        })
        # Soft-delete writes over the latest version. We allow this single
        # exception to the immutability rule because the only thing changing
        # is the deprecated flag — historical content is preserved by older
        # versions, and v0 has no audit log.
        key = block_key(team, block_id, block.version)
        self.dc._upload_bytes(
            key,
            _serialise_block(deprecated),
            content_type="application/x-yaml",
        )
        log.info("s3 block soft-deleted: %s/%s v%d", team, block_id, block.version)
        return deprecated

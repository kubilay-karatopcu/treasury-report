"""
Snapshot persistence — frozen, shareable point-in-time copies of presentations.

Two backends:
- LocalSnapshotStore : filesystem (used in dev + as fallback)
- S3SnapshotStore    : production (Phase 5 stub; real impl wires up boto3 later)

A snapshot is:
- meta.json     : id, owner_id, source presentation_id + version, timestamp
- manifest.json : the frozen manifest (read-only)
- data/*.parquet (optional, future) : DuckDB views frozen as parquet so re-renders
                                      are reproducible without going back to Oracle

Phase 5 saves only the manifest. Parquet snapshotting will piggyback on the
Phase 6+ block-to-DuckDB binding work, when it actually buys reproducibility.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger(__name__)


def _gen_snapshot_id() -> str:
    """`s_<11-char-urlsafe-token>` — ~64 bits of entropy, hard to guess."""
    return "s_" + secrets.token_urlsafe(8)


# ── Protocol ─────────────────────────────────────────────────────────────────

class SnapshotStore(Protocol):
    def save(self, manifest: dict, owner_id: str) -> dict: ...
    def load(self, snapshot_id: str) -> Optional[dict]: ...
    def list_for_owner(self, owner_id: str) -> list[dict]: ...


# ── Local filesystem backend ─────────────────────────────────────────────────

class LocalSnapshotStore:
    """File-backed snapshot store. Layout:

        {base_dir}/{snapshot_id}/manifest.json
        {base_dir}/{snapshot_id}/meta.json
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, manifest: dict, owner_id: str) -> dict:
        sid = _gen_snapshot_id()
        snap_dir = self.base_dir / sid
        snap_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "snapshot_id": sid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "owner_id": owner_id,
            "presentation_id": manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "title": manifest.get("meta", {}).get("title", ""),
        }

        # Snapshot is intentionally a deep copy — frozen.
        frozen = dict(manifest)
        frozen["snapshot_id"] = sid
        frozen["snapshot_created_at"] = meta["created_at"]

        (snap_dir / "manifest.json").write_text(
            json.dumps(frozen, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (snap_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("snapshot saved: %s (presentation=%s, owner=%s)",
                 sid, meta["presentation_id"], owner_id)
        return meta

    def load(self, snapshot_id: str) -> Optional[dict]:
        snap_dir = self.base_dir / snapshot_id
        manifest_path = snap_dir / "manifest.json"
        meta_path = snap_dir / "meta.json"
        if not manifest_path.exists():
            return None
        return {
            "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
            "meta": (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists() else {}
            ),
        }

    def list_for_owner(self, owner_id: str) -> list[dict]:
        results = []
        for snap_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not snap_dir.is_dir():
                continue
            meta_path = snap_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if meta.get("owner_id") == owner_id:
                results.append(meta)
        return results


# ── S3 backend (Phase 5 stub) ────────────────────────────────────────────────

class S3SnapshotStore:
    """Stub. Real impl wires up the corporate S3 client in Phase 5.5+ when the
    Treasury team has that infra ready. For now raises if anyone tries to use it."""

    def __init__(self, bucket: str, prefix: str = "presentations/"):
        self.bucket = bucket
        self.prefix = prefix

    def save(self, manifest: dict, owner_id: str) -> dict:
        raise NotImplementedError("S3 snapshot backend not wired yet — use LocalSnapshotStore")

    def load(self, snapshot_id: str) -> Optional[dict]:
        raise NotImplementedError("S3 snapshot backend not wired yet — use LocalSnapshotStore")

    def list_for_owner(self, owner_id: str) -> list[dict]:
        raise NotImplementedError("S3 snapshot backend not wired yet — use LocalSnapshotStore")

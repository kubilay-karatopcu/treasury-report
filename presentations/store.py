"""
Snapshot persistence — S3-backed.

Layout:
  prisma-treasury/snapshots/<snapshot_id>/manifest.json
  prisma-treasury/snapshots/<snapshot_id>/meta.json

Snapshots are frozen, shareable, point-in-time copies of a presentation.
The manifest is captured verbatim and stored alongside a small meta record
(owner, source presentation, timestamp, title).

Parquet data snapshotting (so re-renders are reproducible without going back
to Oracle) is a future addition — phase 5 only persists the manifest.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional, Protocol

log = logging.getLogger(__name__)


S3_PREFIX = "prisma-treasury/snapshots"


def _gen_snapshot_id() -> str:
    """`s_<11-char-urlsafe>` — ~64 bits of entropy, hard to guess."""
    return "s_" + secrets.token_urlsafe(8)


def _manifest_key(snapshot_id: str) -> str:
    return f"{S3_PREFIX}/{snapshot_id}/manifest.json"


def _meta_key(snapshot_id: str) -> str:
    return f"{S3_PREFIX}/{snapshot_id}/meta.json"


def _owner_index_key(owner_id: str) -> str:
    """Optional per-owner index file. Not used in the simple list-by-prefix
    path; reserved for future caching layer."""
    return f"{S3_PREFIX}/_index/{owner_id}.json"


# ── Protocol ─────────────────────────────────────────────────────────────────

class SnapshotStore(Protocol):
    def save(self, manifest: dict, owner_id: str) -> dict: ...
    def load(self, snapshot_id: str) -> Optional[dict]: ...
    def list_for_owner(self, owner_id: str) -> list[dict]: ...
    def delete(self, snapshot_id: str) -> bool: ...


# ── S3 backend ───────────────────────────────────────────────────────────────

class S3SnapshotStore:
    """Snapshot store backed by S3 via DataClient.

    Uses DataClient's native helpers:
      - _upload_bytes(key, body, content_type)
      - read_json(key)
      - delete_file(key)
      - list_prefix(prefix)
    """

    def __init__(self, dc):
        self.dc = dc

    def save(self, manifest: dict, owner_id: str) -> dict:
        sid = _gen_snapshot_id()

        meta = {
            "snapshot_id":      sid,
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "owner_id":         owner_id,
            "presentation_id":  manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "title":            manifest.get("meta", {}).get("title", ""),
        }

        # Snapshot is intentionally a deep copy — frozen.
        frozen = dict(manifest)
        frozen["snapshot_id"] = sid
        frozen["snapshot_created_at"] = meta["created_at"]

        self.dc._upload_bytes(
            _manifest_key(sid),
            json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        self.dc._upload_bytes(
            _meta_key(sid),
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )

        log.info("snapshot saved: %s (presentation=%s, owner=%s)",
                 sid, meta["presentation_id"], owner_id)
        return meta

    def load(self, snapshot_id: str) -> Optional[dict]:
        try:
            manifest = self.dc.read_json(_manifest_key(snapshot_id))
        except Exception as exc:
            msg = str(exc)
            if "NoSuchKey" in msg or "404" in msg or "not found" in msg.lower():
                return None
            log.warning("snapshot load failed for %s: %s", snapshot_id, exc)
            return None

        try:
            meta = self.dc.read_json(_meta_key(snapshot_id))
        except Exception:
            meta = {}

        return {"manifest": manifest, "meta": meta}

    def list_for_owner(self, owner_id: str) -> list[dict]:
        """Walk every snapshot's meta.json and filter by owner_id.

        Linear-scan; fine for the current scale (~10–100 snapshots). When this
        gets slow, add a per-owner index file under `_index/`.
        """
        try:
            keys = self.dc.list_prefix(S3_PREFIX + "/")
        except Exception as exc:
            log.warning("snapshot list_for_owner: S3 list failed: %s", exc)
            return []

        results = []
        for key in keys:
            if not key.endswith("/meta.json"):
                continue
            if "/_index/" in key:
                continue
            try:
                meta = self.dc.read_json(key)
            except Exception as exc:
                log.warning("snapshot list: failed to read %s: %s", key, exc)
                continue
            if meta.get("owner_id") == owner_id:
                results.append(meta)

        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def delete(self, snapshot_id: str) -> bool:
        """Remove both objects. Idempotent."""
        ok = True
        for key in (_manifest_key(snapshot_id), _meta_key(snapshot_id)):
            try:
                self.dc.delete_file(key)
            except Exception as exc:
                log.warning("snapshot delete: failed for %s: %s", key, exc)
                ok = False
        return ok


# ── Local filesystem backend (dev mode) ──────────────────────────────────────

class LocalSnapshotStore:
    """Snapshot store backed by the local filesystem — DEV_MODE only.

    Layout: <base_dir>/<snapshot_id>/manifest.json + meta.json
    """

    def __init__(self, base_dir):
        from pathlib import Path
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, manifest: dict, owner_id: str) -> dict:
        sid = _gen_snapshot_id()
        meta = {
            "snapshot_id":      sid,
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "owner_id":         owner_id,
            "presentation_id":  manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "title":            manifest.get("meta", {}).get("title", ""),
        }
        frozen = dict(manifest)
        frozen["snapshot_id"] = sid
        frozen["snapshot_created_at"] = meta["created_at"]

        snap_dir = self.base_dir / sid
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "manifest.json").write_text(
            json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (snap_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("local snapshot saved: %s", sid)
        return meta

    def load(self, snapshot_id: str) -> Optional[dict]:
        snap_dir = self.base_dir / snapshot_id
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        meta_path = snap_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return {"manifest": manifest, "meta": meta}

    def list_for_owner(self, owner_id: str) -> list[dict]:
        results = []
        if not self.base_dir.exists():
            return results
        for snap_dir in self.base_dir.iterdir():
            if not snap_dir.is_dir():
                continue
            meta_path = snap_dir / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.get("owner_id") == owner_id:
                results.append(meta)
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def delete(self, snapshot_id: str) -> bool:
        import shutil
        snap_dir = self.base_dir / snapshot_id
        if snap_dir.exists():
            shutil.rmtree(snap_dir)
        return True
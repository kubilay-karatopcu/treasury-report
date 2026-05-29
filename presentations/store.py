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
    def list_all_meta(self) -> list[dict]: ...  # Phase 10C — cross-owner scan
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

    def save(self, manifest: dict, owner_id: str, *,
             title_override: str | None = None,
             description: str = "",
             bound_experts: list[str] | None = None) -> dict:
        sid = _gen_snapshot_id()

        # Resolve title — Phase 10D save modal can override the manifest's
        # meta.title with a snapshot-specific name without mutating the
        # source manifest. Empty/whitespace falls back to the manifest title.
        resolved_title = (title_override or "").strip() or (
            manifest.get("meta", {}).get("title", "") or ""
        )
        # Resolve bound_experts — kwarg is authoritative (save modal's user
        # choice); falls back to whatever the manifest carries. Empty list
        # is a valid explicit choice (snapshot bound to nobody → only
        # findable via direct link, won't appear under any expert).
        resolved_experts = (
            list(bound_experts)
            if bound_experts is not None
            else list(manifest.get("bound_experts") or [])
        )

        meta = {
            "snapshot_id":      sid,
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "owner_id":         owner_id,
            "presentation_id":  manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "title":            resolved_title,
            "description":      description,           # Phase 10D
            "bound_experts":    resolved_experts,
        }

        # Snapshot is intentionally a deep copy — frozen.
        frozen = dict(manifest)
        frozen["snapshot_id"] = sid
        frozen["snapshot_created_at"] = meta["created_at"]
        frozen["bound_experts"] = resolved_experts
        # If the user overrode the title, mirror it onto the frozen manifest's
        # meta so the snapshot view shows the chosen name.
        if title_override and title_override.strip():
            frozen_meta = dict(frozen.get("meta") or {})
            frozen_meta["title"] = resolved_title
            frozen["meta"] = frozen_meta

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

        log.info("snapshot saved: %s (presentation=%s, owner=%s, experts=%s)",
                 sid, meta["presentation_id"], owner_id, resolved_experts)
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
        return [m for m in self.list_all_meta() if m.get("owner_id") == owner_id]

    def list_all_meta(self) -> list[dict]:
        """Phase 10C — return every snapshot's meta.json, newest first.

        Used by the expert detail page (which needs cross-owner filtering by
        ``bound_experts``). Same linear scan as ``list_for_owner``; when the
        snapshot count gets large we'll add an `_index/by_expert/` overlay.
        """
        try:
            keys = self.dc.list_prefix(S3_PREFIX + "/")
        except Exception as exc:
            log.warning("snapshot list_all_meta: S3 list failed: %s", exc)
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
                log.warning("snapshot list_all_meta: failed to read %s: %s", key, exc)
                continue
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

    def set_bound_experts(
        self, snapshot_id: str, bound_experts: list[str],
    ) -> bool:
        """Replace the snapshot's ``bound_experts`` list (meta + frozen
        manifest). Used by the Uzman edit form to keep the reverse-direction
        link in sync — see ``_sync_expert_to_snapshot_links``. Returns
        ``True`` on success, ``False`` if the snapshot is missing."""
        try:
            meta = self.dc.read_json(_meta_key(snapshot_id))
        except Exception:
            return False
        meta["bound_experts"] = list(bound_experts)
        try:
            self.dc._upload_bytes(
                _meta_key(snapshot_id),
                json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )
        except Exception as exc:
            log.warning("snapshot set_bound_experts: meta write failed for %s: %s",
                        snapshot_id, exc)
            return False
        # Mirror into the frozen manifest so reloads pick it up too.
        try:
            manifest = self.dc.read_json(_manifest_key(snapshot_id))
            manifest["bound_experts"] = list(bound_experts)
            self.dc._upload_bytes(
                _manifest_key(snapshot_id),
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )
        except Exception as exc:
            log.warning("snapshot set_bound_experts: manifest mirror failed for %s: %s",
                        snapshot_id, exc)
        return True


# ── Local filesystem backend (dev mode) ──────────────────────────────────────

class LocalSnapshotStore:
    """Snapshot store backed by the local filesystem — DEV_MODE only.

    Layout: <base_dir>/<snapshot_id>/manifest.json + meta.json
    """

    def __init__(self, base_dir):
        from pathlib import Path
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, manifest: dict, owner_id: str, *,
             title_override: str | None = None,
             description: str = "",
             bound_experts: list[str] | None = None) -> dict:
        sid = _gen_snapshot_id()
        # Phase 10D: see S3SnapshotStore.save for the resolution rules.
        resolved_title = (title_override or "").strip() or (
            manifest.get("meta", {}).get("title", "") or ""
        )
        resolved_experts = (
            list(bound_experts)
            if bound_experts is not None
            else list(manifest.get("bound_experts") or [])
        )
        meta = {
            "snapshot_id":      sid,
            "created_at":       datetime.now(timezone.utc).isoformat(),
            "owner_id":         owner_id,
            "presentation_id":  manifest.get("id"),
            "manifest_version": manifest.get("version"),
            "title":            resolved_title,
            "description":      description,
            "bound_experts":    resolved_experts,
        }
        frozen = dict(manifest)
        frozen["snapshot_id"] = sid
        frozen["snapshot_created_at"] = meta["created_at"]
        frozen["bound_experts"] = resolved_experts
        if title_override and title_override.strip():
            frozen_meta = dict(frozen.get("meta") or {})
            frozen_meta["title"] = resolved_title
            frozen["meta"] = frozen_meta

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
        return [m for m in self.list_all_meta() if m.get("owner_id") == owner_id]

    def list_all_meta(self) -> list[dict]:
        """Phase 10C — local mirror of S3SnapshotStore.list_all_meta."""
        results: list[dict] = []
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
            results.append(meta)
        results.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return results

    def delete(self, snapshot_id: str) -> bool:
        import shutil
        snap_dir = self.base_dir / snapshot_id
        if snap_dir.exists():
            shutil.rmtree(snap_dir)
        return True

    def set_bound_experts(
        self, snapshot_id: str, bound_experts: list[str],
    ) -> bool:
        """Local mirror of :meth:`S3SnapshotStore.set_bound_experts`."""
        snap_dir = self.base_dir / snapshot_id
        meta_path = snap_dir / "meta.json"
        manifest_path = snap_dir / "manifest.json"
        if not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        meta["bound_experts"] = list(bound_experts)
        try:
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("local snapshot set_bound_experts: meta write failed: %s", exc)
            return False
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["bound_experts"] = list(bound_experts)
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                log.warning(
                    "local snapshot set_bound_experts: manifest mirror failed: %s",
                    exc,
                )
        return True


# ════════════════════════════════════════════════════════════════════════════
# Dashboard store — published presentations (R > Ekip Raporları)
# ════════════════════════════════════════════════════════════════════════════

D_S3_PREFIX = "prisma-treasury/dashboards"


def _gen_dashboard_id() -> str:
    return "d_" + secrets.token_urlsafe(8)


def _d_manifest_key(did: str) -> str:
    return f"{D_S3_PREFIX}/{did}/manifest.json"


def _d_meta_key(did: str) -> str:
    return f"{D_S3_PREFIX}/{did}/meta.json"


class DashboardStore(Protocol):
    def save(self, manifest: dict, *, name: str, owner_id: str,
             owner_department: str, audience: list[str]) -> dict: ...
    def update(self, dashboard_id: str, *, manifest: dict | None = None,
               name: str | None = None, audience: list[str] | None = None) -> dict | None: ...
    def load(self, dashboard_id: str) -> Optional[dict]: ...
    def list_visible(self, user_sicil: str, user_department: str) -> list[dict]: ...
    def delete(self, dashboard_id: str) -> bool: ...


class S3DashboardStore:
    """Dashboards = R > Ekip Raporları altında listelenen yayınlanmış sunumlar.

    Audience modeli:
      - meta["audience_sicils"]: list[str] — direkt eklenmiş sicil'ler
      - meta["audience_departments"]: list[str] — bu departmandaki herkes görür
      - Sahip her zaman görür.
    """

    def __init__(self, dc):
        self.dc = dc

    def save(self, manifest, *, name, owner_id, owner_department, audience):
        did = _gen_dashboard_id()
        meta = {
            "dashboard_id":          did,
            "created_at":            datetime.now(timezone.utc).isoformat(),
            "updated_at":            datetime.now(timezone.utc).isoformat(),
            "owner_id":              owner_id,
            "owner_department":      owner_department,
            "presentation_id":       manifest.get("id"),
            "manifest_version":      manifest.get("version"),
            "name":                  name or manifest.get("meta", {}).get("title", "(adsız)"),
            "audience_sicils":       list(audience or []),
            "audience_departments":  [owner_department] if owner_department else [],
        }

        frozen = dict(manifest)
        frozen["dashboard_id"]         = did
        frozen["dashboard_created_at"] = meta["created_at"]

        self.dc._upload_bytes(
            _d_manifest_key(did),
            json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        self.dc._upload_bytes(
            _d_meta_key(did),
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        log.info("dashboard saved: %s (name=%r, owner=%s)", did, meta["name"], owner_id)
        return meta

    def update(self, dashboard_id, *, manifest=None, name=None, audience=None):
        loaded = self.load(dashboard_id)
        if not loaded:
            return None
        meta = loaded["meta"]
        if name is not None:
            meta["name"] = name
        if audience is not None:
            meta["audience_sicils"] = list(audience)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()

        if manifest is not None:
            frozen = dict(manifest)
            frozen["dashboard_id"]         = dashboard_id
            frozen["dashboard_created_at"] = meta.get("created_at")
            meta["manifest_version"] = manifest.get("version")
            self.dc._upload_bytes(
                _d_manifest_key(dashboard_id),
                json.dumps(frozen, ensure_ascii=False, indent=2).encode("utf-8"),
                content_type="application/json",
            )

        self.dc._upload_bytes(
            _d_meta_key(dashboard_id),
            json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        return meta

    def load(self, dashboard_id):
        try:
            manifest = self.dc.read_json(_d_manifest_key(dashboard_id))
        except Exception as exc:
            msg = str(exc)
            if "NoSuchKey" in msg or "404" in msg or "not found" in msg.lower():
                return None
            log.warning("dashboard load failed for %s: %s", dashboard_id, exc)
            return None
        try:
            meta = self.dc.read_json(_d_meta_key(dashboard_id))
        except Exception:
            meta = {}
        return {"manifest": manifest, "meta": meta}

    def list_visible(self, user_sicil, user_department):
        try:
            keys = self.dc.list_prefix(D_S3_PREFIX + "/")
        except Exception as exc:
            log.warning("dashboard list_visible: S3 list failed: %s", exc)
            return []

        results = []
        for key in keys:
            if not key.endswith("/meta.json"):
                continue
            try:
                meta = self.dc.read_json(key)
            except Exception as exc:
                log.warning("dashboard list: failed to read %s: %s", key, exc)
                continue
            if _can_see_dashboard(meta, user_sicil, user_department):
                results.append(meta)
        results.sort(key=lambda m: m.get("updated_at", m.get("created_at", "")), reverse=True)
        return results

    def delete(self, dashboard_id):
        ok = True
        for key in (_d_manifest_key(dashboard_id), _d_meta_key(dashboard_id)):
            try:
                self.dc.delete_file(key)
            except Exception as exc:
                log.warning("dashboard delete: failed for %s: %s", key, exc)
                ok = False
        return ok


def _can_see_dashboard(meta: dict, user_sicil: str, user_department: str) -> bool:
    if not meta:
        return False
    if meta.get("owner_id") == user_sicil:
        return True
    if user_sicil and user_sicil in (meta.get("audience_sicils") or []):
        return True
    if user_department and user_department in (meta.get("audience_departments") or []):
        return True
    return False


class LocalDashboardStore:
    """Dev fallback — filesystem-backed dashboard store."""

    def __init__(self, base_dir):
        from pathlib import Path
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, manifest, *, name, owner_id, owner_department, audience):
        did = _gen_dashboard_id()
        meta = {
            "dashboard_id":          did,
            "created_at":            datetime.now(timezone.utc).isoformat(),
            "updated_at":            datetime.now(timezone.utc).isoformat(),
            "owner_id":              owner_id,
            "owner_department":      owner_department,
            "presentation_id":       manifest.get("id"),
            "manifest_version":      manifest.get("version"),
            "name":                  name or manifest.get("meta", {}).get("title", "(adsız)"),
            "audience_sicils":       list(audience or []),
            "audience_departments":  [owner_department] if owner_department else [],
        }
        frozen = dict(manifest)
        frozen["dashboard_id"]         = did
        frozen["dashboard_created_at"] = meta["created_at"]

        d = self.base_dir / did
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("local dashboard saved: %s (%r)", did, meta["name"])
        return meta

    def update(self, dashboard_id, *, manifest=None, name=None, audience=None):
        loaded = self.load(dashboard_id)
        if not loaded:
            return None
        meta = loaded["meta"]
        if name is not None:
            meta["name"] = name
        if audience is not None:
            meta["audience_sicils"] = list(audience)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()

        d = self.base_dir / dashboard_id
        if manifest is not None:
            frozen = dict(manifest)
            frozen["dashboard_id"]         = dashboard_id
            frozen["dashboard_created_at"] = meta.get("created_at")
            meta["manifest_version"] = manifest.get("version")
            (d / "manifest.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def load(self, dashboard_id):
        d = self.base_dir / dashboard_id
        mp = d / "manifest.json"
        if not mp.exists():
            return None
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        return {"manifest": manifest, "meta": meta}

    def list_visible(self, user_sicil, user_department):
        results = []
        if not self.base_dir.exists():
            return results
        for d in self.base_dir.iterdir():
            if not d.is_dir():
                continue
            mp = d / "meta.json"
            if not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _can_see_dashboard(meta, user_sicil, user_department):
                results.append(meta)
        results.sort(key=lambda m: m.get("updated_at", m.get("created_at", "")), reverse=True)
        return results

    def delete(self, dashboard_id):
        import shutil
        d = self.base_dir / dashboard_id
        if d.exists():
            shutil.rmtree(d)
        return True



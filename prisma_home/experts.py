"""Expert dataclass + filesystem-backed store (Phase 10B).

A "PRISMA expert" is a consumer-facing persona — one of six (Likidite,
Mevduat, Fonlama, NII, Security, Kredi) — that:

- Surfaces curated snapshots/blocks as citations under their briefing
- Carries a voice (system prompt + voice examples) the briefing engine
  uses when paraphrasing data
- Owns a recipe describing how to assemble a daily briefing

Experts are versioned YAML files under ``examples/phase_10/experts/``
for dev fixtures; production deploys ship them via the same path. Per
spec §10.2, global/dept-level experts live in git (this module's scope)
and a future Phase 14 may add user-scoped experts in a DB.

This phase is **backend only**. ``LocalExpertStore`` is wired into
``app.config["EXPERT_STORE"]`` and surfaced via the JSON endpoints in
``prisma_home/routes.py``. UI consumption (landing cards, expert detail)
arrives in Phase 10C.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import yaml


log = logging.getLogger(__name__)


# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass
class Expert:
    """A single expert persona, loaded from one YAML file.

    Fields mirror spec §5.1 exactly. Defaults exist so partial YAMLs
    (e.g. minimal test fixtures) don't crash the loader; validation
    that requires every field is left to the caller / tests.
    """
    id: str
    version: int
    code: str
    name: str
    domain_label: str
    short_description: str
    persona: dict = field(default_factory=dict)
    bound_content: dict = field(default_factory=lambda: {"blocks": [], "snapshots": [], "processes": []})
    briefing_recipe: dict = field(default_factory=lambda: {"cache_ttl_seconds": 1800, "sections": []})
    access_scope: dict = field(default_factory=lambda: {"read": ["*"], "edit": []})
    ui: dict = field(default_factory=lambda: {"accent_color": "#6B8AFD", "glyph": ""})
    status: str = "active"
    # W8 — departman bakışları: aynı uzman altında departmana göre farklı süreç
    # seti (topic'lere gruplu) + brifing odağı + sıkı erişim. Boşsa legacy
    # davranış (bound_content.processes + access_scope). Bkz. expert_views.py.
    #   [{departments: [str], label?, briefing_focus?,
    #     topics: [{title, processes: [pid]}]}]
    department_views: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Expert":
        # Tolerate missing optional fields by relying on the dataclass defaults.
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ── Store Protocol ───────────────────────────────────────────────────────────

class ExpertStore(Protocol):
    """Contract for the expert registry. Two concrete implementations:
    ``LocalExpertStore`` (filesystem YAML, DEV fixtures) and
    ``S3ExpertStore`` (prod parity with the other PRISMA stores). ``save``
    persists an edited expert; the Atölye editor (``uzman_save``) uses it.
    """
    def list_all(self) -> list[Expert]: ...
    def load(self, expert_id: str) -> Optional[Expert]: ...
    def list_for_user(self, user) -> list[Expert]: ...
    def exists(self, expert_id: str) -> bool: ...
    def save(self, expert) -> Expert: ...
    def delete(self, expert_id: str) -> bool: ...


def _serialize_expert(expert) -> tuple[str, dict, bytes]:
    """(id, dict, yaml-bytes) for an Expert or a plain dict. Shared by both
    stores so on-disk and S3 YAML have identical shape."""
    data = expert.to_dict() if isinstance(expert, Expert) else dict(expert)
    eid = data.get("id")
    if not eid:
        raise ValueError("expert requires a non-empty 'id'")
    body = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).encode("utf-8")
    return eid, data, body


# ── Filesystem-backed store ──────────────────────────────────────────────────

class LocalExpertStore:
    """Reads ``*.yaml`` from a directory; one expert per file.

    The cache is loaded lazily on first access and held for the lifetime
    of the store. Because experts are immutable per version (spec §5.1),
    no invalidation is required during a process's lifetime; pod restart
    re-reads from disk.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self._cache: dict[str, Expert] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.base_dir.exists():
            # Tolerate a missing directory so DEV / CI without fixtures still
            # boots; the store just looks empty.
            self._loaded = True
            return
        for yaml_path in sorted(self.base_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                # Skip malformed files but don't crash the whole store; a
                # failing YAML shouldn't take down the landing page.
                continue
            if not isinstance(data, dict) or "id" not in data:
                continue
            expert = Expert.from_dict(data)
            self._cache[expert.id] = expert
        self._loaded = True

    def list_all(self) -> list[Expert]:
        self._ensure_loaded()
        # Sorted by code for deterministic UI ordering (LIQ, DEP, FND, NII, SEC, KRD
        # → alphabetical: DEP, FND, KRD, LIQ, NII, SEC). Frontend can re-sort.
        return sorted(self._cache.values(), key=lambda e: e.code)

    def load(self, expert_id: str) -> Optional[Expert]:
        self._ensure_loaded()
        return self._cache.get(expert_id)

    def exists(self, expert_id: str) -> bool:
        self._ensure_loaded()
        return expert_id in self._cache

    def list_for_user(self, user) -> list[Expert]:
        """Görünürlük: W8 departman bakışı varsa SIKI (eşleşen bakış şart),
        yoksa legacy access_scope.read ('*'/dept). ``can_access`` tek karar
        noktasıdır — detay/edit route'larıyla tutarlı."""
        from prisma_home.expert_views import can_access

        self._ensure_loaded()
        dept = getattr(user, "department", None) or ""
        out = [e for e in self._cache.values() if can_access(e, dept)]
        return sorted(out, key=lambda e: e.code)

    def save(self, expert) -> Expert:
        """Persist an expert as ``<id>.yaml`` and refresh the cache. Accepts
        an :class:`Expert` or a plain dict."""
        eid, data, body = _serialize_expert(expert)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / f"{eid}.yaml").write_bytes(body)
        self._ensure_loaded()
        exp = Expert.from_dict(data)
        self._cache[eid] = exp
        return exp

    def delete(self, expert_id: str) -> bool:
        """Remove ``<id>.yaml`` and drop it from the cache. Idempotent."""
        self._ensure_loaded()
        p = self.base_dir / f"{expert_id}.yaml"
        existed = p.exists()
        if existed:
            p.unlink()
        self._cache.pop(expert_id, None)
        return existed


# ── S3-backed store (prod parity) ────────────────────────────────────────────

class S3ExpertStore:
    """Reads/writes one ``*.yaml`` per expert under ``EXPERTS_S3_PREFIX`` via
    the DataClient — prod parity with S3BlockStore / S3SnapshotStore.

    A short TTL cache avoids re-listing S3 on every landing render; ``save``
    invalidates it so Atölye edits show on the next request. Unlike the
    file store, experts here are *editable at runtime* and survive pod
    restarts (the reason experts moved off ``examples/`` for prod).
    """

    PREFIX = "prisma-treasury/experts"

    def __init__(self, dc, ttl_seconds: int = 30):
        self._dc = dc
        self._ttl = max(0, int(ttl_seconds))
        self._cache: dict[str, Expert] = {}
        self._loaded_at: float | None = None

    def _key(self, expert_id: str) -> str:
        return f"{self.PREFIX}/{expert_id}.yaml"

    def _ensure_loaded(self, *, force: bool = False) -> None:
        if (not force and self._loaded_at is not None
                and (time.monotonic() - self._loaded_at) < self._ttl):
            return
        cache: dict[str, Expert] = {}
        try:
            keys = self._dc.list_prefix(f"{self.PREFIX}/")
        except Exception:
            keys = []
        for key in keys or []:
            if not str(key).endswith(".yaml"):
                continue
            try:
                data = yaml.safe_load(self._dc.read_bytes(key).decode("utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or "id" not in data:
                continue
            exp = Expert.from_dict(data)
            cache[exp.id] = exp
        self._cache = cache
        self._loaded_at = time.monotonic()

    def list_all(self) -> list[Expert]:
        self._ensure_loaded()
        return sorted(self._cache.values(), key=lambda e: e.code)

    def load(self, expert_id: str) -> Optional[Expert]:
        self._ensure_loaded()
        return self._cache.get(expert_id)

    def exists(self, expert_id: str) -> bool:
        self._ensure_loaded()
        return expert_id in self._cache

    def list_for_user(self, user) -> list[Expert]:
        # W8 — LocalExpertStore ile aynı karar: can_access (bakış varsa sıkı).
        from prisma_home.expert_views import can_access

        self._ensure_loaded()
        dept = getattr(user, "department", None) or ""
        out = [e for e in self._cache.values() if can_access(e, dept)]
        return sorted(out, key=lambda e: e.code)

    def save(self, expert) -> Expert:
        eid, data, body = _serialize_expert(expert)
        self._dc._upload_bytes(self._key(eid), body, content_type="application/x-yaml")
        self._ensure_loaded(force=True)
        return self._cache.get(eid) or Expert.from_dict(data)

    def delete(self, expert_id: str) -> bool:
        """Delete the expert's S3 object + refresh the cache. Idempotent."""
        existed = self.exists(expert_id)
        try:
            self._dc.delete_file(self._key(expert_id))
        except Exception:
            log.warning("S3ExpertStore: delete failed for %s", expert_id, exc_info=True)
            return False
        self._ensure_loaded(force=True)
        return existed

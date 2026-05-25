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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import yaml


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

    @classmethod
    def from_dict(cls, d: dict) -> "Expert":
        # Tolerate missing optional fields by relying on the dataclass defaults.
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ── Store Protocol ───────────────────────────────────────────────────────────

class ExpertStore(Protocol):
    """Read-only contract for the expert registry.

    Concrete implementations: ``LocalExpertStore`` (filesystem YAML). A
    future ``S3ExpertStore`` would live alongside for prod parity with
    other PRISMA stores.
    """
    def list_all(self) -> list[Expert]: ...
    def load(self, expert_id: str) -> Optional[Expert]: ...
    def list_for_user(self, user) -> list[Expert]: ...
    def exists(self, expert_id: str) -> bool: ...


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
        """Filter by access_scope.read — '*' matches all users, otherwise
        the user's department must be in the read list.

        Per spec §9.5: for initial launch all 6 experts are visible to
        everyone. This method honours the YAML's access_scope.read field
        so the data team can tighten visibility later without a code change.
        """
        self._ensure_loaded()
        dept = getattr(user, "department", None) or ""
        out: list[Expert] = []
        for expert in self._cache.values():
            read = expert.access_scope.get("read") or []
            if "*" in read or dept in read:
                out.append(expert)
        return sorted(out, key=lambda e: e.code)

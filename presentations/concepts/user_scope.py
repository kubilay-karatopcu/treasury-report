"""User-scoped concepts (Phase 7.d, spec §3.4, §10.5).

A power user can define a one-off concept inside a single presentation. User
concepts are **extension-only**:

- their ``id`` must not collide with any global/departmental concept
  (rejected at save — they cannot redefine or mask a higher-scope concept);
- they are visible only within the presentation that declares them;
- promotion to departmental is the *only* path to wider scope.

Storage: the presentation's manifest carries a top-level ``user_concepts``
array (per-presentation JSON, persisted with the manifest). The effective
registry for a presentation = base (global+dept) ⊕ that array, with base
winning any id collision.
"""
from __future__ import annotations

from typing import Any

from presentations.concepts.schema import Concept, load_concept_file_from_dict  # noqa: F401
from presentations.concepts.registry import ConceptRegistry


class UserConceptError(ValueError):
    """Raised when a user concept is invalid or collides with a base concept."""


def validate_user_concept(base: ConceptRegistry, raw: dict[str, Any]) -> Concept:
    """Validate a user-authored concept against the base registry.

    Forces ``scope: user``. Rejects ids that collide with any global/dept
    concept (extension-only, §10.5). Raises :class:`UserConceptError` with a
    Turkish, user-facing message on any problem.
    """
    if not isinstance(raw, dict):
        raise UserConceptError("Kavram tanımı bir nesne olmalı.")
    data = {k: v for k, v in raw.items() if k != "scope"}
    data["scope"] = "user"
    try:
        concept = Concept.model_validate(data)
    except Exception as exc:  # pydantic ValidationError → friendly message
        raise UserConceptError(f"Kavram tanımı geçersiz: {exc}") from exc

    if base.has(concept.id):
        existing = base.get(concept.id)
        raise UserConceptError(
            f"'{concept.id}' zaten {existing.scope!r} kapsamında tanımlı — "
            "kullanıcı kavramı global/departman kavramını yeniden tanımlayamaz. "
            "Farklı bir id seçin veya o kavramı doğrudan kullanın."
        )
    return concept


def build_effective_registry(
    base: ConceptRegistry, user_concepts_raw: list[dict[str, Any]] | None
) -> ConceptRegistry:
    """Merge base + a presentation's user concepts into one registry.

    Base concepts always win id collisions (extension-only). Malformed user
    concepts are skipped defensively (the add endpoint validates on write, so
    persisted ones are normally clean; this guards against hand-edited
    manifests).
    """
    concepts = list(base.all_concepts())
    base_ids = base.all_ids()
    for raw in (user_concepts_raw or []):
        try:
            data = {k: v for k, v in raw.items() if k != "scope"}
            data["scope"] = "user"
            c = Concept.model_validate(data)
        except Exception:
            continue
        if c.id in base_ids:
            continue  # extension-only — base wins
        concepts.append(c)
    return ConceptRegistry(concepts)

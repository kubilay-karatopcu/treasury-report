"""Pydantic models for the Phase 7 concept registry (spec §3.1).

A concept is a versioned business term: a canonical id, a human label, and
(for enum/bucket types) a canonical value alphabet with aliases. Concepts
live in YAML files keyed by scope — see :mod:`presentations.concepts.registry`
for the loader.

On-disk YAML shape (one file per scope):

    version: 1
    scope: global
    owners:
      department: data_platform
      contact: data.platform@qnbfb.com
    concepts:
      - id: currency
        name: "Para Birimi"
        type: enum
        description: "..."
        canonical_values:
          - code: TRY
            label: "Türk Lirası"
            aliases: [TL, TRL]
          - code: USD
            label: "US Doları"
            aliases: ["US Dollar"]
        related_concepts: [counterparty]

Validation rules (spec §3.1, §10):
- ``id`` is a lower_snake slug, globally unique within a registry.
- ``type`` is one of ``enum`` / ``time`` / ``bucket`` / ``scalar``.
- ``time`` concepts carry NO ``canonical_values`` (they're parameterized by
  range + granularity).
- ``bucket`` canonical values MAY carry a ``day_range`` ``[lo, hi]`` (hi may
  be ``null`` for an open-ended top bucket); the binding inference + the
  ``bucket_from_range`` transform key off this.
- canonical value ``code``s are unique within a concept.

This module does NOT import :mod:`presentations.variables.semantic_tags` —
the dependency runs the other way (semantic_tags reads from the registry,
with a static fallback). Keeping schema.py free of that import avoids a
circular dependency.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


# ── Identifier types ──────────────────────────────────────────────────────

# Concept ids are lower_snake slugs (mirrors the Phase 6.5 semantic_tag shape
# so migration is a direct copy).
_CONCEPT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Scope: "global" | "user" | "dept:<name>".
_SCOPE_RE = re.compile(r"^(global|user|dept:[a-z0-9_]+)$")

ConceptId = Annotated[
    str,
    StringConstraints(min_length=2, max_length=60, pattern=_CONCEPT_ID_RE.pattern),
]

ConceptType = Literal["enum", "time", "bucket", "scalar"]


# ── Canonical value ────────────────────────────────────────────────────────

class CanonicalValue(BaseModel):
    """One canonical value of an enum/bucket concept.

    ``code`` is the canonical token emitted to SQL (e.g. ``USD``). ``aliases``
    are accepted on import / value resolution but never emitted. ``day_range``
    only applies to ``bucket`` concepts: ``[lo, hi]`` with ``lo`` inclusive,
    ``hi`` exclusive; ``hi`` may be ``null`` for the open-ended top bucket.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    label: str | None = None
    aliases: list[str] = Field(default_factory=list)
    day_range: list[int | None] | None = None

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for a in v:
            if not isinstance(a, str) or not a.strip():
                raise ValueError(f"alias must be a non-empty string: {a!r}")
            out.append(a.strip())
        return out

    @model_validator(mode="after")
    def _check_day_range(self) -> "CanonicalValue":
        if self.day_range is None:
            return self
        if len(self.day_range) != 2:
            raise ValueError(
                f"day_range for {self.code!r} must be [lo, hi]; got {self.day_range!r}"
            )
        lo, hi = self.day_range
        if lo is None:
            raise ValueError(f"day_range low bound for {self.code!r} cannot be null")
        if not isinstance(lo, int) or (hi is not None and not isinstance(hi, int)):
            raise ValueError(f"day_range bounds for {self.code!r} must be integers")
        if hi is not None and hi <= lo:
            raise ValueError(
                f"day_range for {self.code!r} must have hi > lo; got [{lo}, {hi}]"
            )
        return self


# ── Owners ─────────────────────────────────────────────────────────────────

class ConceptOwners(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str | None = None
    contact: str | None = None


# ── Concept ──────────────────────────────────────────────────────────────

class Concept(BaseModel):
    """A single business concept.

    ``scope`` is optional on disk: when a concept omits it, the registry
    stamps the owning file's scope. A concept loaded standalone (tests) may
    leave it ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    id: ConceptId
    name: str = Field(min_length=1, max_length=120)
    type: ConceptType
    description: str | None = None
    scope: str | None = None
    version: int = Field(default=1, ge=1, le=100_000)

    # enum / bucket only.
    canonical_values: list[CanonicalValue] = Field(default_factory=list)

    # time only.
    granularity_default: Literal["day", "hour", "minute"] | None = None
    reference_anchor: str | None = None

    owners: ConceptOwners | None = None
    related_concepts: list[str] = Field(default_factory=list)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _SCOPE_RE.match(v):
            raise ValueError(
                f"scope {v!r} must be 'global', 'user', or 'dept:<name>'"
            )
        return v

    @model_validator(mode="after")
    def _check_type_consistency(self) -> "Concept":
        if self.type == "time":
            if self.canonical_values:
                raise ValueError(
                    f"concept {self.id!r}: time concepts cannot declare "
                    "canonical_values (they're parameterized by range + granularity)"
                )
        else:
            # granularity_default / reference_anchor only meaningful for time.
            if self.granularity_default is not None:
                raise ValueError(
                    f"concept {self.id!r}: granularity_default only applies to "
                    "time concepts"
                )

        # canonical value codes must be unique within the concept.
        codes = [cv.code for cv in self.canonical_values]
        if len(codes) != len(set(codes)):
            dupes = sorted({c for c in codes if codes.count(c) > 1})
            raise ValueError(
                f"concept {self.id!r}: duplicate canonical value codes: {dupes}"
            )

        # An alias must not collide with a different value's code, and must be
        # unique across the concept (case-insensitive) so resolution is
        # unambiguous.
        seen: dict[str, str] = {}  # lowered token → owning code
        for cv in self.canonical_values:
            key = cv.code.lower()
            if key in seen and seen[key] != cv.code:
                raise ValueError(
                    f"concept {self.id!r}: value {cv.code!r} collides "
                    f"case-insensitively with {seen[key]!r}"
                )
            seen[key] = cv.code
        for cv in self.canonical_values:
            for alias in cv.aliases:
                key = alias.lower()
                if key in seen and seen[key] != cv.code:
                    raise ValueError(
                        f"concept {self.id!r}: alias {alias!r} on {cv.code!r} "
                        f"collides with value/alias of {seen[key]!r}"
                    )
                seen[key] = cv.code
        return self

    # ── Value resolution ────────────────────────────────────────────────

    def canonical_codes(self) -> list[str]:
        return [cv.code for cv in self.canonical_values]

    def _value_index(self) -> dict[str, str]:
        """Lower-cased {code|alias → canonical code} lookup."""
        idx: dict[str, str] = {}
        for cv in self.canonical_values:
            idx[cv.code.lower()] = cv.code
            for alias in cv.aliases:
                idx.setdefault(alias.lower(), cv.code)
        return idx

    def resolve_value(self, value: Any) -> str | None:
        """Resolve a user/import value to its canonical code, or None.

        Resolution order: exact code (case-sensitive) → case-insensitive code
        → alias (case-insensitive). For concepts with no canonical_values
        (e.g. an enum the data team hasn't filled in yet), resolution is a
        permissive pass-through: the input string is returned as-is so the
        Phase 6.5 "accept whatever the user types" behaviour is preserved
        (spec §6.3).
        """
        if value is None:
            return None
        s = str(value)
        if not self.canonical_values:
            return s  # permissive pass-through for unfilled concepts
        # Exact code match first (cheap, common).
        for cv in self.canonical_values:
            if cv.code == s:
                return cv.code
        return self._value_index().get(s.lower())

    def get_value(self, code: str) -> CanonicalValue | None:
        for cv in self.canonical_values:
            if cv.code == code:
                return cv
        return None


# ── File wrapper ───────────────────────────────────────────────────────────

class ConceptFile(BaseModel):
    """On-disk root of a single concept YAML file (one per scope)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=100_000)
    scope: str
    owners: ConceptOwners | None = None
    concepts: list[Concept] = Field(default_factory=list)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: str) -> str:
        if not _SCOPE_RE.match(v):
            raise ValueError(
                f"file scope {v!r} must be 'global', 'user', or 'dept:<name>'"
            )
        return v

    @model_validator(mode="after")
    def _stamp_and_check(self) -> "ConceptFile":
        ids = [c.id for c in self.concepts]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate concept ids in file: {dupes}")
        # Stamp the file scope onto concepts that omit it; reject mismatches.
        for c in self.concepts:
            if c.scope is None:
                c.scope = self.scope
            elif c.scope != self.scope:
                raise ValueError(
                    f"concept {c.id!r} declares scope {c.scope!r} but lives in "
                    f"a {self.scope!r} file"
                )
        return self


def load_concept_file_from_dict(raw: dict[str, Any]) -> ConceptFile:
    """Parse one concept YAML dict into a validated :class:`ConceptFile`."""
    return ConceptFile.model_validate(raw)


# ════════════════════════════════════════════════════════════════════════
# Column bindings (Phase 7.b) — how a table column realizes a concept.
# Lives in table docs under catalog/tables/<SCHEMA>/<TABLE>.yaml.
# ════════════════════════════════════════════════════════════════════════

_COLUMN_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")

# Provenance of a binding. Only ``human_verified`` reaches the compiler
# (locked decision §10.4). Inferred / llm_proposed bindings live in the YAML
# but are gated until an operator approves them in the 7.c review UI.
ConfidenceLevel = Literal[
    "human_verified",
    "llm_proposed",
    "inferred_sample",
    "inferred_regex",
    "inferred_dtype",
]


class IdentityTransform(BaseModel):
    """Column value is already canonical. ``col IN (...)``."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["identity"] = "identity"


class MapTransform(BaseModel):
    """Canonical → table value via an inline dict, then ``col IN (mapped...)``.

    ``pairs`` maps **table value → canonical code** (the direction observed in
    the data); the compiler inverts it to translate a canonical filter value
    back to the table's stored values.
    """
    model_config = ConfigDict(extra="forbid")
    kind: Literal["map"]
    pairs: dict[str, str] = Field(min_length=1)


class LookupTransform(BaseModel):
    """Value via a dimension-table join.

    ``col IN (SELECT dim_key FROM dim_table WHERE dim_canonical IN (...))``.
    """
    model_config = ConfigDict(extra="forbid")
    kind: Literal["lookup"]
    dim_table: str = Field(min_length=1)
    dim_key: str = Field(min_length=1)
    dim_canonical: str = Field(min_length=1)


class BucketFromRangeTransform(BaseModel):
    """Numeric column → canonical bucket via the concept's ``day_range``s.

    ``ranges_concept`` names the bucket concept whose canonical values carry
    ``day_range`` arrays; the compiler expands each selected bucket into a
    ``(col >= lo AND col < hi)`` clause.
    """
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bucket_from_range"]
    ranges_concept: str = Field(min_length=1)


class TimeTruncationTransform(BaseModel):
    """TIMESTAMP column compared as a date: ``TRUNC(col) BETWEEN :from AND :to``."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["time_truncation"]


Transform = Annotated[
    Union[
        IdentityTransform,
        MapTransform,
        LookupTransform,
        BucketFromRangeTransform,
        TimeTruncationTransform,
    ],
    Field(discriminator="kind"),
]


class ColumnBinding(BaseModel):
    """Declares that ``column`` in some table realizes ``concept`` via ``transform``."""

    model_config = ConfigDict(extra="forbid")

    concept: ConceptId
    column: str = Field(min_length=1, max_length=128)
    transform: Transform
    confidence: ConfidenceLevel = "llm_proposed"
    verified_at: Any | None = None     # datetime on disk; kept loose for round-trips
    verified_by: str | None = None

    @field_validator("column")
    @classmethod
    def _check_column(cls, v: str) -> str:
        if not _COLUMN_RE.match(v):
            raise ValueError(f"column {v!r} must be an ALL_CAPS Oracle identifier")
        return v

    @property
    def is_usable(self) -> bool:
        """Only human-verified bindings reach the compiler (§10.4)."""
        return self.confidence == "human_verified"

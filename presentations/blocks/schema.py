"""Pydantic models for Phase 6.5 blocks.

A block is the atomic, version-controlled artifact authored in the block
editor. The YAML on disk has a single top-level ``block:`` key whose value
matches :class:`Block`. See ``examples/phase_6_5/sample_block.yaml`` for the
canonical fixture and ``docs/PHASE_6_5_SPEC.md`` §2.1 for the wire format.

Validation rules enforced here (Phase 6.5.a):
- ``block.id`` must be kebab/snake-case, 3-60 chars, ``[a-z0-9_]+``.
- ``block.version`` is a positive integer (immutable per-version on disk).
- Every variable carries a non-empty ``semantic_tag`` drawn from
  :mod:`presentations.variables.semantic_tags` (§3.2 of the spec).
- ``enum_single`` / ``enum_multi`` require ``allowed_values`` of the same
  type. ``enum_multi.default`` must be a subset of ``allowed_values``.
- ``date_range`` / ``number_range`` defaults carry the right accessors.

SQL-level checks (whitelist, bind/declared-variable consistency) live in
:mod:`presentations.sql.validator`; this module deliberately does not parse
SQL — schema validation runs *before* the SQL validator.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from presentations.variables.semantic_tags import SEMANTIC_TAGS_V0


# ── Primitive identifier types ─────────────────────────────────────────────

# block.id and variable.name: snake_case-ish. Spec §2.5.
_ID_RE = re.compile(r"^[a-z0-9_]+$")
_TEAM_RE = re.compile(r"^[a-z0-9_]+$")
_VAR_RE = re.compile(r"^[a-z0-9_]+$")
_TAG_RE = re.compile(r"^[a-z0-9_-]+$")

BlockId = Annotated[
    str,
    StringConstraints(min_length=3, max_length=60, pattern=_ID_RE.pattern),
]
TeamId = Annotated[
    str,
    StringConstraints(min_length=2, max_length=60, pattern=_TEAM_RE.pattern),
]
VariableName = Annotated[
    str,
    StringConstraints(min_length=3, max_length=40, pattern=_VAR_RE.pattern),
]


# ── Variable types ────────────────────────────────────────────────────────

VariableType = Literal["date", "date_range", "enum_single", "enum_multi", "number_range"]

# Treasury team uses these date expressions in block defaults. The resolver
# (presentations/variables/resolver.py) is the source of truth; the schema
# only checks shape, not semantics.
_REL_DATE_RE = re.compile(
    r"""
    ^\s*
    (?:
        today
        (?:\s*-\s*\d+[dwmy])?      # today, today - 30d, today - 2w, etc.
      | start_of_(?:month|year|quarter)
      | \d{4}-\d{2}-\d{2}          # ISO literal
    )
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _looks_like_relative_date(s: str) -> bool:
    return bool(_REL_DATE_RE.match(s))


class Variable(BaseModel):
    """A single declared variable on a block.

    ``semantic_tag`` is **mandatory** and bounded by ``SEMANTIC_TAGS_V0``;
    this is the forward-compat contract with Phase 7 (spec §1.2).
    """

    model_config = ConfigDict(extra="forbid")

    name: VariableName
    semantic_tag: str = Field(
        description="From SEMANTIC_TAGS_V0. Use 'other' as the escape hatch.",
    )
    type: VariableType
    required: bool = True
    default: Any | None = None
    # Only meaningful for enum_single / enum_multi.
    allowed_values: list[Any] | None = None

    @field_validator("semantic_tag")
    @classmethod
    def _check_semantic_tag(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("semantic_tag is required (spec §3.2)")
        if v not in SEMANTIC_TAGS_V0:
            raise ValueError(
                f"semantic_tag {v!r} not in allow-list. "
                f"Valid tags: {sorted(SEMANTIC_TAGS_V0)}. "
                "Use 'other' if nothing fits (UI will flag it)."
            )
        # Defensive: tag string must match the canonical shape.
        if not _TAG_RE.match(v):
            raise ValueError(f"semantic_tag {v!r} has invalid characters")
        return v

    @model_validator(mode="after")
    def _check_per_type(self) -> "Variable":
        t = self.type
        d = self.default
        allowed = self.allowed_values

        if t in ("enum_single", "enum_multi"):
            if not allowed or not isinstance(allowed, list):
                raise ValueError(
                    f"variable {self.name!r}: {t} requires a non-empty "
                    "allowed_values list"
                )
            if t == "enum_multi" and d is not None:
                if not isinstance(d, list):
                    raise ValueError(
                        f"variable {self.name!r}: enum_multi.default must be a list"
                    )
                bad = [x for x in d if x not in allowed]
                if bad:
                    raise ValueError(
                        f"variable {self.name!r}: default values {bad!r} are not "
                        f"in allowed_values {allowed!r}"
                    )
            if t == "enum_single" and d is not None and d not in allowed:
                raise ValueError(
                    f"variable {self.name!r}: enum_single.default {d!r} is not in "
                    f"allowed_values {allowed!r}"
                )
        else:
            if allowed is not None:
                raise ValueError(
                    f"variable {self.name!r}: allowed_values is only valid for "
                    "enum_single / enum_multi"
                )

        if t == "date" and d is not None:
            if not isinstance(d, str) or not _looks_like_relative_date(d):
                raise ValueError(
                    f"variable {self.name!r}: date.default {d!r} must be 'today', "
                    "'today - <N>d/w/m/y', 'start_of_month/year/quarter', "
                    "or an ISO date literal"
                )

        if t == "date_range" and d is not None:
            if not isinstance(d, dict) or set(d.keys()) - {"from", "to"} \
                    or "from" not in d or "to" not in d:
                raise ValueError(
                    f"variable {self.name!r}: date_range.default must be "
                    "{'from': <expr>, 'to': <expr>}"
                )
            for k in ("from", "to"):
                v = d[k]
                if not isinstance(v, str) or not _looks_like_relative_date(v):
                    raise ValueError(
                        f"variable {self.name!r}: date_range.default.{k} {v!r} "
                        "is not a valid relative-date expression"
                    )

        if t == "number_range" and d is not None:
            if not isinstance(d, dict) or set(d.keys()) - {"min", "max"} \
                    or "min" not in d or "max" not in d:
                raise ValueError(
                    f"variable {self.name!r}: number_range.default must be "
                    "{'min': <num>, 'max': <num>}"
                )
            for k in ("min", "max"):
                if not isinstance(d[k], (int, float)) or isinstance(d[k], bool):
                    raise ValueError(
                        f"variable {self.name!r}: number_range.default.{k} must be numeric"
                    )

        if self.required and self.default is None:
            # Required variables don't *need* a default (a dashboard binding
            # may supply one), but flag the missing-default case as a soft
            # constraint that the editor enforces. Schema-level: allow.
            pass

        return self


# ── Documentation ─────────────────────────────────────────────────────────

class BlockDocumentation(BaseModel):
    """Free-form documentation embedded in the block YAML."""

    model_config = ConfigDict(extra="forbid")

    purpose: str | None = None
    business_context: str | None = None
    decision_support: str | None = None
    known_limitations: str | None = None


# ── Visualization ─────────────────────────────────────────────────────────

VizType = Literal[
    "kpi", "kpi_grid", "line", "bar", "bar_chart", "line_chart", "table", "pie",
    # Phase 12.container — composite (container) blocks. Carry no SQL; their
    # `type` names the container kind. `canvas` is forward-compat for the
    # generic layout container (madde 2).
    "carousel", "canvas",
]


class Visualization(BaseModel):
    """Visualization spec: chart type + free-form config dict.

    Per-type field validation is deferred to the renderer; the editor surfaces
    a JSON-shaped form per type. Phase 6.5.a allows free-form config to avoid
    coupling block validation to chart-library changes.
    """

    model_config = ConfigDict(extra="forbid")

    type: VizType
    config: dict[str, Any] = Field(default_factory=dict)


# ── Block (root) ──────────────────────────────────────────────────────────

_DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DayCode = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class RefreshSchedule(BaseModel):
    """Phase B+ — Time-of-day scheduling for ``refresh_policy.kind=scheduled``.

    The scheduler fires at each ``HH:MM`` in ``times`` on every weekday in
    ``days`` (in the given ``timezone``). Cache age vs. the most-recent
    target time decides whether to enqueue a refetch — so a missed tick
    (app restart) is caught on the next poll.
    """

    model_config = ConfigDict(extra="forbid")

    times: list[str] = Field(min_length=1, max_length=24)
    days: list[DayCode] = Field(
        default_factory=lambda: list(_DAY_CODES),
        min_length=1, max_length=7,
    )
    timezone: str = "Europe/Istanbul"

    @field_validator("times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        """Normalise to ``HH:MM`` (zero-padded). Accept ``9:00`` or ``09:00``."""
        seen: set[str] = set()
        out: list[str] = []
        for t in v:
            s = (t or "").strip()
            if not s or ":" not in s:
                raise ValueError(f"times entry must be HH:MM, got {t!r}")
            hpart, _, mpart = s.partition(":")
            try:
                hh = int(hpart); mm = int(mpart)
            except ValueError:
                raise ValueError(f"times entry not numeric: {t!r}")
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError(f"times out of range: {t!r}")
            canonical = f"{hh:02d}:{mm:02d}"
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append(canonical)
        out.sort()
        return out

    @field_validator("days")
    @classmethod
    def _dedup_days(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for d in v:
            if d not in _DAY_CODES:
                raise ValueError(f"days entry not a valid weekday code: {d!r}")
            if d not in seen:
                seen.add(d); out.append(d)
        # Sort by weekday order, not alphabetically.
        return sorted(out, key=lambda d: _DAY_CODES.index(d))


class RefreshPolicy(BaseModel):
    """Phase B — Library block read-side cache policy.

    Default ``kind="on_open"`` preserves pre-Phase-B behaviour: every render
    re-runs the SQL (no shared cache). Setting ``kind="lazy_ttl"`` opts the
    block into the per-library-block shared cache with serve-stale semantics:

      * fresh hit  → returned immediately, no fetch
      * stale hit  → returned immediately (if ``serve_stale``) AND a background
                     refetch is enqueued; next view sees the new data
      * miss       → synchronous fetch, write to cache, return

    ``max_age_seconds`` is the hard ceiling for serve-stale: a hit older than
    this is treated as a miss (sync fetch) regardless of ``serve_stale``.

    For ``kind="scheduled"`` either ``interval_seconds`` (every N seconds) or
    ``schedule`` (time-of-day list per weekday) drives the warm-cache
    background refetch. The two are mutually exclusive at validation time.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["on_open", "lazy_ttl", "scheduled", "manual"] = "on_open"
    fresh_for_seconds: int = Field(default=600, ge=10, le=86_400)
    serve_stale: bool = True
    max_age_seconds: int | None = Field(default=86_400, ge=60, le=30 * 86_400)
    # Scheduled-only — exactly ONE of these may be set.
    interval_seconds: int | None = Field(default=None, ge=10, le=86_400)
    schedule: RefreshSchedule | None = None

    @model_validator(mode="after")
    def _check_schedule_xor(self) -> "RefreshPolicy":
        if self.kind == "scheduled":
            has_int = self.interval_seconds is not None
            has_sched = self.schedule is not None
            if has_int and has_sched:
                raise ValueError(
                    "refresh_policy.scheduled: pick one of "
                    "interval_seconds OR schedule, not both"
                )
            if not (has_int or has_sched):
                # Default to a daily 09:00 warm if user picked scheduled but
                # didn't fill the form — better than silently doing nothing.
                self.schedule = RefreshSchedule(times=["09:00"])
        return self


class Block(BaseModel):
    """The root block object.

    On disk: ``block: <Block fields>`` (single top-level ``block`` key).
    Use :func:`load_block_yaml` to read with that wrapper.
    """

    model_config = ConfigDict(extra="forbid")

    id: BlockId
    version: int = Field(ge=1, le=10_000)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    team: TeamId
    owner: str = Field(min_length=1, max_length=80)
    created_at: datetime
    updated_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    changelog: str | None = None

    documentation: BlockDocumentation | None = None

    # Phase 6.5 = single SQL block (default). Phase 12.container = composite
    # blocks (carousel/canvas): no SQL of their own — they hold child blocks
    # verbatim (manifest sub-block shape) so the library can store & re-insert
    # a whole container. `kind` discriminates; `_check_kind_shape` enforces it.
    kind: Literal["single", "composite"] = "single"
    query: str = ""
    children: list[dict[str, Any]] | None = None
    variables: list[Variable] = Field(default_factory=list)
    visualization: Visualization

    # Phase B — opt-in shared cache for library blocks; default keeps pre-B behaviour.
    refresh_policy: RefreshPolicy | None = None

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("tags must be a list of strings")
        out: list[str] = []
        for t in v:
            if not isinstance(t, str) or not t.strip():
                raise ValueError(f"tags contains non-string entry: {t!r}")
            out.append(t.strip())
        return out

    @model_validator(mode="after")
    def _check_unique_variable_names(self) -> "Block":
        names = [v.name for v in self.variables]
        if len(names) != len(set(names)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(
                f"variable names must be unique within a block; duplicates: {dupes}"
            )
        return self

    @model_validator(mode="after")
    def _check_kind_shape(self) -> "Block":
        """single → needs a non-empty query, no children.
        composite → needs children, a container viz type, no SQL."""
        if self.kind == "composite":
            if not self.children:
                raise ValueError("composite block must carry a non-empty 'children' list")
            for i, c in enumerate(self.children):
                if not isinstance(c, dict) or not isinstance(c.get("type"), str) or not c.get("type"):
                    raise ValueError(f"children[{i}] must be a dict with a non-empty string 'type'")
            if self.visualization.type not in ("carousel", "canvas"):
                raise ValueError(
                    "composite block visualization.type must be 'carousel' or 'canvas'"
                )
        else:
            if not (self.query or "").strip():
                raise ValueError("single block requires a non-empty 'query'")
            if self.children:
                raise ValueError("single block must not carry 'children' (use kind='composite')")
        return self


# ── YAML wrappers ─────────────────────────────────────────────────────────

class BlockDocument(BaseModel):
    """The on-disk YAML root: ``{block: {...}}``."""

    model_config = ConfigDict(extra="forbid")

    block: Block


def load_block_from_dict(raw: dict[str, Any]) -> Block:
    """Parse a block YAML/JSON dict (with the ``block:`` wrapper) into a Block.

    Raises ``pydantic.ValidationError`` with a list of field paths on failure.
    """
    doc = BlockDocument.model_validate(raw)
    return doc.block


def block_to_dict(block: Block) -> dict[str, Any]:
    """Serialize a Block back to the ``{block: {...}}`` YAML shape."""
    return BlockDocument(block=block).model_dump(mode="json", exclude_none=True)

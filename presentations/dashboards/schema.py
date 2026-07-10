"""Pydantic models for Phase 6.5.c dashboard-level filters.

YAML / manifest shape (per spec §2.2):

    dashboard:
      id: branch_morning_review
      version: 3
      filters:
        - id: f_period
          semantic_tag: as_of_time
          type: date_range
          label: "Tarih Aralığı"
          default:
            from: "today - 30d"
            to: "today"
        - id: f_currency
          semantic_tag: currency
          type: enum_multi
          label: "Para Birimi"
          allowed_values: [TRY, USD, EUR]
          default: [TRY, USD, EUR]

      layout:
        sections:
          - blocks:
              - block_ref: {team, id, version}
                variable_bindings:
                  as_of_from:
                    from_filter: f_period
                    accessor: from
                  currency_list:
                    from_filter: f_currency
                  as_of_from:                # block-level constant override
                    constant: "today - 7d"

The shape is identical to the block ``Variable`` schema with one twist:
``DashboardFilter.type`` excludes ``date`` (use ``date_range``) because at
the dashboard level the user controls a *range*, then bindings derive both
ends via ``accessor: from / to``.
"""
from __future__ import annotations

import re
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


# ── Identifier types ──────────────────────────────────────────────────────

_FILTER_ID_RE = re.compile(r"^[a-z0-9_]+$")

FilterId = Annotated[
    str,
    StringConstraints(min_length=3, max_length=40, pattern=_FILTER_ID_RE.pattern),
]


# ── Filter types (subset of block variable types) ─────────────────────────

# date is intentionally absent: dashboards expose a range, blocks derive both
# ends via the variable_bindings accessor mechanism. See spec §3.5 / §5.4.
DashboardFilterType = Literal[
    "date_range",
    "enum_single",
    "enum_multi",
    "number_range",
]


# ── DashboardFilter ──────────────────────────────────────────────────────

class DashboardFilter(BaseModel):
    """A single dashboard-level filter.

    Renders into a widget per ``type`` (date_range picker, multi-select,
    dropdown, number slider). Default values resolve to the same Python
    representations as block variables — ISO date strings or
    {from, to} dicts for date_range, sorted lists for enum_multi, etc.
    """

    model_config = ConfigDict(extra="forbid")

    id: FilterId
    semantic_tag: str = Field(description="From SEMANTIC_TAGS_V0.")
    type: DashboardFilterType
    label: str = Field(min_length=1, max_length=80)
    default: Any | None = None
    allowed_values: list[Any] | None = None
    # UI hint (Sunum fixed date widget): present a date_range as a single date
    # (the widget shows one date and stores from == to). The stored value stays
    # a {from, to} dict, so the backend predicate is unchanged — this only flips
    # the picker between single-day and range modes.
    single: bool = False
    # Sayfa kapsamı (ops.): manifest.pages[].id — filtre bar'ı bu filtreyi
    # yalnız o sayfa aktifken gösterir. None = her sayfada (global filtre).
    page: str | None = None

    @field_validator("semantic_tag")
    @classmethod
    def _check_tag(cls, v: str) -> str:
        if v not in SEMANTIC_TAGS_V0:
            raise ValueError(
                f"semantic_tag {v!r} not in SEMANTIC_TAGS_V0. Add via PR."
            )
        return v

    @model_validator(mode="after")
    def _check_per_type(self) -> "DashboardFilter":
        t = self.type
        d = self.default
        allowed = self.allowed_values

        if t in ("enum_single", "enum_multi"):
            if not allowed:
                raise ValueError(
                    f"filter {self.id!r}: {t} requires non-empty allowed_values"
                )
            if t == "enum_multi" and d is not None:
                if not isinstance(d, list):
                    raise ValueError(
                        f"filter {self.id!r}: enum_multi.default must be a list"
                    )
                bad = [x for x in d if x not in allowed]
                if bad:
                    raise ValueError(
                        f"filter {self.id!r}: default {bad!r} not in allowed_values"
                    )
            if t == "enum_single" and d is not None and d not in allowed:
                raise ValueError(
                    f"filter {self.id!r}: enum_single.default {d!r} not in allowed_values"
                )
        else:
            if allowed is not None:
                raise ValueError(
                    f"filter {self.id!r}: allowed_values is only valid for enum types"
                )

        if t == "date_range" and d is not None:
            if not isinstance(d, dict) or "from" not in d or "to" not in d:
                raise ValueError(
                    f"filter {self.id!r}: date_range.default must be {{from, to}}"
                )

        if t == "number_range" and d is not None:
            if not isinstance(d, dict) or "min" not in d or "max" not in d:
                raise ValueError(
                    f"filter {self.id!r}: number_range.default must be {{min, max}}"
                )

        if self.single and t != "date_range":
            raise ValueError(
                f"filter {self.id!r}: single=True is only valid for date_range"
            )

        return self


# ── VariableBinding ──────────────────────────────────────────────────────

class VariableBinding(BaseModel):
    """A per-block-variable binding declaration.

    Exactly one of:
    - ``from_filter`` (+ optional ``accessor``): take the value from the named
      dashboard filter at run time.
    - ``from_scope_filter`` (+ optional ``accessor``): Phase 8 — take the value
      from a scope-contract filter. If that filter is *pinned*, the value is
      immutable and ignores any dashboard widget state; if *interactive*, it
      behaves like ``from_filter`` against the surfaced widget. The id refers
      to a scope ``pf_*`` / ``if_*`` filter.
    - ``constant``: hard-code an expression (passed to ``parse_date_expr`` for
      dates, used as-is otherwise). Spec §5.4 — used for "this block always
      shows last 7d regardless of filter" patterns.
    """

    model_config = ConfigDict(extra="forbid")

    from_filter: FilterId | None = None
    from_scope_filter: str | None = None
    accessor: Literal["from", "to", "min", "max"] | None = None
    constant: Any | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "VariableBinding":
        sources = sum([
            self.from_filter is not None,
            self.from_scope_filter is not None,
            self.constant is not None,
        ])
        if sources != 1:
            raise ValueError(
                "VariableBinding must set exactly one of from_filter, "
                "from_scope_filter or constant"
            )
        if self.accessor is not None and (
            self.from_filter is None and self.from_scope_filter is None
        ):
            raise ValueError(
                "accessor only makes sense with from_filter / from_scope_filter"
            )
        return self

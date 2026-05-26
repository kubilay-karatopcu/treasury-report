"""Pydantic models for Phase 6.5.b extended table documentation.

YAML shape (per spec §2.3):

    table: TRD_BRANCH_POSITION
    schema: ODS_TREASURY
    description: "Şube bazlı günlük net pozisyon snapshot'ı."
    partition_column: AS_OF_DATE
    estimated_daily_rows: 12000
    columns:
      AS_OF_DATE:
        type: DATE
        description: "Snapshot tarihi"
        filterable: true
        filter_role: time_axis
        suggested_variable: as_of_date
        suggested_semantic_tag: as_of_time
      BRANCH_ID:
        type: VARCHAR2(8)
        description: "Şube kodu"
        filterable: true
        filter_role: dimension
        suggested_variable: branch_id
        suggested_semantic_tag: branch
        lookup:
          table: DIM_BRANCH
          key: BRANCH_ID
          display: BRANCH_NAME
      ...
      CREATED_AT:
        type: TIMESTAMP
        description: "Kayıt yaratma zamanı"
        filterable: false
        visible_in_ui: false

Validation rules:
- ``table`` and ``schema`` must be ALL_CAPS_WITH_UNDERSCORES (Oracle identifier
  shape; the loader rejects free-form table names so the on-disk key path
  ``table_docs/<schema>/<table>.yaml`` stays predictable).
- ``filter_role`` is one of the spec's allow-list (``time_axis``, ``dimension``,
  ``measure_threshold``).
- ``suggested_semantic_tag`` must be in the Phase 6.5 allow-list
  (mirrors ``presentations/variables/semantic_tags.py``).
- ``distinct_values_sampled_at`` is required IF ``distinct_values_sample`` is
  set; the cron job writes both together.

The forward-compat target (Phase 7): ``suggested_semantic_tag`` becomes the
inference seed for the concept binding compiler. See ROADMAP §1.
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


# ── Identifier types ──────────────────────────────────────────────────────

_ORACLE_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")
_COL_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")

OracleIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=_ORACLE_IDENT_RE.pattern),
]
ColumnName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=_COL_NAME_RE.pattern),
]


# ── Per-column metadata ──────────────────────────────────────────────────

FilterRole = Literal["time_axis", "dimension", "measure_threshold"]


class LookupRef(BaseModel):
    """Foreign-key style reference to a dimension table.

    Used by the LLM ("if you need the human name, JOIN this table") and by
    Phase 7's binding inference (concept resolution for non-primary identifiers).
    """
    model_config = ConfigDict(extra="forbid")

    table: OracleIdentifier
    key: ColumnName
    display: ColumnName


class ColumnDoc(BaseModel):
    """A single column's metadata."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=64)
    description: str | None = None

    # Phase 6.5.b additions — filter affordances.
    filterable: bool = False
    filter_role: FilterRole | None = None
    suggested_variable: str | None = Field(default=None, max_length=40)
    suggested_semantic_tag: str | None = None

    # Optional dimensional lookup (FK reference).
    lookup: LookupRef | None = None

    # Distinct values sample — populated nightly by the cron job (§8.b).
    # When the column is high-cardinality, the cron may store a representative
    # subset (e.g., 50 most-frequent values) rather than every distinct value.
    distinct_values_sample: list[Any] | None = None
    distinct_values_sampled_at: datetime | None = None

    # Aggregation hint — Phase 7 uses this to recommend SUM/AVG columns.
    aggregatable: bool = False

    # UI / LLM gating — internal-audit columns hide from both pickers.
    visible_in_ui: bool = True

    @field_validator("suggested_semantic_tag")
    @classmethod
    def _check_tag(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in SEMANTIC_TAGS_V0:
            raise ValueError(
                f"suggested_semantic_tag {v!r} not in SEMANTIC_TAGS_V0. "
                "Use 'other' as the escape hatch or update the allow-list "
                "via PR."
            )
        return v

    @model_validator(mode="after")
    def _check_filter_consistency(self) -> "ColumnDoc":
        # filter_role only makes sense when filterable=true.
        if self.filter_role is not None and not self.filterable:
            raise ValueError(
                "filter_role can only be set when filterable=true"
            )
        # suggested_variable/tag pair: if you suggest one, suggest both so the
        # LLM has a complete picture. The data team migration must fill both.
        if self.suggested_variable is None and self.suggested_semantic_tag is not None:
            raise ValueError(
                "suggested_semantic_tag set but suggested_variable is missing"
            )
        # distinct_values_sample requires a timestamp; the cron job writes both.
        if self.distinct_values_sample is not None and self.distinct_values_sampled_at is None:
            raise ValueError(
                "distinct_values_sample requires distinct_values_sampled_at"
            )
        return self


# ── Table-level metadata ──────────────────────────────────────────────────

class TableDoc(BaseModel):
    """Extended documentation for a single Oracle table."""

    model_config = ConfigDict(extra="forbid")

    table: OracleIdentifier
    schema_name: OracleIdentifier = Field(alias="schema")
    description: str | None = None

    # Hints for the query planner / LLM.
    partition_column: ColumnName | None = None
    estimated_daily_rows: int | None = Field(default=None, ge=0)

    # Columns keyed by name (preserves order via Python 3.7+ dict).
    columns: dict[ColumnName, ColumnDoc] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_partition_column_exists(self) -> "TableDoc":
        if self.partition_column is not None:
            if self.partition_column not in self.columns:
                raise ValueError(
                    f"partition_column {self.partition_column!r} is not "
                    f"declared in columns: {sorted(self.columns)}"
                )
        return self

    # ── Convenience accessors used by the LLM context builder ────────────

    def filterable_columns(self) -> dict[str, ColumnDoc]:
        return {n: c for n, c in self.columns.items() if c.filterable}

    def time_axis_column(self) -> tuple[str, ColumnDoc] | None:
        for name, col in self.columns.items():
            if col.filterable and col.filter_role == "time_axis":
                return name, col
        return None

    def visible_columns(self) -> dict[str, ColumnDoc]:
        return {n: c for n, c in self.columns.items() if c.visible_in_ui}

    def to_yaml_shape(self) -> dict[str, Any]:
        """Serialise back to the on-disk YAML shape (schema field as 'schema')."""
        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


def load_table_doc_from_dict(raw: dict[str, Any]) -> TableDoc:
    """Parse a raw YAML/JSON dict into a TableDoc. Use this instead of
    ``TableDoc.model_validate(raw)`` so the alias remap (``schema`` →
    ``schema_name``) happens uniformly."""
    return TableDoc.model_validate(raw)

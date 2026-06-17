"""Pydantic models for the Phase 8 scope contract (spec §2.1, §2.4).

A scope contract is stored at
``s3://<bucket>/presentations/<user>/<id>/scope_v<N>.yaml`` with a single
top-level ``scope:`` key whose value matches :class:`ScopeContract`. See
``examples/phase_8/sample_scope.yaml`` for the canonical fixture.

This module is pure: it parses / validates / serialises the contract shape and
the identifier rules from §2.4. The *semantic* validators (concept existence,
projection-column existence, join consistency, …) live in
:mod:`presentations.scope.validators` because they need catalog metadata; the
routing decision lives in :mod:`presentations.scope.routing`.

Field defaults (§2.1, "All scope contract field defaults documented"):

- ``parent_version``        → ``None`` (first version of a presentation).
- ``projection.include_all``→ ``False`` (explicit column list is honoured).
- ``routing.decided_by``    → ``"system"`` (the algorithm chose it).
- ``routing.threshold_bytes``→ ``None`` (decision recorded without the cap that
  produced it — only meaningful for audit; the live cap comes from config).
- ``filters.pinned`` / ``filters.interactive`` → ``[]``.
- ``<filter>.applies_to``   → ``[]`` meaning "all basket tables that bind the
  concept" (§2.1; the validator interprets the empty list, not the schema).
- ``status.state``          → ``"drafting"`` (no fetch has run yet).
- ``status.cached_tables`` / ``lazy_tables`` / ``errors`` → ``[]``.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from presentations.scope._yaml import dump_yaml, load_yaml


# ── Identifier types (§2.4) ────────────────────────────────────────────────

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")          # snake_case, 3–40 chars
_PF_ID_RE = re.compile(r"^pf_[a-z0-9_-]+$")
_IF_ID_RE = re.compile(r"^if_[a-z0-9_-]+$")
_JOIN_ID_RE = re.compile(r"^j_[a-z0-9_-]+$")
_ORACLE_IDENT_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")

Alias = Annotated[
    str,
    StringConstraints(min_length=3, max_length=40, pattern=_ALIAS_RE.pattern),
]
PinnedFilterId = Annotated[
    str, StringConstraints(min_length=4, max_length=60, pattern=_PF_ID_RE.pattern)
]
InteractiveFilterId = Annotated[
    str, StringConstraints(min_length=4, max_length=60, pattern=_IF_ID_RE.pattern)
]
JoinId = Annotated[
    str, StringConstraints(min_length=3, max_length=60, pattern=_JOIN_ID_RE.pattern)
]
RawFilterId = Annotated[
    str, StringConstraints(min_length=4, max_length=60, pattern=r"^rf_[a-z0-9_-]+$")
]
OracleIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=_ORACLE_IDENT_RE.pattern),
]


# ── Basket ──────────────────────────────────────────────────────────────────

class TableRef(BaseModel):
    """A fully-qualified Oracle table reference."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # `schema` shadows BaseModel.schema(); store as schema_name, alias on disk.
    schema_name: OracleIdentifier = Field(alias="schema")
    name: OracleIdentifier


class JoinedColumn(BaseModel):
    """A column pulled from a related alias via a confirmed join (§6R.5)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    via_join: JoinId
    column: str = Field(min_length=1, max_length=128)
    as_: str | None = Field(default=None, alias="as", max_length=128)


class DerivedColumn(BaseModel):
    """A calculated column. Authoring is deferred (§6R.7); schema-only for now."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    expr: str = Field(min_length=1)


# Kolon adları SQL'e identifier olarak interpolate edilir (compose_cached_sql
# projection'ı, _raw_predicates_from WHERE'i, compile_aggregate/join SELECT'i).
# Değerler her zaman bind'lenir ama identifier bind'lenemez — bu yüzden kolon
# ADI şeması injection'a kapalı olmalı: harf/altçizgi ile başlar, harf/rakam/
# _/$/# devam eder (Oracle + DuckDB kimlik kuralı; büyük-küçük serbest).
_COLUMN_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


def _check_column_ident(value: str, *, what: str) -> str:
    v = (value or "").strip()
    if not _COLUMN_IDENT_RE.match(v):
        raise ValueError(
            f"{what} geçerli bir kolon adı değil: {value!r} "
            "(harf/altçizgi ile başlamalı; harf, rakam, _, $, # içerebilir)"
        )
    return v


class Projection(BaseModel):
    """Which columns of the table are pulled into scope."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(default_factory=list)
    include_all: bool = False
    joined: list[JoinedColumn] = Field(default_factory=list)
    derived: list[DerivedColumn] = Field(default_factory=list)

    @field_validator("columns")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in v:
            if not isinstance(c, str) or not c.strip():
                raise ValueError(f"projection column must be a non-empty string: {c!r}")
            out.append(_check_column_ident(c, what="projection column"))
        return out


class Routing(BaseModel):
    """Per-table cached/lazy decision, recorded at scope-build time."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["cached", "lazy"]
    decided_by: Literal["system", "user"] = "system"
    # Sign / floor sanity is a *validator* concern (§2.2 rule 7), not a schema
    # one — the contract must be parseable even when these are misconfigured so
    # the validator can surface a precise message instead of a Pydantic error.
    estimated_bytes: int
    # The cap that was applied at decision time (audit only — the live cap is a
    # config value). Optional so a contract can be recorded without it.
    threshold_bytes: int | None = None
    # Which estimator produced `estimated_bytes` (madde 4): "explain_plan" when
    # refined via Oracle cardinality, else the catalog/partition estimate. UI
    # hint only — the frontend round-trips it on the scope, so the schema must
    # accept it (extra="forbid" otherwise rejects the whole POST).
    estimate_source: str | None = None


class NodePosition(BaseModel):
    """React Flow node position on the ER canvas (UI persistence, §6R.7)."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


AggFn = Literal["sum", "avg", "count", "count_distinct", "min", "max"]


class Measure(BaseModel):
    """An aggregation in a derived table: ``fn(column) AS as``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    column: str = Field(min_length=1, max_length=128)
    fn: AggFn
    as_: str = Field(alias="as", min_length=1, max_length=128)

    @field_validator("column", "as_")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_column_ident(v, what="measure column")


class CalculatedColumn(BaseModel):
    """One computed output column for a ``kind: calculated`` derivation.

    ``expr`` is a DuckDB SQL expression referencing input columns. When the
    calculated derivation joins multiple source aliases, ambiguous column
    names must be qualified by alias (e.g. ``deposits_daily.BALANCE_TRY``).
    The compiler does NOT rewrite the expression — it's emitted verbatim in
    the SELECT list, so column names must already match the materialised
    DuckDB views.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    expr: str = Field(min_length=1, max_length=2000)
    type_hint: str | None = Field(default=None, max_length=64)

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_column_ident(v, what="calculated column name")

    @field_validator("expr")
    @classmethod
    def _expr_guard(cls, v: str) -> str:
        # expr DuckDB SELECT listesine verbatim girer (external access kapalı
        # bağlantıda). İfade bağlamında zaten statement koşamaz; yine de en
        # bariz kaçış vektörlerini şemada kes (derinlemesine savunma).
        if ";" in v:
            raise ValueError("calculated expr ';' içeremez")
        if re.search(r"\b(ATTACH|INSTALL|LOAD|COPY|PRAGMA|EXPORT|IMPORT|CALL)\b",
                     v, re.IGNORECASE):
            raise ValueError("calculated expr yasaklı bir DuckDB komutu içeriyor")
        return v


class CalculatedJoinKey(BaseModel):
    """Inner-join across the source aliases of a calculated derivation.

    The compiler chains ``INNER JOIN right_alias ON …`` clauses in order;
    the first source_alias is the FROM root."""

    model_config = ConfigDict(extra="forbid")

    left_alias: Alias
    left_column: str = Field(min_length=1, max_length=128)
    right_alias: Alias
    right_column: str = Field(min_length=1, max_length=128)

    @field_validator("left_column", "right_column")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_column_ident(v, what="join key column")


class Derivation(BaseModel):
    """A derived table generated from one or more basket aliases.

    Two kinds (spec §6R aggregate + Polish-5 calculated):

    - ``aggregate`` — single ``source_alias``, ``GROUP BY group_by`` + the
      ``measures`` (fn(column) AS as) emit a coarser table. Pivot UI / LLM
      both produce this definition; the compiler is
      :func:`presentations.scope.fetch.compile_aggregate_sql`.

    - ``calculated`` — multiple ``source_aliases`` joined via ``join_keys``,
      then a SELECT of arbitrary expressions (``columns``). Use cases:
      "deposits.INTEREST_RATE - competitor.RATE", "ratio of two measures",
      "concatenated label". The compiler is
      :func:`presentations.scope.fetch.compile_calculated_sql`. NO group-by
      step — for that, build a separate aggregate downstream of the
      calculated alias.

    The two field sets are mutually exclusive (validator enforces it). The
    front-end never produces SQL directly — it produces this definition and
    the server-side compiler emits the SQL.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["aggregate", "calculated", "filter", "join", "union", "python"] = "aggregate"
    # aggregate-only ----------------------------------------------------
    source_alias: Alias | None = None
    group_by: list[str] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)

    @field_validator("group_by")
    @classmethod
    def _group_by_idents(cls, v: list[str]) -> list[str]:
        return [_check_column_ident(c, what="group_by column") for c in v]
    # calculated-only ---------------------------------------------------
    source_aliases: list[Alias] = Field(default_factory=list)
    join_keys: list[CalculatedJoinKey] = Field(default_factory=list)
    columns: list[CalculatedColumn] = Field(default_factory=list)
    # join-only (Hazırlık ER): tam 2 source_aliases + 1 join_key. Çıktı = iki
    # tablonun tüm kolonları (çakışanlar sağ alias prefix'iyle). DuckDB'de
    # hesaplanır → compile_join_sql.
    join_type: Literal["inner", "left"] = "inner"
    # union-only: 2+ source_aliases, kolon sayısı + tipleri uyumlu olmalı
    # (frontend ön-kontrol + DuckDB execute doğrular). union_all=False → DISTINCT.
    union_all: bool = True
    # filter-only (Faz R1) — bir main node'dan filtreyle türetilen, cache'lenip
    # cron'lanabilen alt-node. `source_alias` lineage (edge bundan çizilir);
    # `filters` GÖMÜLÜ (scope-level filters değil). Compiler kaynağın table_ref'i
    # + bu filtrelerle Oracle `SELECT … WHERE …` üretir; relative tarihler her
    # materialize'da yeniden çözülür → dinamik dataset. (Forward-ref: Filters
    # aşağıda tanımlı → modül sonunda model_rebuild.)
    filters: "Filters | None" = None
    # python-only (Faz P) — TEK girişli prosedürel dönüşüm. `source_alias` çalışma
    # anında `input_node_df` (pandas DataFrame) olarak bağlanır; script sonunda
    # `output_node_df` (DataFrame) beklenir. SQL ile ifade edilemeyen çok-adımlı /
    # pandas dönüşümler için. İzolasyon YAPISALDIR: yalnız tek source df enjekte
    # edilir, başka hiçbir node'a erişilemez (cross-bulaşı imkânsız). Yürütme
    # AST-whitelist + subprocess sandbox'ta yapılır (presentations.python_runtime).
    python_code: str | None = Field(default=None, max_length=20_000)
    # Run/preview sırasında tespit edilen çıktı kolon adları. UI hint (COLS_BY_ALIAS);
    # downstream node'lar bu kolonlara göre kurulur. Build/cron çalıştığında script
    # gerçeği üretir — bu liste yalnızca editör/canvas için bir gölgedir.
    output_columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _kind_shape(self) -> "Derivation":
        if self.kind == "aggregate":
            if self.source_alias is None:
                raise ValueError("aggregate derivation: source_alias zorunlu")
            if self.source_aliases or self.join_keys or self.columns or self.filters \
                    or self.python_code:
                raise ValueError(
                    "aggregate derivation: source_aliases / join_keys / columns / "
                    "filters / python_code alanları yalnız calculated/filter/python "
                    "kind'leri için kullanılır"
                )
            if not self.group_by and not self.measures:
                raise ValueError(
                    "aggregate derivation: group_by ya da measures'tan en az biri olmalı"
                )
        elif self.kind == "filter":
            # Faz R1 — main node'dan filtreyle türetilen alt-node.
            if self.source_alias is None:
                raise ValueError("filter derivation: source_alias zorunlu")
            if self.source_aliases or self.join_keys or self.columns \
                    or self.group_by or self.measures or self.python_code:
                raise ValueError(
                    "filter derivation: yalnız source_alias + filters kullanılır"
                )
            if self.filters is None or not (self.filters.pinned or self.filters.raw):
                raise ValueError(
                    "filter derivation: en az bir pinned ya da raw filtre gerekli"
                )
        elif self.kind == "calculated":
            if not self.source_aliases:
                raise ValueError("calculated derivation: en az bir source_aliases gerekli")
            if not self.columns:
                raise ValueError("calculated derivation: en az bir output column gerekli")
            if self.source_alias is not None or self.group_by or self.measures \
                    or self.filters or self.python_code:
                raise ValueError(
                    "calculated derivation: source_alias / group_by / measures / "
                    "filters / python_code alanları yalnız aggregate/filter/python "
                    "kind'leri için kullanılır"
                )
            # Multi-source requires explicit join_keys; single-source needs none.
            if len(self.source_aliases) > 1 and not self.join_keys:
                raise ValueError(
                    "calculated derivation: çoklu source_aliases için join_keys gerekli"
                )
            # Every join_key's aliases must be in source_aliases.
            srcset = set(self.source_aliases)
            for jk in self.join_keys:
                if jk.left_alias not in srcset:
                    raise ValueError(
                        f"calculated join_key: '{jk.left_alias}' source_aliases'ta yok"
                    )
                if jk.right_alias not in srcset:
                    raise ValueError(
                        f"calculated join_key: '{jk.right_alias}' source_aliases'ta yok"
                    )
            # Unique output column names.
            seen: set[str] = set()
            for c in self.columns:
                if c.name in seen:
                    raise ValueError(f"calculated columns: '{c.name}' iki kez tanımlı")
                seen.add(c.name)
        elif self.kind == "join":
            # Hazırlık ER: iki tabloyu bir join_key üstünden birleştir.
            if len(self.source_aliases) != 2:
                raise ValueError("join derivation: tam olarak 2 source_aliases gerekli")
            if not self.join_keys:
                raise ValueError("join derivation: bir join_key gerekli")
            if self.source_alias is not None or self.group_by or self.measures \
                    or self.columns or self.filters or self.python_code:
                raise ValueError(
                    "join derivation: yalnız source_aliases + join_keys + join_type kullanılır"
                )
            srcset = set(self.source_aliases)
            for jk in self.join_keys:
                if jk.left_alias not in srcset or jk.right_alias not in srcset:
                    raise ValueError(
                        "join join_key: alias source_aliases içinde değil"
                    )
        elif self.kind == "union":
            if len(self.source_aliases) < 2:
                raise ValueError("union derivation: en az 2 source_aliases gerekli")
            if self.source_alias is not None or self.group_by or self.measures \
                    or self.columns or self.join_keys or self.filters or self.python_code:
                raise ValueError(
                    "union derivation: yalnız source_aliases (+ union_all) kullanılır"
                )
        elif self.kind == "python":
            # Faz P — main ya da herhangi bir türetilmiş node'dan TEK girişli
            # prosedürel dönüşüm. Yapısal izolasyon: tek source_alias.
            if self.source_alias is None:
                raise ValueError("python derivation: source_alias zorunlu (tek giriş)")
            if not (self.python_code and self.python_code.strip()):
                raise ValueError("python derivation: python_code zorunlu")
            if self.source_aliases or self.join_keys or self.columns \
                    or self.group_by or self.measures or self.filters:
                raise ValueError(
                    "python derivation: yalnız source_alias + python_code kullanılır"
                )
        return self


# ── Dataset refresh policy (Faz A — Hazırlık'a taşınan cron) ────────────────

class RefreshSchedule(BaseModel):
    """Time-of-day schedule for a scheduled dataset refresh.

    Fires at each ``HH:MM`` in ``times`` on every weekday in ``days`` (MON..SUN;
    empty = every day), interpreted in ``timezone``.
    """

    model_config = ConfigDict(extra="forbid")

    times: list[str] = Field(min_length=1)
    days: list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]] = Field(
        default_factory=list
    )
    timezone: str = "Europe/Istanbul"

    @field_validator("times")
    @classmethod
    def _check_times(cls, v: list[str]) -> list[str]:
        for t in v:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t or ""):
                raise ValueError(f"schedule time must be HH:MM (24h): {t!r}")
        return v


class DatasetRefresh(BaseModel):
    """Cron refresh policy for a CACHED dataset (Faz A).

    Only valid on a basket item whose ``routing.decision == 'cached'`` — a lazy
    (too-big-to-materialise) table can't be cron-refreshed; the user must shrink
    it (aggregate / filter) so it routes cached. Exactly one of
    ``interval_seconds`` / ``schedule`` drives the dataset scheduler when
    ``kind == 'scheduled'``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual", "scheduled"] = "manual"
    interval_seconds: int | None = Field(default=None, ge=60, le=86_400)
    schedule: RefreshSchedule | None = None

    @model_validator(mode="after")
    def _shape(self) -> "DatasetRefresh":
        if self.kind == "scheduled":
            has_int = self.interval_seconds is not None
            has_sched = self.schedule is not None
            if has_int and has_sched:
                raise ValueError(
                    "scheduled refresh: pick one of interval_seconds OR schedule"
                )
            if not (has_int or has_sched):
                raise ValueError(
                    "scheduled refresh: set interval_seconds OR schedule"
                )
        elif self.interval_seconds is not None or self.schedule is not None:
            raise ValueError(
                "interval_seconds / schedule only apply when kind='scheduled'"
            )
        return self


class BasketItem(BaseModel):
    """One dataset in the scope basket — exactly one source of:

    - ``table_ref``  — a real Oracle table (projected + filtered),
    - ``derivation`` — an aggregate/calculated table computed in DuckDB,
    - ``sql``        — a free-form Oracle ``SELECT``/``WITH`` (Faz C): the user
      (or LLM) authors arbitrary query SQL in Hazırlık; the cron materialises
      its result to parquet exactly like a cached table. Used for the big
      bespoke queries (UNIONs, multi-step aggregates) that don't fit the
      projection/derivation builders.
    """

    model_config = ConfigDict(extra="forbid")

    table_ref: TableRef | None = None
    derivation: Derivation | None = None
    sql: str | None = Field(default=None, max_length=20_000)
    alias: Alias
    projection: Projection = Field(default_factory=Projection)
    routing: Routing
    layout: NodePosition | None = None
    # Faz A: cron refresh for cached datasets. None = no scheduled refresh
    # (materialised once at scope build). Only meaningful when cached.
    refresh: DatasetRefresh | None = None
    # Faz C: where an LLM-authored sql dataset came from (kaynakça/provenance).
    provenance: str | None = Field(default=None, max_length=4000)
    # Faz R4/#1: lineage for a `sql` node produced by "Çözümle" — the basket
    # aliases of the source tables the query reads. Pure UI hint: the edge from
    # each source main node → this node is drawn from it. (derivation nodes carry
    # their own source_alias(es); this is for free-form sql nodes.)
    derived_from: list[Alias] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "BasketItem":
        n = sum(s is not None for s in (self.table_ref, self.derivation, self.sql))
        if n != 1:
            raise ValueError(
                f"basket item {self.alias!r}: set exactly one of "
                "table_ref, derivation, or sql"
            )
        return self

    @model_validator(mode="after")
    def _refresh_requires_cached(self) -> "BasketItem":
        if (
            self.refresh is not None
            and self.refresh.kind == "scheduled"
            and self.routing.decision != "cached"
        ):
            raise ValueError(
                f"basket item {self.alias!r}: scheduled refresh requires "
                "routing.decision='cached' (lazy tables can't be cron-refreshed "
                "— shrink the table so it caches)"
            )
        return self


# ── Joins ─────────────────────────────────────────────────────────────────

class JoinSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: Alias
    column: str = Field(min_length=1, max_length=128)


class Join(BaseModel):
    """A confirmed join between two basket aliases."""

    model_config = ConfigDict(extra="forbid")

    id: JoinId
    kind: Literal["lookup", "inner", "left"]
    left: JoinSide
    right: JoinSide
    confirmed_at: datetime | None = None


# ── Filters ─────────────────────────────────────────────────────────────────

FilterOp = Literal["between", "in", "not_in", "eq", "last_n_days", "gt", "gte", "lt", "lte"]


def _iso(v: Any) -> Any:
    """Normalise a date/datetime to an ISO string; pass everything else
    through. Filter values are JSON-native (strings/numbers) after this, so
    a contract round-trips through YAML/JSON without date↔str drift, and ISO
    date strings compare correctly for the ``between`` rule."""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _iso_list(v: Any) -> Any:
    if isinstance(v, list):
        return [_iso(x) for x in v]
    return v


class PinnedFilter(BaseModel):
    """A filter locked at scope time. Immutable in Sunum (§2.1, §4.1).

    Op-dependent value carriers:
      - ``between``            → ``from`` / ``to``
      - ``in`` / ``not_in``    → ``values``
      - ``eq`` / ``last_n_days``→ ``value``
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: PinnedFilterId
    concept: str = Field(min_length=1)
    op: FilterOp
    # `from` is a Python keyword; store as from_ with the on-disk alias.
    from_: Any | None = Field(default=None, alias="from")
    to: Any | None = None
    values: list[Any] | None = None
    value: Any | None = None
    applies_to: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise_dates(self) -> "PinnedFilter":
        self.from_ = _iso(self.from_)
        self.to = _iso(self.to)
        self.value = _iso(self.value)
        self.values = _iso_list(self.values)
        return self


class InteractiveFilter(BaseModel):
    """A filter exposed as a dashboard widget in Sunum (§2.1)."""

    model_config = ConfigDict(extra="forbid")

    id: InteractiveFilterId
    concept: str = Field(min_length=1)
    op: FilterOp
    default_values: list[Any] | None = None
    allowed_values: list[Any] | None = None
    label: str | None = None
    applies_to: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalise_dates(self) -> "InteractiveFilter":
        self.default_values = _iso_list(self.default_values)
        self.allowed_values = _iso_list(self.allowed_values)
        return self


class RawFilter(BaseModel):
    """A non-concept, column-level filter (§6R.4). Applied as a fetch-time
    WHERE to shrink the cached table; never exported as a concept filter."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: RawFilterId
    alias: Alias
    column: str = Field(min_length=1, max_length=128)
    op: FilterOp
    from_: Any | None = Field(default=None, alias="from")
    to: Any | None = None
    values: list[Any] | None = None
    value: Any | None = None

    @field_validator("column")
    @classmethod
    def _ident(cls, v: str) -> str:
        # column adı Oracle/DuckDB WHERE'ine interpolate edilir (değerler bind,
        # ad değil) — identifier dışı her şey şemada reddedilir (injection).
        return _check_column_ident(v, what="raw filter column")

    @model_validator(mode="after")
    def _normalise_dates(self) -> "RawFilter":
        self.from_ = _iso(self.from_)
        self.to = _iso(self.to)
        self.value = _iso(self.value)
        self.values = _iso_list(self.values)
        return self


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: list[PinnedFilter] = Field(default_factory=list)
    interactive: list[InteractiveFilter] = Field(default_factory=list)
    raw: list[RawFilter] = Field(default_factory=list)


# Faz R1 — Derivation.filters: "Filters" forward-ref'ini şimdi çöz (Filters
# burada tanımlandı). BasketItem ⊃ Derivation olduğundan onu da tazele.
Derivation.model_rebuild()
BasketItem.model_rebuild()


# ── Status ──────────────────────────────────────────────────────────────────

class Status(BaseModel):
    """System-mutated materialisation state (§2.1).

    The user-authored fields of the contract never change once
    ``state == "ready"``; only this block is rewritten as the scope is fetched.
    """

    model_config = ConfigDict(extra="forbid")

    state: Literal["drafting", "fetching", "ready", "failed"] = "drafting"
    fetched_at: datetime | None = None
    fetch_duration_ms: int | None = Field(default=None, ge=0)
    cached_tables: list[str] = Field(default_factory=list)
    lazy_tables: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ── Scope contract (root) ──────────────────────────────────────────────────

class ScopeContract(BaseModel):
    """The durable scope artifact for one presentation version."""

    model_config = ConfigDict(extra="forbid")

    presentation_id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1, le=1_000_000)
    created_by: str = Field(min_length=1, max_length=80)
    created_at: datetime
    parent_version: int | None = Field(default=None, ge=1)

    basket: list[BasketItem] = Field(default_factory=list)
    joins: list[Join] = Field(default_factory=list)
    filters: Filters = Field(default_factory=Filters)
    status: Status = Field(default_factory=Status)
    # Auto-suggested edges the user explicitly dismissed (× on the edge label).
    # Format: "alias.col—alias.col" sorted lexicographically (the same
    # joinKey() string the frontend uses to dedup). Persisted with the scope
    # so the dismissal survives reloads; cleared when one of the aliases is
    # removed from the basket so re-adding gives a clean slate.
    dismissed_suggestions: list[str] = Field(default_factory=list)
    # Faz R/B — "Sunum basketi": basket'te DURAN ama Sunum'a GİTMEYECEK alias'lar.
    # Hazırlık canvas'ında dimmed (kararmış) gösterilir; build yalnız AKTİF
    # (bu listede olmayan) node'ları materialise eder + Sunum'a alır. Kullanıcı
    # sol menüden tıklayarak aktif/pasif yapar.
    inactive_aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _parent_below_self(self) -> "ScopeContract":
        if self.parent_version is not None and self.parent_version >= self.version:
            raise ValueError(
                f"parent_version ({self.parent_version}) must be < version ({self.version})"
            )
        return self

    # ── Convenience accessors (used by validators / routing / resolver) ──────

    def alias_list(self) -> list[str]:
        return [b.alias for b in self.basket]

    def basket_item(self, alias: str) -> BasketItem | None:
        for b in self.basket:
            if b.alias == alias:
                return b
        return None

    def raw_items(self) -> list[BasketItem]:
        """Basket items backed by a real Oracle table."""
        return [b for b in self.basket if b.table_ref is not None]

    def derived_items(self) -> list[BasketItem]:
        """Derived (aggregate) basket items."""
        return [b for b in self.basket if b.derivation is not None]

    def find_pinned(self, filter_id: str) -> PinnedFilter | None:
        for f in self.filters.pinned:
            if f.id == filter_id:
                return f
        return None

    def find_interactive(self, filter_id: str) -> InteractiveFilter | None:
        for f in self.filters.interactive:
            if f.id == filter_id:
                return f
        return None

    def pinned_filter_ids(self) -> set[str]:
        return {f.id for f in self.filters.pinned}

    def pinned_filters_for_alias(self, alias: str) -> list[PinnedFilter]:
        """Pinned filters that target ``alias`` — explicit match, or empty
        ``applies_to`` (= all basket tables, per §2.1)."""
        return [
            f for f in self.filters.pinned
            if not f.applies_to or alias in f.applies_to
        ]

    def raw_filters_for_alias(self, alias: str) -> list["RawFilter"]:
        """Non-concept (raw) filters targeting ``alias`` (§6R.4)."""
        return [f for f in self.filters.raw if f.alias == alias]

    def find_join(self, join_id: str) -> Join | None:
        for j in self.joins:
            if j.id == join_id:
                return j
        return None

    def is_lazy_alias(self, alias: str) -> bool:
        return alias in self.status.lazy_tables


# ── YAML wrappers ───────────────────────────────────────────────────────────

class ScopeDocument(BaseModel):
    """On-disk YAML root: ``{scope: {...}}``."""

    model_config = ConfigDict(extra="forbid")

    scope: ScopeContract


def load_scope_from_dict(raw: dict[str, Any]) -> ScopeContract:
    """Parse a scope dict (with or without the ``scope:`` wrapper)."""
    if isinstance(raw, dict) and set(raw.keys()) == {"scope"}:
        return ScopeDocument.model_validate(raw).scope
    return ScopeContract.model_validate(raw)


def scope_to_dict(scope: ScopeContract) -> dict[str, Any]:
    """Serialise to the ``{scope: {...}}`` shape (aliases applied, dates as
    ISO strings, ``None`` fields dropped). Stable field order."""
    return {
        "scope": scope.model_dump(by_alias=True, mode="json", exclude_none=True)
    }


def load_scope_yaml(text: str) -> ScopeContract:
    """Parse scope YAML text (bool-safe) into a :class:`ScopeContract`."""
    raw = load_yaml(text)
    if raw is None:
        raise ValueError("empty scope YAML")
    return load_scope_from_dict(raw)


def dump_scope_yaml(scope: ScopeContract) -> str:
    """Serialise a :class:`ScopeContract` to YAML text. Idempotent:
    ``dump_scope_yaml(load_scope_yaml(t))`` is a fixed point."""
    return dump_yaml(scope_to_dict(scope))


# ── ScopeRef (for the dashboard manifest, §2.3) ────────────────────────────

class ScopeRef(BaseModel):
    """Pointer from a dashboard manifest to a scope contract version."""

    model_config = ConfigDict(extra="forbid")

    presentation_id: str = Field(min_length=1, max_length=80)
    scope_version: int = Field(ge=1, le=1_000_000)


__all__ = [
    "TableRef", "Projection", "JoinedColumn", "DerivedColumn", "Routing",
    "NodePosition", "AggFn", "Measure", "CalculatedColumn", "CalculatedJoinKey",
    "Derivation", "RefreshSchedule", "DatasetRefresh", "BasketItem",
    "JoinSide", "Join",
    "FilterOp", "PinnedFilter", "InteractiveFilter", "RawFilter", "Filters",
    "Status", "ScopeContract", "ScopeDocument", "ScopeRef",
    "load_scope_from_dict", "scope_to_dict",
    "load_scope_yaml", "dump_scope_yaml",
]

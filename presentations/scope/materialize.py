"""Faz A — dataset materialisation to S3 parquet.

The new data model (Hazırlık = data layer, Sunum = pure visuals): a *cached*
scope dataset is materialised ONCE per refresh into versioned S3 parquet, and
every Sunum viewer reads the parquet — never Oracle. This decouples "veri çekme"
(cron, single writer, deduplicated per dataset) from "çizim" (charts project
columns from the materialised relation in DuckDB).

S3 layout (per presentation, per dataset alias)::

    prisma-treasury/datasets/<pid>/<alias>/data.parquet
    prisma-treasury/datasets/<pid>/<alias>/meta.json   # columns/rows/refreshed_at/sql_hash

Write side runs ONLY from the dataset scheduler (cron) or scope build — a single
writer. Read side (`read_dataset` / `load_into_duck`) runs per pod and never
touches Oracle. Parquet is read via pandas (not DuckDB's read_parquet) so the
DuckDB connection keeps external filesystem access disabled (see
``presentations.duck.connect_duckdb``).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

from presentations.duck import register_dataframe
from presentations.scope.fetch import (
    compile_aggregate_sql,
    compile_calculated_sql,
    compile_join_sql,
    compile_union_sql,
    compile_filter_sql,
    compose_cached_sql,
)
from presentations.scope.schema import BasketItem, ScopeContract

log = logging.getLogger(__name__)

DATASET_S3_PREFIX = "prisma-treasury/datasets"


def dataset_data_key(pid: str, alias: str) -> str:
    return f"{DATASET_S3_PREFIX}/{pid}/{alias}/data.parquet"


def dataset_meta_key(pid: str, alias: str) -> str:
    return f"{DATASET_S3_PREFIX}/{pid}/{alias}/meta.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _sql_hash(sql: str) -> str:
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class DatasetMeta:
    """Lightweight pointer/metadata for a materialised dataset."""

    columns: list[str]
    row_count: int
    refreshed_at: str          # ISO naive-UTC — when the parquet was written
    sql_hash: str              # detects source-SQL drift across refreshes

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "row_count": self.row_count,
            "refreshed_at": self.refreshed_at,
            "sql_hash": self.sql_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DatasetMeta":
        return cls(
            columns=list(d.get("columns") or []),
            row_count=int(d.get("row_count") or 0),
            refreshed_at=str(d.get("refreshed_at") or ""),
            sql_hash=str(d.get("sql_hash") or ""),
        )

    def refreshed_dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.refreshed_at)
        except (ValueError, TypeError):
            return None


def _dedupe_columns(df):
    """Parquet (pyarrow) rejects duplicate column names, which a manual-SQL
    dataset easily produces with ``SELECT *`` over a join (e.g. two tables both
    carrying ``CCY_CODE``). Rename later duplicates to ``COL_2``, ``COL_3`` … so
    materialisation never crashes; the scheduler stays alive and the dataset is
    usable. Logged so the author can clean up the source query."""
    cols = [str(c) for c in df.columns]
    used: set[str] = set()
    out: list[str] = []
    changed = False
    for c in cols:
        if c not in used:
            used.add(c)
            out.append(c)
            continue
        i = 2
        while f"{c}_{i}" in used:
            i += 1
        nc = f"{c}_{i}"
        used.add(nc)
        out.append(nc)
        changed = True
    if changed:
        df = df.copy()
        df.columns = out
        log.warning("materialize: duplicate columns renamed → %s", out)
    return df


def _df_to_parquet_bytes(df) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ── Write side (cron / build — single writer) ───────────────────────────────

def write_dataset(dc, pid: str, alias: str, df, *, sql: str) -> DatasetMeta:
    """Write ``df`` as the materialised parquet for ``(pid, alias)``.

    Data is written first, then the meta pointer — a reader that races sees
    either the previous meta (and previous-or-newer data, both valid) or the new
    meta (and the new data). S3 put_object is atomic per object, so no half-write
    is ever visible.
    """
    df = _dedupe_columns(df)
    dc._upload_bytes(
        dataset_data_key(pid, alias),
        _df_to_parquet_bytes(df),
        content_type="application/octet-stream",
    )
    meta = DatasetMeta(
        columns=[str(c) for c in df.columns],
        row_count=int(len(df)),
        refreshed_at=_now_iso(),
        sql_hash=_sql_hash(sql),
    )
    dc._upload_bytes(
        dataset_meta_key(pid, alias),
        json.dumps(meta.to_dict(), ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )
    log.info(
        "materialize: wrote dataset %s/%s (%d rows, %d cols)",
        pid, alias, meta.row_count, len(meta.columns),
    )
    return meta


def _compute_dataset_df(
    dc, scope: ScopeContract, item: BasketItem, *,
    catalog, concept_registry, binding_catalog, visited: frozenset,
):
    """Compute a dataset's DataFrame WITHOUT persisting it. Returns ``(df, sql)``.

    - ``sql`` / ``table_ref`` sources hit Oracle (the permitted cron/build trigger).
    - ``derived`` (aggregate/calculated) sources compute on DuckDB over their
      source aliases. Each source is taken from its materialised parquet when
      present (cheap, no Oracle) and otherwise computed in-memory here — so a
      derived dataset over a *lazy* source still works: the cron pulls the big
      source once, aggregates, and only the small result is persisted.

    ``visited`` guards against a derivation cycle (defence in depth — the scope
    validators already enforce a DAG).
    """
    import pandas as pd

    if item.sql is not None:
        from presentations.sql.validator import validate_sql
        check = validate_sql(item.sql)
        if not check.ok:
            raise ValueError(
                f"materialize_dataset: dataset {item.alias!r} SQL rejected by "
                f"whitelist: {'; '.join(check.errors)}"
            )
        df = dc.get_data(
            base_prefix=None,
            dataset=f"dataset::{scope.presentation_id}/{item.alias}",
            query=item.sql, query_params={},
        )
        return (df if df is not None else pd.DataFrame()), item.sql

    if item.table_ref is not None:
        sql, binds = compose_cached_sql(
            scope, item, catalog,
            concept_registry=concept_registry, binding_catalog=binding_catalog,
        )
        df = dc.get_data(
            base_prefix=None,
            dataset=f"dataset::{scope.presentation_id}/{item.alias}",
            query=sql, query_params=binds,
        )
        return (df if df is not None else pd.DataFrame()), sql

    # Filter-node (Faz R1/A): re-query the source with the embedded filters.
    #  - Oracle source (lazy main) → compile_filter_sql (Oracle), dc.get_data.
    #  - Derived source (Faz A zincirleme) → compute the source recursively, run
    #    the DuckDB filter SQL over it. Only the small filtered result persists.
    if item.derivation is not None and item.derivation.kind == "filter":
        d = item.derivation
        sql, binds = compile_filter_sql(
            scope, item, catalog,
            concept_registry=concept_registry, binding_catalog=binding_catalog,
        )
        src = scope.basket_item(d.source_alias)
        if src is not None and src.table_ref is None:
            # Derived source — materialise it (parquet or in-memory), filter in DuckDB.
            if item.alias in visited:
                raise ValueError(f"materialize_dataset: derivation cycle through {item.alias!r}")
            from presentations.duck import connect_duckdb
            got = read_dataset(dc, scope.presentation_id, d.source_alias)
            if got is not None:
                src_df = got[0]
            else:
                src_df, _ = _compute_dataset_df(
                    dc, scope, src, catalog=catalog,
                    concept_registry=concept_registry, binding_catalog=binding_catalog,
                    visited=visited | {item.alias},
                )
            conn = connect_duckdb(":memory:")
            register_dataframe(conn, d.source_alias, src_df)
            df = conn.execute(sql, binds).fetchdf() if binds else conn.execute(sql).fetchdf()
            return df, sql
        df = dc.get_data(
            base_prefix=None,
            dataset=f"dataset::{scope.presentation_id}/{item.alias}",
            query=sql, query_params=binds,
        )
        return (df if df is not None else pd.DataFrame()), sql

    # Derived (aggregate/calculated): compute on DuckDB over the source aliases.
    if item.alias in visited:
        raise ValueError(f"materialize_dataset: derivation cycle through {item.alias!r}")
    visited = visited | {item.alias}

    from presentations.duck import connect_duckdb

    d = item.derivation
    src_aliases = [d.source_alias] if d.kind == "aggregate" else list(d.source_aliases)
    by_alias = {b.alias: b for b in scope.basket}

    conn = connect_duckdb(":memory:")
    cols_by_src: dict[str, list[str]] = {}
    for src in src_aliases:
        got = read_dataset(dc, scope.presentation_id, src)
        if got is not None:
            src_df = got[0]
        else:
            src_item = by_alias.get(src)
            if src_item is None:
                src_df = pd.DataFrame()
            else:
                src_df, _ = _compute_dataset_df(
                    dc, scope, src_item, catalog=catalog,
                    concept_registry=concept_registry, binding_catalog=binding_catalog,
                    visited=visited,
                )
        cols_by_src[src] = list(src_df.columns)
        register_dataframe(conn, src, src_df)

    if d.kind == "aggregate":
        sql = compile_aggregate_sql(item)
    elif d.kind == "join":
        sql = compile_join_sql(item, cols_by_src.get(d.source_aliases[0], []),
                               cols_by_src.get(d.source_aliases[1], []))
    elif d.kind == "union":
        sql = compile_union_sql(item)
    else:  # calculated
        sql = compile_calculated_sql(item)
    df = conn.execute(sql).fetchdf()
    return df, sql


def materialize_dataset(
    dc, scope: ScopeContract, item: BasketItem, *,
    catalog=None, concept_registry=None, binding_catalog=None,
) -> DatasetMeta:
    """Materialise a cached dataset to S3 parquet (single writer: cron / build).

    Handles all three source kinds:
    - ``table_ref`` — composed projection/pinned SQL via ``compose_cached_sql``.
    - ``sql`` (Faz C) — the user/LLM-authored free-form query, re-validated
      against the SELECT/WITH whitelist before it runs as the service account.
    - ``derived`` (aggregate/calculated) — computed on DuckDB over the dataset's
      source aliases (each resolved from its parquet, or in-memory if absent),
      then the *result* is persisted so viewers read it like any other cached
      dataset. This is what makes an aggregate/derived table cron-able: N charts
      drawing from it read one small parquet, and the expensive source query
      runs once per interval — never on a viewer's request.
    """
    df, sql = _compute_dataset_df(
        dc, scope, item, catalog=catalog,
        concept_registry=concept_registry, binding_catalog=binding_catalog,
        visited=frozenset(),
    )
    return write_dataset(dc, scope.presentation_id, item.alias, df, sql=sql)


# ── Read side (per pod — never touches Oracle) ──────────────────────────────

def read_dataset_meta(dc, pid: str, alias: str) -> Optional[DatasetMeta]:
    """Return the dataset's meta pointer, or None if not materialised yet."""
    try:
        raw = dc.read_json(dataset_meta_key(pid, alias))
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    return DatasetMeta.from_dict(raw)


def read_dataset(dc, pid: str, alias: str):
    """Return ``(DataFrame, DatasetMeta)`` for a materialised dataset, or None.

    Reads parquet via pandas — DuckDB keeps external access off."""
    import pandas as pd

    meta = read_dataset_meta(dc, pid, alias)
    if meta is None:
        return None
    try:
        blob = dc.read_bytes(dataset_data_key(pid, alias))
    except Exception:
        return None
    if not blob:
        return None
    return pd.read_parquet(io.BytesIO(blob)), meta


# ── Sunum read: project a dataset-bound block (Faz B) ───────────────────────

# DuckDB identifier guard for values interpolated into SQL. Aliases come from
# the scope (Alias type, lowercase) and columns from the materialised dataset's
# schema, but a Sunum block's dataset_binding is still user-authored, so we
# validate before interpolation rather than trust it.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")


def _view_exists(conn, alias: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [alias]
        ).fetchone() is not None
    except Exception:
        return False


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _coerce(v):
    """Coerce ISO-date-looking strings to date objects so DuckDB binds them as
    DATE (correct comparison against a DATE column); pass everything else
    through. Bound as parameters — never concatenated."""
    if isinstance(v, str) and _DATE_RE.match(v):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return v
    return v


def _filter_predicate(spec: dict, value, params: dict, idx: int) -> str | None:
    """Build one DuckDB WHERE fragment for an interactive-filter→column spec,
    binding values into ``params`` ($-style). Returns None to skip (no/empty
    value, bad column, or unknown op). Determinism: the column is declared
    explicitly on the binding (no regex inference)."""
    col = spec.get("column")
    op = spec.get("op")
    if not col or not _IDENT_RE.match(str(col)):
        return None
    p = f"flt{idx}"
    if op == "between":
        if not isinstance(value, dict):
            return None
        lo = value.get("from", value.get("min"))
        hi = value.get("to", value.get("max"))
        if lo is None or hi is None:
            return None
        params[f"{p}_lo"] = _coerce(lo)
        params[f"{p}_hi"] = _coerce(hi)
        return f'"{col}" BETWEEN ${p}_lo AND ${p}_hi'
    if op in ("in", "not_in"):
        vals = value if isinstance(value, list) else None
        if not vals:
            return None
        names = []
        for j, x in enumerate(vals):
            params[f"{p}_{j}"] = _coerce(x)
            names.append(f"${p}_{j}")
        kw = "IN" if op == "in" else "NOT IN"
        return f'"{col}" {kw} ({", ".join(names)})'
    if op in ("eq", "date"):
        if value is None or value == "":
            return None
        params[p] = _coerce(value)
        return f'"{col}" = ${p}'
    return None


def project_block_from_dataset(conn, binding: dict, filter_state: dict | None = None):
    """Project a dataset-bound Sunum block from its materialised DuckDB view,
    applying interactive dashboard filters as LOCAL DuckDB predicates.

    ``binding = {"alias": str, "columns": [str]?, "filters": [{filter_id, column,
    op}]?}``. Each filter spec maps a dashboard interactive filter (looked up in
    ``filter_state`` by ``filter_id``) to a dataset column — an explicit,
    deterministic mapping (no regex inference). Returns the projected DataFrame,
    or ``None`` when the alias view isn't registered (dataset not materialised).
    NEVER touches Oracle — the view came from parquet via :func:`load_into_duck`.
    """
    alias = (binding or {}).get("alias")
    if not alias or not _IDENT_RE.match(str(alias)) or not _view_exists(conn, alias):
        return None
    cols = [c for c in (binding.get("columns") or []) if _IDENT_RE.match(str(c))]
    select = ", ".join(f'"{c}"' for c in cols) if cols else "*"

    fs = filter_state or {}
    params: dict[str, Any] = {}
    clauses: list[str] = []
    for i, spec in enumerate(binding.get("filters") or []):
        if not isinstance(spec, dict):
            continue
        value = fs.get(spec.get("filter_id"))
        if value is None:
            continue
        frag = _filter_predicate(spec, value, params, i)
        if frag:
            clauses.append(frag)

    sql = f'SELECT {select} FROM "{alias}"'
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    try:
        return conn.execute(sql, params).fetchdf() if params else conn.execute(sql).fetchdf()
    except Exception:
        log.warning("project_block_from_dataset: query failed for alias %s", alias,
                    exc_info=True)
        return None


def _ensure_state_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS __dataset_meta "
        "(alias VARCHAR PRIMARY KEY, refreshed_at VARCHAR)"
    )


def load_into_duck(dc, conn, scope: ScopeContract) -> dict[str, dict[str, Any]]:
    """Materialise every cached dataset of ``scope`` into the session DuckDB
    (real tables) so Sunum charts can project columns locally with NO Oracle
    round-trip. Returns ``{alias: {rows, refreshed_at}}`` for what's available.

    Tazelik kontrolü: her alias için önce küçük ``meta.json`` okunur; oturumda
    aynı ``refreshed_at`` ile yazılmış tablo zaten varsa parquet BLOB'u hiç
    indirilmez (eskiden her hidrasyon TÜM parquet'leri S3'ten çekiyordu).
    Durum ``__dataset_meta`` tablosunda tutulur — session.duckdb ile birlikte
    yaşar, pod restart'ta parquet'ten doğal şekilde yeniden dolar.

    Datasets not yet materialised (cron hasn't run) are simply skipped — the
    viewer never triggers a fetch; the chart renders empty until the parquet
    is warmed (build-time one-shot ya da cron).
    """
    import io as _io

    import pandas as pd

    from presentations.duck import materialize_table

    loaded: dict[str, dict[str, Any]] = {}
    _ensure_state_table(conn)
    state = dict(conn.execute("SELECT alias, refreshed_at FROM __dataset_meta").fetchall())
    for item in scope.basket:
        if item.routing.decision != "cached":
            continue
        meta = read_dataset_meta(dc, scope.presentation_id, item.alias)
        if meta is None:
            continue
        if state.get(item.alias) == meta.refreshed_at and _view_exists(conn, item.alias):
            loaded[item.alias] = {"rows": meta.row_count, "refreshed_at": meta.refreshed_at}
            continue
        try:
            blob = dc.read_bytes(dataset_data_key(scope.presentation_id, item.alias))
        except Exception:
            continue
        if not blob:
            continue
        df = pd.read_parquet(_io.BytesIO(blob))
        if len(df.columns) > 0:
            materialize_table(conn, item.alias, df)
            conn.execute(
                "INSERT OR REPLACE INTO __dataset_meta VALUES (?, ?)",
                [item.alias, meta.refreshed_at],
            )
        loaded[item.alias] = {"rows": meta.row_count, "refreshed_at": meta.refreshed_at}
    return loaded

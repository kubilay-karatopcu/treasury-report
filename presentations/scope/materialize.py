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
from presentations.scope.fetch import compose_cached_sql
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


def materialize_dataset(
    dc, scope: ScopeContract, item: BasketItem, *,
    catalog=None, concept_registry=None, binding_catalog=None,
) -> DatasetMeta:
    """Run a cached ``table_ref`` dataset's Oracle SQL and persist its parquet.

    Derived (aggregate/calculated) datasets compute on DuckDB over their source
    aliases at read time, so only ``table_ref``-backed cached datasets are
    materialised here. ``max_rows`` is left unbounded — routing already keeps a
    cached dataset under the byte threshold.
    """
    import pandas as pd

    if item.table_ref is None:
        raise ValueError(
            f"materialize_dataset: dataset {item.alias!r} has no table_ref "
            "(derived datasets are computed in DuckDB from materialised sources)"
        )
    sql, binds = compose_cached_sql(
        scope, item, catalog,
        concept_registry=concept_registry, binding_catalog=binding_catalog,
    )
    df = dc.get_data(
        base_prefix=None,
        dataset=f"dataset::{scope.presentation_id}/{item.alias}",
        query=sql, query_params=binds,
    )
    if df is None:
        df = pd.DataFrame()
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


def load_into_duck(dc, conn, scope: ScopeContract) -> dict[str, dict[str, Any]]:
    """Register every materialised cached dataset of ``scope`` as a DuckDB view
    named by its alias, so Sunum charts can project columns locally with NO
    Oracle round-trip. Returns ``{alias: {rows, refreshed_at}}`` for what loaded.

    Datasets not yet materialised (cron hasn't run) are simply skipped — the
    viewer never triggers a fetch; the chart renders empty until the cron warms
    the parquet.
    """
    loaded: dict[str, dict[str, Any]] = {}
    for item in scope.basket:
        if item.table_ref is None or item.routing.decision != "cached":
            continue
        got = read_dataset(dc, scope.presentation_id, item.alias)
        if got is None:
            continue
        df, meta = got
        if len(df.columns) > 0:
            register_dataframe(conn, item.alias, df)
        loaded[item.alias] = {"rows": meta.row_count, "refreshed_at": meta.refreshed_at}
    return loaded

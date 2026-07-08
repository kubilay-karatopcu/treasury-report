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
from datetime import date, datetime, timedelta, timezone
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

    # Python node (Faz P): tek-girişli prosedürel dönüşüm. Source df'i (parquet
    # ya da in-memory recursive) `input_node_df` olarak ver, AST-whitelist +
    # subprocess sandbox'ta çalıştır, `output_node_df` sonucunu persist et. Yalnız
    # TEK source enjekte edilir → izolasyon yapısaldır. Cron bu yolu kullandığı
    # için upstream zincir (query → python → query …) burada sırayla yeniden koşar.
    if item.derivation is not None and item.derivation.kind == "python":
        from presentations.python_runtime import run_python_transform

        d = item.derivation
        if item.alias in visited:
            raise ValueError(f"materialize_dataset: derivation cycle through {item.alias!r}")
        src_item = scope.basket_item(d.source_alias)
        got = read_dataset(dc, scope.presentation_id, d.source_alias)
        if got is not None:
            src_df = got[0]
        elif src_item is not None:
            src_df, _ = _compute_dataset_df(
                dc, scope, src_item, catalog=catalog,
                concept_registry=concept_registry, binding_catalog=binding_catalog,
                visited=visited | {item.alias},
            )
        else:
            src_df = pd.DataFrame()
        result = run_python_transform(d.python_code, src_df)
        if not result.ok:
            raise ValueError(
                f"python node {item.alias!r} çalıştırması başarısız: {result.error}"
            )
        return result.df, d.python_code

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


# Tam-anchor'lu: ya saf ISO tarih ya da saat bileşenli ISO datetime. Başlangıç-
# anchor'lı eski desen '2026-06-01extra' gibi artık çöpü sessizce kabul edip
# tarihe kırpıyordu; bu yüzden $ ile sona da sabitliyoruz.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$"
)


def _coerce(v):
    """ISO tarih/zaman görünümlü string'leri DuckDB'nin doğru tipte bağlaması için
    coerce et: saat bileşeni varsa ``datetime`` (TIMESTAMP), saf tarih ise ``date``
    (DATE) dön. Saat bileşeni ARTIK sessizce düşmüyor (Oracle DATE kolonları saat
    taşır, parquet'te datetime64 olarak korunur). Tanınamayan / artık karakterli
    değer ham string olarak geçer. Her zaman parametre olarak bağlanır — asla
    SQL'e gömülmez."""
    if isinstance(v, str):
        if _DATE_RE.match(v):
            try:
                return date.fromisoformat(v)
            except ValueError:
                return v
        if _DATETIME_RE.match(v):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                return v
    return v


def _is_pure_date(v) -> bool:
    """datetime, date'in alt sınıfı — saat bileşeni TAŞIMAYAN saf tarih mi?"""
    return isinstance(v, date) and not isinstance(v, datetime)


def _datetime_columns(conn, alias: str) -> frozenset:
    """Materialise edilmiş view'ın datetime/timestamp tipli kolonlarını DuckDB
    ``DESCRIBE``'ından oku. between/eq'in saf-tarih sınırını gün-içi satırları da
    kapsayacak şekilde genişletip genişletmeyeceğine bununla karar verilir."""
    try:
        rows = conn.execute('DESCRIBE "' + alias + '"').fetchall()
    except Exception:
        return frozenset()
    out: set[str] = set()
    for r in rows:
        t = str(r[1]).upper()
        if "TIMESTAMP" in t or "DATETIME" in t or t == "DATE":
            out.add(str(r[0]))
    return frozenset(out)


def _day_inclusive_upper(hi, col_is_dt: bool):
    """TIMESTAMP kolon + saf-tarih üst sınır → gün-kapsayıcı (yarı-açık) semantik.
    BETWEEN inclusive üst sınırı, sınır gününde 14:30 gibi satırları düşürürdü;
    sınırı (hi + 1 gün)'e çekip ``< hi`` kullanırsak tüm gün dahil olur. Geriye
    ``(yeni_hi, yarı_açık_mi)`` döner."""
    if col_is_dt and _is_pure_date(hi):
        return hi + timedelta(days=1), True
    return hi, False


def _filter_predicate(spec: dict, value, params: dict, idx: int,
                      dt_cols: frozenset = frozenset()) -> str | None:
    """Build one DuckDB WHERE fragment for an interactive-filter→column spec,
    binding values into ``params`` ($-style). Returns None to skip (no/empty
    value, bad column, or unknown op). Determinism: the column is declared
    explicitly on the binding (no regex inference).

    ``dt_cols`` view'ın datetime/timestamp kolon adı kümesidir; saf-tarih sınırlı
    between/eq bunlar üzerinde gün-kapsayıcı yarı-açık aralığa dönüşür."""
    col = spec.get("column")
    op = spec.get("op")
    if not col or not _IDENT_RE.match(str(col)):
        return None
    col_is_dt = col in dt_cols
    p = f"flt{idx}"
    if op == "between":
        if not isinstance(value, dict):
            return None
        lo = value.get("from", value.get("min"))
        hi = value.get("to", value.get("max"))
        if lo is None or hi is None:
            return None
        hi_c, half_open = _day_inclusive_upper(_coerce(hi), col_is_dt)
        params[f"{p}_lo"] = _coerce(lo)
        params[f"{p}_hi"] = hi_c
        if half_open:
            return f'"{col}" >= ${p}_lo AND "{col}" < ${p}_hi'
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
        v_c = _coerce(value)
        # eq + saf-tarih değer + TIMESTAMP kolon → o günün tüm satırları (gece
        # yarısı dışındakiler de). Aksi halde 14:30 satırı 0 satır eşleşirdi.
        if col_is_dt and _is_pure_date(v_c):
            params[f"{p}_lo"] = v_c
            params[f"{p}_hi"] = v_c + timedelta(days=1)
            return f'"{col}" >= ${p}_lo AND "{col}" < ${p}_hi'
        params[p] = v_c
        return f'"{col}" = ${p}'
    return None


def _concept_predicates(column_concepts, concept_filters, params: dict,
                        dt_cols: frozenset = frozenset(),
                        prefix: str = "cf") -> list[str]:
    """#4 / end-to-end — dataset (türetilmiş) node'a CONCEPT filtrelerini uygula.

    ``column_concepts = {COLUMN: concept_id}`` kullanıcının Hazırlık'ta o node'un
    kolonuna bağladığı concept'tir → IDENTITY semantiği (kolon değeri = canonical
    değer). Aktif concept filtrelerinden, bu node'un bir kolonuna bağlı olan her
    concept için DuckDB predicate'i üretilir. Böylece concept filtresi katalog
    tablolarındaki gibi türetilmiş node'larda da çalışır.

    ``concept_filters = [{"concept", "operator", "values"}]`` (canonical değerler).
    """
    if not column_concepts or not concept_filters:
        return []
    by_concept: dict[str, list[str]] = {}
    for col, cid in column_concepts.items():
        if cid and _IDENT_RE.match(str(col)):
            by_concept.setdefault(cid, []).append(str(col))
    clauses: list[str] = []
    idx = 0
    for f in concept_filters:
        cid = (f or {}).get("concept")
        op = (f or {}).get("operator")
        vals = (f or {}).get("values") or []
        cols = by_concept.get(cid)
        if not cols or not vals:
            continue
        for col in cols:
            col_is_dt = col in dt_cols
            p = f"{prefix}{idx}"
            if op == "between" and len(vals) == 2 and vals[0] is not None and vals[1] is not None:
                hi_c, half_open = _day_inclusive_upper(_coerce(vals[1]), col_is_dt)
                params[f"{p}_lo"] = _coerce(vals[0])
                params[f"{p}_hi"] = hi_c
                if half_open:
                    clauses.append(f'"{col}" >= ${p}_lo AND "{col}" < ${p}_hi')
                else:
                    clauses.append(f'"{col}" BETWEEN ${p}_lo AND ${p}_hi')
            elif op in ("in", "eq"):
                names = []
                for j, x in enumerate(vals):
                    params[f"{p}_{j}"] = _coerce(x)
                    names.append(f"${p}_{j}")
                clauses.append(f'"{col}" IN ({", ".join(names)})')
            idx += 1
    return clauses


_SCHEMA_TABLE_RE = re.compile(r"^[A-Za-z_][\w$#]*\.[A-Za-z_][\w$#]*$")


def inherit_source_bindings(conn, alias: str, source_ref: str | None,
                            concept_filters, catalog, *,
                            skip_concepts: set | None = None):
    """Base→türev binding mirası: kaynak katalog tablosunun human_verified
    binding'lerini, aynı adla türetilmiş view'a taşınmış kolonlara uygula.

    Sunum filtre çubuğu basket'teki KAYNAK tabloların binding'lerinden
    tohumlanır; bloklar ise Hazırlık'ta üretilen view/dataset'i okur. View
    katmanında binding olmadığı için filtreler daha önce koşulsuz blind
    kalıyordu ("filtre seçtim ama grafik değişmiyor"). Bu fonksiyon,
    ``source_ref`` (SCHEMA.TABLE) üzerindeki binding'lerden view'da birebir
    var olan kolonlara predicate eşlemesi türetir:

    - ``identity`` / ``time_truncation`` → kolon → concept (identity predicate;
      tarih kolonları ``dt_cols`` üzerinden gün-kapsayıcı işlenir),
    - ``map`` → kolon → concept + filtre değerleri canonical→tablo değerine
      çevrilir (pairs tersine çevrilir; compiler'la aynı semantik, bilinmeyen
      canonical düşer),
    - ``lookup`` / ``bucket_from_range`` → atlanır (view'da dim join garantisi
      yok) — concept blind kalır ve UI rozetinde görünür.

    Returns ``(extra_column_concepts, translated_filters)``. Bir concept'e
    çevrilmiş değer kalmazsa (map'te hiç eşleşme yok) o concept miras
    ALINMAZ — sessizce filtresiz koşmak yerine görünür şekilde blind kalır.
    """
    if not source_ref or catalog is None or not concept_filters:
        return {}, list(concept_filters or [])
    ref = str(source_ref).strip()
    if not _SCHEMA_TABLE_RE.match(ref):
        return {}, list(concept_filters)
    schema, _, table = ref.upper().partition(".")

    try:
        view_cols = {str(r[0]) for r in conn.execute('DESCRIBE "' + alias + '"').fetchall()}
    except Exception:
        return {}, list(concept_filters)

    skip = skip_concepts or set()
    extra_cc: dict = {}
    translated: list = []
    for f in concept_filters:
        cid = (f or {}).get("concept")
        if not cid or cid in skip:
            translated.append(f)
            continue
        try:
            binding = catalog.get_binding(schema, table, cid)
        except Exception:
            binding = None
        if binding is None or binding.column not in view_cols:
            translated.append(f)
            continue
        kind = binding.transform.kind
        if kind in ("identity", "time_truncation"):
            extra_cc[binding.column] = cid
            translated.append(f)
        elif kind == "map" and (f.get("operator") in ("in", "eq")):
            inv: dict[str, list[str]] = {}
            for table_val, canon in binding.transform.pairs.items():
                inv.setdefault(str(canon), []).append(str(table_val))
            new_vals = [tv for v in (f.get("values") or [])
                        for tv in inv.get(str(v), [])]
            if new_vals:
                extra_cc[binding.column] = cid
                translated.append({**f, "values": new_vals})
            else:
                translated.append(f)
        else:
            translated.append(f)
    return extra_cc, translated


_CONCEPT_SENTINEL = "{{concept_filters}}"


def inject_dataset_concepts(
    sql: str, column_concepts: dict | None, concept_filters,
) -> tuple[str, dict]:
    """Replace the ``{{concept_filters}}`` sentinel in a produced-view block's
    SQL with DuckDB predicates derived from the view's ``column_concepts`` and
    the active dashboard concept filters.

    This is the aggregation (manual-SQL) counterpart to the projection-only
    :func:`project_block_from_dataset` path: a KPI / chart that runs ``AVG(...)``
    over a Hazırlık-produced view becomes interactively filterable by a concept
    the user bound to one of its columns — WITHOUT a catalog table-doc binding
    (the Phase 7 compiler is catalog-table-only, so it can't reach a derived
    view). Returns ``(sql, params)``; when no active filter maps to a bound
    column the sentinel collapses to ``1 = 1`` (no-op, returns all rows).
    Parameterised ``$``-binds only — values are never concatenated into SQL.
    """
    params: dict = {}
    clauses = _concept_predicates(column_concepts or {}, concept_filters or [], params)
    predicate = " AND ".join(clauses) if clauses else "1 = 1"
    return sql.replace(_CONCEPT_SENTINEL, predicate), params


def project_block_from_dataset(conn, binding: dict, filter_state: dict | None = None,
                               *, concept_filters=None, column_concepts=None,
                               source_ref: str | None = None, binding_catalog=None):
    """Project a dataset-bound Sunum block from its materialised DuckDB view,
    applying interactive dashboard filters as LOCAL DuckDB predicates.

    ``binding = {"alias": str, "columns": [str]?, "filters": [{filter_id, column,
    op}]?}``. Each filter spec maps a dashboard interactive filter (looked up in
    ``filter_state`` by ``filter_id``) to a dataset column — an explicit,
    deterministic mapping (no regex inference). Returns the projected DataFrame,
    or ``None`` when the alias view isn't registered (dataset not materialised).
    NEVER touches Oracle — the view came from parquet via :func:`load_into_duck`.

    ``concept_filters`` + ``column_concepts`` (end-to-end #4): aktif concept
    filtreleri, kullanıcının bu node kolonlarına bağladığı concept'lerle
    eşleşirse identity predicate olarak da uygulanır.

    ``source_ref`` + ``binding_catalog``: dataset'in kaynağı olduğu katalog
    tablosunun human_verified binding'leri view kolonlarına miras alınır
    (:func:`inherit_source_bindings`) — kullanıcı Hazırlık'ta kolona concept
    bağlamamış olsa bile kaynak tablodan gelen binding filtreyi işletir.
    """
    alias = (binding or {}).get("alias")
    if not alias or not _IDENT_RE.match(str(alias)) or not _view_exists(conn, alias):
        return None
    cols = [c for c in (binding.get("columns") or []) if _IDENT_RE.match(str(c))]
    select = ", ".join(f'"{c}"' for c in cols) if cols else "*"

    # Kolon tipleri view'dan (DESCRIBE) okunur → datetime kolonlarda saf-tarih
    # sınırlı between/eq gün-içi satırları da kapsar.
    dt_cols = _datetime_columns(conn, alias)
    fs = filter_state or {}
    params: dict[str, Any] = {}
    clauses: list[str] = []
    for i, spec in enumerate(binding.get("filters") or []):
        if not isinstance(spec, dict):
            continue
        value = fs.get(spec.get("filter_id"))
        if value is None:
            continue
        frag = _filter_predicate(spec, value, params, i, dt_cols)
        if frag:
            clauses.append(frag)
    # Concept filtreleri (column_concepts → identity) — katalog binding'i olmayan
    # türetilmiş node'lara da uygulanır.
    clauses += _concept_predicates(column_concepts, concept_filters, params, dt_cols)
    # Kaynak tablo binding'lerinin mirası (base→türev): column_concepts'in
    # kapsamadığı concept'ler için kaynak kolon adı view'da aynen varsa
    # predicate oradan türetilir.
    extra_cc, translated = inherit_source_bindings(
        conn, alias, source_ref, concept_filters, binding_catalog,
        skip_concepts=set((column_concepts or {}).values()),
    )
    if extra_cc:
        clauses += _concept_predicates(extra_cc, translated, params, dt_cols,
                                       prefix="ih")

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


# ── Sample/full fidelity ledger (Oturum 1, A5) ────────────────────────────────
# Kept SEPARATE from __dataset_meta so that table's (alias, refreshed_at)
# contract and its positional `VALUES (?, ?)` inserts stay untouched. Tracks,
# per alias, whether the session DuckDB relation is a design-time SAMPLE or the
# build-time FULL data, plus the composed-SQL fingerprint so a sample is
# re-materialised when a scope edit changes its SQL. Lives in session.duckdb →
# survives across requests, wiped on pod restart (re-materialised lazily on the
# next preview).

def _ensure_fidelity_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS __dataset_fidelity "
        "(alias VARCHAR PRIMARY KEY, fidelity VARCHAR, fingerprint VARCHAR, "
        "row_count BIGINT, refreshed_at VARCHAR)"
    )


def record_fidelity(conn, alias: str, fidelity: str, *,
                    fingerprint: Optional[str] = None,
                    row_count: Optional[int] = None) -> None:
    """Record that the session relation ``alias`` holds a 'sample' or 'full'
    dataset (with the composed-SQL ``fingerprint`` for sample staleness)."""
    _ensure_fidelity_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO __dataset_fidelity "
        "(alias, fidelity, fingerprint, row_count, refreshed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [alias, fidelity, fingerprint,
         int(row_count) if row_count is not None else None, _now_iso()],
    )


def dataset_fidelity(conn, alias: str) -> Optional[dict]:
    """Return ``{fidelity, fingerprint, row_count, refreshed_at}`` for ``alias``
    or ``None`` when no fidelity has been recorded (treat as absent)."""
    _ensure_fidelity_table(conn)
    row = conn.execute(
        "SELECT fidelity, fingerprint, row_count, refreshed_at "
        "FROM __dataset_fidelity WHERE alias = ?", [alias],
    ).fetchone()
    if row is None:
        return None
    return {"fidelity": row[0], "fingerprint": row[1],
            "row_count": row[2], "refreshed_at": row[3]}


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

"""
Oracle → Arrow → DuckDB bridge + Excel upload routing.

execute_block_sql now detects two engines:
  - Oracle (default): SQL goes through DataClient.get_data, single result view
  - DuckDB-only (when SQL references upload__... tables): we ensure those
    sheets are loaded into DuckDB from S3 first, then run SQL on the DuckDB
    connection directly. No Oracle round-trip.

Excel sheet references in SQL use a stable convention:
   upload__<upload_id>__<sheet_sanitised_name>
Example: `upload__u_a8Df12__targets`

The caller (LLM, refresh endpoint) writes this name in SQL; we resolve it at
execute time to (s3_key, sheet_display_name).
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import re
from typing import TYPE_CHECKING, Callable, Optional

import sqlparse
from sqlparse import sql as _sql
from sqlparse import tokens as _tok

from .aggregation_gate import GateError, GateResult, MAX_RAW_ROWS, validate_and_wrap

if TYPE_CHECKING:
    import duckdb
    import pandas as pd


log = logging.getLogger(__name__)


# ── DuckDB connection hardening ─────────────────────────────────────────────
# Block SQL is user-/LLM-authored. DuckDB's default config lets a plain SELECT
# reach the filesystem and network (read_csv, read_text, read_blob, glob,
# ATTACH, COPY, INSTALL/LOAD). This module only ever queries in-memory
# relations registered from DataFrames plus the session database, so we disable
# external access on every connection that may run block SQL — closing an
# arbitrary file-read vector without removing any capability the app uses.
def connect_duckdb(database: str = ":memory:", **kwargs):
    """Open a DuckDB connection with external filesystem/network access off.

    Use everywhere user- or LLM-authored block SQL may execute (the per-session
    DB, library preview, the DEV stub). Callers may still pass extra ``config``
    keys; ``enable_external_access`` defaults to ``"false"`` unless overridden.
    """
    import duckdb

    config = dict(kwargs.pop("config", None) or {})
    config.setdefault("enable_external_access", "false")
    return duckdb.connect(database, config=config, **kwargs)


# ════════════════════════════════════════════════════════════════════════════
# LEGACY BASKET PATH — unchanged
# ════════════════════════════════════════════════════════════════════════════
# (preserved verbatim from previous version — fetch_basket_item,
#  register_dataframe, populate_basket, list_views, preview_view,
#  summarize_views, _compute_stats, _jsonable, _safe_identifier, _table_alias)

def _table_alias(table_id: str) -> str:
    return table_id.split(".")[-1].lower()


def fetch_basket_item(dc, basket_item: dict) -> "pd.DataFrame":
    import pandas as pd

    table_id = basket_item["table"]
    columns = basket_item.get("columns") or ["*"]
    row_filter = basket_item.get("row_filter")
    row_limit = int(basket_item.get("row_limit", MAX_RAW_ROWS))

    cols_sql = ", ".join(columns) if columns != ["*"] else "*"
    where_sql = f" WHERE {row_filter}" if row_filter else ""
    limit_sql = f" FETCH FIRST {row_limit} ROWS ONLY" if row_limit > 0 else ""
    sql = f"SELECT {cols_sql} FROM {table_id}{where_sql}{limit_sql}"

    df = dc.get_data(base_prefix=None, dataset=table_id, query=sql, query_params={})

    if df is None or len(df.columns) == 0:
        placeholder_cols = columns if columns != ["*"] else ["_no_data"]
        df = pd.DataFrame({c: pd.Series(dtype=object) for c in placeholder_cols})
        log.info("duck.fetch_basket_item: %s -> 0 rows (no mock data, placeholder schema)", table_id)
    else:
        log.info("duck.fetch_basket_item: %s -> %d rows", table_id, len(df))

    return df


def register_dataframe(conn, view_name, df):
    try:
        conn.unregister(view_name)
    except Exception:
        pass
    conn.register(view_name, df)
    log.debug("duck.register_dataframe: %s (%d rows)", view_name, len(df))


def materialize_table(conn, name: str, df):
    """Persist ``df`` as a REAL DuckDB table named ``name`` (scope datasets).

    ``conn.register`` bir pandas DataFrame'i Python RAM'inde tutan sanal bir
    view üretir ve CONNECTION ile ölür — idle cleanup (30 dk) ya da pod restart
    sonrası her scope dataset'i Oracle'dan yeniden çekiliyordu, RAM'de de ikinci
    bir kopya yaşıyordu. Gerçek tablo ``session.duckdb`` dosyasına yazılır:
    yeniden bağlanınca durur, pandas kopyası GC'ye bırakılır."""
    tmp = f"__mat_{name}"
    try:
        conn.unregister(tmp)
    except Exception:
        pass
    conn.register(tmp, df)
    try:
        # Aynı isimde önceki register'dan kalan VIEW, CREATE TABLE ile çakışır →
        # önce onu düşür. AMA `name` zaten gerçek bir TABLE ise (ikinci materialize:
        # re-build / cron refresh, kalıcı session conn'unda), DROP VIEW IF EXISTS
        # no-op DEĞİL — duckdb 1.5.2'de tip-uyuşmazlığı CatalogException atar
        # (IF EXISTS yalnız 'bulunamadı' halini yutar, tip-uyuşmazlığını değil).
        # try/except ile yut; var olan TABLE'ı zaten CREATE OR REPLACE değiştirir.
        try:
            conn.execute(f'DROP VIEW IF EXISTS "{name}"')
        except Exception:
            pass
        conn.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "{tmp}"')
    finally:
        try:
            conn.unregister(tmp)
        except Exception:
            pass
    log.debug("duck.materialize_table: %s (%d rows)", name, len(df))


def drop_relation(conn, name: str) -> None:
    """Drop ``name`` whether it is a view (legacy register) or a table."""
    for stmt in (f'DROP VIEW IF EXISTS "{name}"', f'DROP TABLE IF EXISTS "{name}"'):
        try:
            conn.execute(stmt)
        except Exception:
            pass


def populate_basket(dc, conn, basket):
    loaded = {}
    for item in basket:
        # Phase 11.basket-blocks: basket now also holds saved library blocks
        # (kind="block") which aren't Oracle tables — skip them in fetch.
        if item.get("kind") == "block" or not item.get("table"):
            continue
        # Scope-derived sql / derived (filter/aggregate) entries carry the
        # alias as their `table` — there's no Oracle table to pull; their data
        # is materialised into DuckDB by the scope build (fetch_cached_tables).
        if item.get("source") in ("sql", "derived"):
            continue
        # Upload-backed basket entries don't fetch via Oracle.
        if item["table"].startswith("upload__"):
            continue
        view = _table_alias(item["table"])
        df = fetch_basket_item(dc, item)
        register_dataframe(conn, view, df)
        loaded[view] = {"table": item["table"], "rows": len(df)}
    return loaded


def list_views(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
    ).fetchall()
    # `__`-önekli ilişkiler dahili defter tutmadır (__dataset_meta, __mat_* geçici
    # register'ları) — LLM data_summary'ye ve blok SQL routing'ine sızmasınlar.
    return [r[0] for r in rows if not str(r[0]).startswith("__")]


def preview_view(conn, view_name, limit=10):
    safe_name = _safe_identifier(view_name)
    df = conn.execute(f"SELECT * FROM {safe_name} LIMIT {int(limit)}").fetchdf()
    total = conn.execute(f"SELECT COUNT(*) FROM {safe_name}").fetchone()[0]
    # `.values.tolist()` ham float('nan')/pd.NaT sızdırıp `json.dumps`'a geçersiz
    # `NaN` yazdırıyordu (client: "Unexpected token 'N'"). Diğer preview yolları gibi
    # hücre-bazlı _jsonable'dan geçir (itertuples kolon dtype'larını korur, .values
    # tek ndarray'e upcast etmez).
    return {
        "columns": list(df.columns),
        "rows": [
            [_jsonable(v) for v in row]
            for row in df.itertuples(index=False, name=None)
        ],
        "row_count": int(total),
    }


def _safe_identifier(name):
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe view name: {name!r}")
    return name


def summarize_views(conn, view_names, sample_rows=5):
    out = {}
    for view in view_names:
        try:
            safe = _safe_identifier(view)
        except ValueError:
            continue
        try:
            cols = conn.execute(f"DESCRIBE {safe}").fetchdf()
            col_list = [
                {"name": str(r["column_name"]), "type": str(r["column_type"])}
                for _, r in cols.iterrows()
            ]
            row_count = int(conn.execute(f"SELECT COUNT(*) FROM {safe}").fetchone()[0])
            sample = []
            if row_count > 0:
                sample_df = conn.execute(f"SELECT * FROM {safe} LIMIT {sample_rows}").fetchdf()
                sample = [
                    [_jsonable(v) for v in row]
                    for row in sample_df.itertuples(index=False, name=None)
                ]
            stats = _compute_stats(conn, safe, col_list, row_count)
            out[view] = {"columns": col_list, "row_count": row_count, "sample": sample, "stats": stats}
        except Exception as exc:
            log.warning("summarize_views: failed for %s: %s", view, exc)
    return out


_NUMERIC_TYPES = ("INT", "BIGINT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL")


def _compute_stats(conn, safe_view, col_list, row_count):
    if row_count == 0:
        return {}
    stats = {}
    for c in col_list:
        ctype = c["type"].upper()
        if not any(t in ctype for t in _NUMERIC_TYPES):
            continue
        col = c["name"]
        if not col.replace("_", "").isalnum():
            continue
        try:
            row = conn.execute(
                f'SELECT MIN("{col}") AS mn, MAX("{col}") AS mx, AVG("{col}") AS av FROM {safe_view}'
            ).fetchone()
            mn, mx, av = row
            argmax_row = conn.execute(
                f'SELECT * FROM {safe_view} WHERE "{col}" = ? LIMIT 1', [mx]
            ).fetchdf()
            argmax = (
                {k: _jsonable(v) for k, v in argmax_row.iloc[0].to_dict().items()}
                if not argmax_row.empty else {}
            )
            stats[col] = {
                "min": _jsonable(mn), "max": _jsonable(mx),
                "avg": _jsonable(av), "argmax": argmax,
            }
        except Exception as exc:
            log.debug("_compute_stats: skipped %s.%s: %s", safe_view, col, exc)
    return stats


def _jsonable(v):
    import math
    import numpy as np
    import pandas as pd
    if v is None:
        return None
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) else v
    # pandas eksik-değer sentinel'leri (nullable Int64/boolean/Arrow -> pd.NA,
    # datetime NULL -> pd.NaT) buraya kadar geliyordu ve str() fallback'ı
    # literal '<NA>'/'NaT' string'i üretiyordu — JSON null olması gerekirken.
    # pd.isna SADECE skalerde güvenli; dizi/Series'te dizi döndürüp boolean
    # bağlamda patlar, o yüzden container tiplerini önce ayıkla.
    if not isinstance(v, (list, tuple, set, dict, np.ndarray, pd.Series, pd.Index)):
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return str(v)


# ════════════════════════════════════════════════════════════════════════════
# UPLOAD ROUTING — Excel sheets become DuckDB views on demand
# ════════════════════════════════════════════════════════════════════════════
#
# Convention for upload table names in SQL:
#   upload__<upload_id>__<sheet_sanitised_name>
#
# The resolver translates this to (s3_key, sheet_display_name). The catalog
# (delivered by the /sources endpoint) carries the mapping; we keep a small
# in-process lookup that's rebuilt from the manifest on each call.

# Regex: catches `upload__u_aBcD12__targets` and friends. Word-boundary safe.
_UPLOAD_REF_RE = re.compile(r"\b(upload__[A-Za-z0-9_]+)\b")


def find_upload_refs(sql: str) -> list[str]:
    """Return all `upload__...` table references in a SQL string."""
    return list(set(_UPLOAD_REF_RE.findall(sql)))


def find_view_refs(sql: str, view_names: list[str]) -> list[str]:
    """Return which of ``view_names`` are referenced as bare identifiers in
    ``sql``.

    Scope datasets — cached Oracle tables, manual-SQL nodes, and aggregate /
    filter / calculated derivations — are each materialised into the session
    DuckDB as a view named by its alias. A block whose SQL touches one of these
    (e.g. ``FROM deposits_by_branch``) must run in DuckDB: derivations have no
    Oracle counterpart, so routing to Oracle would fail. The match is
    case-insensitive and ignores qualified column refs (``t.deposits``) via the
    leading ``.``/word-char guard — same word-boundary spirit as
    ``find_upload_refs``.
    """
    table_names = _table_position_names(sql)
    hits: list[str] = []
    for name in view_names:
        if not name:
            continue
        if table_names is not None:
            # sqlparse SQL'i tablo konumlarına çözebildi: yalnızca gerçek
            # FROM/JOIN tablo adlarıyla eşleştir — literal/yorum/alias içindeki
            # ad artık DuckDB'ye yanlış yönlendirme tetiklemez.
            if name.lower() in table_names:
                hits.append(name)
        else:
            # Ayrıştırma başarısız: eski token-regex davranışına düş ki gerçek
            # FROM/JOIN referansları sessizce kaçırılıp Oracle'a yönlenmesin.
            pat = re.compile(rf"(?<![\w.])({re.escape(name)})(?![\w])", re.IGNORECASE)
            if pat.search(sql):
                hits.append(name)
    return hits


def _table_position_names(sql: str) -> Optional[set[str]]:
    """sqlparse ile ``sql`` içindeki FROM/JOIN konumundaki tablo adlarını
    (küçük harf; takma ad ve şema öneki soyulmuş) topla.

    Yalnızca gerçek tablo konumlarını sayar: tek tırnaklı string literalleri,
    yorumları ve ``AS alias`` takma adlarını yok sayar. Böylece bir scope-view
    adı yalnızca literal/etiket olarak geçtiğinde blok yanlışlıkla DuckDB'ye
    yönlendirilmez. Alt sorgu / CTE gövdelerine de iner. Ayrıştırma başarısız
    olursa ``None`` döner → çağıran eski regex davranışına düşer (gerçek
    referansları kaçırmamak için).
    """
    try:
        parsed = sqlparse.parse(sql)
    except Exception:
        return None
    if not parsed:
        return None
    names: set[str] = set()

    def _add(tok):
        if isinstance(tok, _sql.Identifier):
            real = tok.get_real_name()  # takma adı ve şema önekini soyar
            if real:
                names.add(real.lower())
        elif isinstance(tok, _sql.IdentifierList):
            for sub in tok.get_identifiers():
                _add(sub)
        elif tok.ttype in (_tok.Name,):
            names.add(tok.value.lower())

    def _is_comment(tok):
        return isinstance(tok, _sql.Comment) or (
            tok.ttype is not None and tok.ttype in _tok.Comment)

    def _walk(tokens):
        expecting = False  # bir önceki anlamlı token FROM/JOIN mıydı?
        for tok in tokens:
            if tok.is_whitespace:
                continue
            # Yorumları yok say — FROM/JOIN ile tablo adı ARASINDA bir yorum
            # (`FROM /*c*/ positions`) `expecting` durumunu tüketip gerçek
            # tabloyu kaçırmamalı (aksi halde blok yanlışlıkla Oracle'a yönlenir).
            if _is_comment(tok):
                continue
            if expecting:
                if isinstance(tok, (_sql.Identifier, _sql.IdentifierList)):
                    _add(tok)
                elif isinstance(tok, _sql.Parenthesis):
                    _walk(tok.tokens)  # alt sorgu: içeride yine FROM/JOIN ara
                elif tok.ttype in (_tok.Name,):
                    names.add(tok.value.lower())
                expecting = False
            if tok.ttype is _tok.Keyword and tok.normalized == "FROM":
                expecting = True
                continue
            if tok.ttype is _tok.Keyword and "JOIN" in tok.normalized:
                expecting = True
                continue
            if tok.is_group:
                _walk(tok.tokens)

    for stmt in parsed:
        _walk(stmt.tokens)
    return names


def execute_with_binds(conn, sql: str, params: dict | None):
    """Run ``sql`` against DuckDB, translating the binder's ``:name`` placeholders
    (native to Oracle) to DuckDB's ``$name``. Empty params → plain execute."""
    if not params:
        return conn.execute(sql).fetchdf()
    duck_sql = sql
    # Longest key first so ``:currency`` doesn't clobber ``:currency_list``.
    for k in sorted(params, key=len, reverse=True):
        duck_sql = re.sub(rf"(?<![\w$]):{re.escape(k)}\b", f"${k}", duck_sql)
    return conn.execute(duck_sql, params).fetchdf()


def run_block_sql_routed(
    dc, conn, block_id: str, sql: str, params: dict | None = None,
    *,
    upload_lookup: Optional[dict] = None,
    s3_get: Optional[Callable[[str], bytes]] = None,
    extra_view_names: Optional[list[str]] = None,
):
    """Execute already-validated, concept-stripped block SQL on the right engine
    and return ``(df, engine_label)``.

    DuckDB when the SQL references a scope dataset view (cached table, manual-SQL
    node, or derivation — none of which exist in Oracle) or an upload; otherwise
    Oracle via DataClient. This is the routing behind both the chart/preview path
    (``execute_block_sql``) and the manual-run path, so a block can source from
    Hazırlık-produced nodes by alias.

    ``extra_view_names`` lets the caller add scope basket aliases that *should*
    route to DuckDB even when not currently registered (not yet materialised) —
    so the user gets a clear "table does not exist" from DuckDB instead of a
    misleading Oracle ORA-00942.
    """
    upload_refs = find_upload_refs(sql)
    names = [v for v in list_views(conn) if not v.startswith("block_preview_")]
    if extra_view_names:
        names = list({*names, *extra_view_names})
    view_refs = find_view_refs(sql, names)
    if upload_refs or view_refs:
        if upload_refs:
            if upload_lookup is None or s3_get is None:
                raise RuntimeError(
                    "Excel referansı içeren SQL için upload_lookup ve s3_get gerekli."
                )
            ensure_upload_views(conn, upload_refs, upload_lookup, s3_get)
        try:
            return execute_with_binds(conn, sql, params), "duckdb"
        except Exception as exc:
            raise RuntimeError(f"DuckDB SQL hatası: {exc}") from exc
    df = dc.get_data(
        base_prefix=None, dataset=f"block::{block_id}",
        query=sql, query_params=params or {},
    )
    return df, "oracle"


def ensure_upload_views(
    conn: "duckdb.DuckDBPyConnection",
    refs: list[str],
    upload_lookup: dict,
    s3_get: Callable[[str], bytes],
) -> dict:
    """For each upload reference, register the corresponding Excel sheet as a
    DuckDB view if it isn't already.

    `upload_lookup` maps `upload_id → {s3_key, sheets: {sheet_sanitised_name → display_name}}`.

    Returns a dict of `{ref: row_count}` for what was loaded (or already loaded).
    """
    import pandas as pd

    loaded = {}
    for ref in refs:
        # Parse `upload__<id>__<sheet>` → (id, sheet)
        upload_id, sheet_key = _split_upload_ref(ref)

        info = upload_lookup.get(upload_id)
        if not info:
            raise ValueError(f"Upload bulunamadı: {upload_id}")
        sheet_display = info["sheets"].get(sheet_key)
        if sheet_display is None:
            raise ValueError(f"Sheet bulunamadı: {ref}")

        # Already loaded?
        existing = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            [ref],
        ).fetchone()
        if existing:
            loaded[ref] = -1   # marker: was cached
            continue

        # Fetch xlsx bytes from S3 and read the requested sheet
        try:
            blob = s3_get(info["s3_key"])
        except Exception as exc:
            raise RuntimeError(f"Excel S3'ten okunamadı ({ref}): {exc}") from exc

        df = pd.read_excel(io.BytesIO(blob), sheet_name=sheet_display, engine="openpyxl")
        # Rename columns to match the catalog's sanitised names so SQL works.
        if "columns_sanitised" in info and ref in info["columns_sanitised"]:
            df.columns = info["columns_sanitised"][ref]
        register_dataframe(conn, ref, df)
        loaded[ref] = len(df)
        log.info("duck.ensure_upload_views: registered %s (%d rows)", ref, len(df))

    return loaded


def _split_upload_ref(ref: str) -> tuple[str, str]:
    """`upload__u_aBcD12__targets` → ('u_aBcD12', 'targets')"""
    if not ref.startswith("upload__"):
        raise ValueError(f"Bad ref: {ref!r}")
    rest = ref[len("upload__"):]
    # `u_aBcD12__targets` — split on first `__`
    parts = rest.split("__", 1)
    if len(parts) != 2:
        raise ValueError(f"Bad ref: {ref!r}")
    return parts[0], parts[1]


def build_upload_lookup(manifest: dict) -> dict:
    """Rebuild the in-memory upload lookup from manifest.uploads."""
    lookup: dict = {}
    for u in (manifest.get("uploads") or []):
        upload_id = u.get("id")
        if not upload_id:
            continue
        sheets_map = {}
        columns_sanitised: dict = {}
        for sheet in u.get("sheets") or []:
            sheets_map[sheet["name"]] = sheet.get("display_name") or sheet["name"]
            ref = f"upload__{upload_id}__{sheet['name']}"
            columns_sanitised[ref] = [c["name"] for c in sheet.get("columns") or []]
        lookup[upload_id] = {
            "s3_key": u.get("s3_key"),
            "sheets": sheets_map,
            "columns_sanitised": columns_sanitised,
        }
    return lookup


# ════════════════════════════════════════════════════════════════════════════
# BLOCK-DRIVEN PATH — Oracle OR DuckDB-only
# ════════════════════════════════════════════════════════════════════════════

def _block_view_name(block_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", block_id).lower()
    if not safe or safe[0].isdigit():
        safe = "b_" + safe
    return f"block_{safe}"


_MAX_ROWS_IN_MANIFEST = MAX_RAW_ROWS


def infer_column_kinds(df) -> dict:
    """Kolon adı → görsel tip ('number' | 'date' | 'text') — pandas dtype'tan.
    DataTable bloğu sayıları sağa yaslayıp tr-TR formatlar, tarihleri kısaltır;
    tip bilgisi olmadan her hücre düz metin görünüyordu."""
    import pandas as pd

    kinds: dict = {}
    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype):
            kinds[str(col)] = "date"
        elif pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype):
            kinds[str(col)] = "number"
        else:
            kinds[str(col)] = "text"
    return kinds


def execute_block_sql(
    dc,
    conn,
    block_id: str,
    sql: str,
    sample_rows: int = 5,
    *,
    upload_lookup: Optional[dict] = None,
    s3_get: Optional[Callable[[str], bytes]] = None,
):
    """Validate + execute SQL, returning a data_source dict.

    Routing:
      - SQL contains `upload__...` references → DuckDB-only. We pre-load each
        referenced sheet into DuckDB, then run SQL there.
      - Otherwise → Oracle via DataClient (original behaviour).
    """
    import pandas as pd

    from presentations.concepts.integration import strip_concept_sentinel

    # Decide the execution engine BEFORE wrapping. Scope datasets are
    # registered in this session's DuckDB as views named by their alias, so a
    # block touching one of them — a cached table, a manual-SQL node, or a
    # derivation (which has no Oracle counterpart at all) — must run in DuckDB.
    # This also drives the row-cap dialect: Oracle wraps with `WHERE ROWNUM<=N`,
    # DuckDB with `LIMIT N`; wrapping with the wrong one fails at execution.
    # `block_preview_*` are internal helpers, not real scope datasets.
    upload_refs = find_upload_refs(sql)
    scope_views = [v for v in list_views(conn) if not v.startswith("block_preview_")]
    view_refs = find_view_refs(sql, scope_views)
    is_duckdb_only = bool(upload_refs) or bool(view_refs)

    gate: GateResult = validate_and_wrap(
        sql, dialect="duckdb" if is_duckdb_only else None,
    )

    # A block may carry the Phase-7 `{{concept_filters}}` sentinel in its SQL.
    # The concept-apply path substitutes it with real predicates; every OTHER
    # execution path (manual refresh, chat-node re-run, /execute route) reaches
    # here WITHOUT that substitution, so the literal token would hit Oracle as
    # `WHERE {{concept_filters}}` → ORA-00936 "missing expression". Neutralize it
    # to a no-op `1 = 1` for execution, while keeping the authored SQL (sentinel
    # intact) in `original_sql` so future concept injection still works. Mirrors
    # run_block_manual's strip-at-exec pattern.
    exec_sql = strip_concept_sentinel(gate.sql)

    log.info(
        "duck.execute_block_sql: block=%s gate=%s rewritten=%s truncated=%s cap=%d",
        block_id, gate.reason, gate.rewritten, gate.truncated, gate.cap,
    )

    if is_duckdb_only:
        if upload_refs:
            if upload_lookup is None or s3_get is None:
                raise RuntimeError(
                    "Excel referansı içeren SQL için upload_lookup ve s3_get gerekli."
                )
            ensure_upload_views(conn, upload_refs, upload_lookup, s3_get)
        # Run the SQL directly against DuckDB (scope views + any uploads).
        try:
            df = conn.execute(exec_sql).fetchdf()
        except Exception as exc:
            raise RuntimeError(f"DuckDB SQL hatası: {exc}") from exc
    else:
        df = dc.get_data(
            base_prefix=None,
            dataset=f"block::{block_id}",
            query=exec_sql,
            query_params={},
        )

    if df is None:
        df = pd.DataFrame()
    df = df.reset_index(drop=True) if hasattr(df, "reset_index") else df

    # DuckDB.register reddediyor 0-column DataFrame'leri — empty sonuçta
    # crash etmesin, view register'ı atlanır, downstream rows=[] ile devam eder.
    view_name = _block_view_name(block_id)
    if len(df.columns) > 0:
        register_dataframe(conn, view_name, df)
    else:
        log.info("duck.execute_block_sql: block=%s empty result (no columns), skip register", block_id)

    columns = [str(c) for c in df.columns]
    total_rows = int(len(df))

    all_rows = []
    if total_rows > 0:
        rows_for_manifest = df.head(_MAX_ROWS_IN_MANIFEST)
        all_rows = [
            [_jsonable(v) for v in row]
            for row in rows_for_manifest.itertuples(index=False, name=None)
        ]

    sample = all_rows[:sample_rows]

    engine_label = "duckdb" if is_duckdb_only else "oracle"
    log.info(
        "duck.execute_block_sql: block=%s engine=%s -> %d rows, view=%s",
        block_id, engine_label, total_rows, view_name,
    )

    return {
        "sql":          exec_sql,
        "original_sql": gate.original_sql,
        "rewritten":    gate.rewritten,
        "truncated":    gate.truncated,
        "cap":          gate.cap,
        "reason":       gate.reason,
        "executed_at":  _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "row_count":    total_rows,
        "columns":      columns,
        "column_types": infer_column_kinds(df),
        "preview_rows": sample,
        "rows":         all_rows,
        "view_name":    view_name,
        "engine":       engine_label,
    }


def drop_block_view(conn, block_id):
    try:
        conn.unregister(_block_view_name(block_id))
    except Exception:
        pass
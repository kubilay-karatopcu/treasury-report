"""
Aggregation gate — Oracle/DuckDB SQL validator and safety wrapper.

Same policy as before; new optional `dialect` parameter selects the LIMIT
syntax for the wrap path:

  dialect="oracle"  → `WHERE ROWNUM <= N` (default, backwards compatible)
  dialect="duckdb"  → `LIMIT N`

The validator AUTODETECTS DuckDB if the SQL contains an `upload__...` reference
(Excel sheet), so the LLM/caller doesn't have to pass anything new in the
common case.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_RAW_ROWS = 5000

_AGG_FUNCTIONS = (
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "STDDEV", "VARIANCE", "MEDIAN", "LISTAGG",
    "PERCENTILE_CONT", "PERCENTILE_DISC",
    "FIRST_VALUE", "LAST_VALUE",
    "CORR", "COVAR_POP", "COVAR_SAMP",
    "REGR_SLOPE", "REGR_INTERCEPT",
)

_FORBIDDEN_STARTS = (
    "INSERT", "UPDATE", "DELETE", "MERGE",
    "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "COMMIT", "ROLLBACK",
    "BEGIN", "DECLARE", "EXEC", "EXECUTE", "CALL",
)

# Same pattern as duck.find_upload_refs — keep in sync (small duplication, no cycle).
_UPLOAD_REF_RE = re.compile(r"\bupload__[A-Za-z0-9_]+\b")


@dataclass
class GateResult:
    sql: str
    original_sql: str
    truncated: bool
    cap: int
    reason: str
    rewritten: bool
    dialect: str = "oracle"     # New in this version.


class GateError(ValueError):
    pass


def _strip_trailing_punctuation(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _is_safe_select(sql_no_comments: str) -> bool:
    stripped = sql_no_comments.strip().lstrip("(").strip()
    if not stripped:
        return False
    first_word = re.match(r"\s*(\w+)", stripped)
    if not first_word:
        return False
    head = first_word.group(1).upper()
    if head in _FORBIDDEN_STARTS:
        return False
    if head not in ("SELECT", "WITH"):
        return False
    if ";" in sql_no_comments:
        return False
    return True


def _has_aggregation(sql_no_comments_upper: str) -> bool:
    if re.search(r"\bGROUP\s+BY\b", sql_no_comments_upper):
        return True
    if re.search(r"\bSELECT\s+DISTINCT\b", sql_no_comments_upper):
        return True
    for fn in _AGG_FUNCTIONS:
        if re.search(rf"\b{fn}\s*\(", sql_no_comments_upper):
            return True
    return False


def _find_fetch_first_limit(sql_no_comments_upper: str) -> int | None:
    m = re.search(
        r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)\s+ROWS?\s+ONLY\b",
        sql_no_comments_upper,
    )
    return int(m.group(1)) if m else None


def _find_rownum_limit(sql_no_comments_upper: str) -> int | None:
    m = re.search(r"\bROWNUM\s*<=?\s*(\d+)\b", sql_no_comments_upper)
    return int(m.group(1)) if m else None


def _find_limit_clause(sql_no_comments_upper: str) -> int | None:
    """DuckDB-style trailing LIMIT N."""
    m = re.search(r"\bLIMIT\s+(\d+)\b", sql_no_comments_upper)
    return int(m.group(1)) if m else None


def _detect_dialect(sql: str, override: str | None) -> str:
    if override in ("oracle", "duckdb"):
        return override
    if _UPLOAD_REF_RE.search(sql):
        return "duckdb"
    return "oracle"


def validate_and_wrap(sql: str, dialect: str | None = None) -> GateResult:
    """Main entrypoint. Validates SQL, wraps if needed.
    `dialect` is auto-detected from the SQL when not provided."""
    if not isinstance(sql, str) or not sql.strip():
        raise GateError("SQL boş olamaz.")

    original_sql = sql.strip()
    cleaned = _strip_trailing_punctuation(original_sql)
    sql_no_comments = _strip_sql_comments(cleaned)
    sql_upper = sql_no_comments.upper()

    if not _is_safe_select(sql_no_comments):
        raise GateError(
            "Sadece tek bir SELECT (veya WITH ... SELECT) sorgusu çalıştırılabilir. "
            "DDL/DML/çoklu ifade kabul edilmiyor."
        )

    resolved_dialect = _detect_dialect(cleaned, dialect)

    # Path 1: aggregation → trust.
    if _has_aggregation(sql_upper):
        return GateResult(
            sql=cleaned, original_sql=original_sql, truncated=False,
            cap=MAX_RAW_ROWS,
            reason="Aggregation tespit edildi; sorgu olduğu gibi çalıştırılıyor.",
            rewritten=cleaned != original_sql, dialect=resolved_dialect,
        )

    # Path 2: explicit limit (different syntax per dialect).
    if resolved_dialect == "duckdb":
        declared_limit = _find_limit_clause(sql_upper) or _find_fetch_first_limit(sql_upper)
    else:
        fetch_n = _find_fetch_first_limit(sql_upper)
        rownum_n = _find_rownum_limit(sql_upper)
        if fetch_n is not None and rownum_n is not None:
            declared_limit = min(fetch_n, rownum_n)
        elif fetch_n is not None:
            declared_limit = fetch_n
        elif rownum_n is not None:
            declared_limit = rownum_n
        else:
            declared_limit = None

    if declared_limit is not None and declared_limit <= MAX_RAW_ROWS:
        return GateResult(
            sql=cleaned, original_sql=original_sql, truncated=False,
            cap=declared_limit,
            reason=f"Sorgu en fazla {declared_limit} satır döndürür.",
            rewritten=cleaned != original_sql, dialect=resolved_dialect,
        )

    # Path 3 / 4: raw or oversized → wrap. Different syntax per dialect.
    if resolved_dialect == "duckdb":
        wrapped = f"SELECT * FROM (\n{cleaned}\n) LIMIT {MAX_RAW_ROWS}"
    else:
        wrapped = f"SELECT * FROM (\n{cleaned}\n) WHERE ROWNUM <= {MAX_RAW_ROWS}"

    if declared_limit is not None and declared_limit > MAX_RAW_ROWS:
        reason = (
            f"Sorgu {declared_limit} satır talep ediyor; "
            f"güvenlik için ilk {MAX_RAW_ROWS} satıra indirildi."
        )
    else:
        reason = (
            f"Aggregation veya limit yok; ilk {MAX_RAW_ROWS} satır gösteriliyor."
        )

    return GateResult(
        sql=wrapped, original_sql=original_sql, truncated=True,
        cap=MAX_RAW_ROWS, reason=reason, rewritten=True,
        dialect=resolved_dialect,
    )
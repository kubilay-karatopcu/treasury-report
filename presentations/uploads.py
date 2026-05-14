"""
Excel & paste-data parsing for the presentations module.

Handles both:
- Multi-sheet .xlsx uploads (user picks a file)
- TSV-style clipboard paste from Excel (user copies a range, pastes into modal)

Both paths produce the same shape:
    {
        "filename": str,                       # original or user-given name
        "sheets": [
            {
                "name": str,                   # sheet name (sanitised at SQL boundary)
                "display_name": str,           # what to show in UI
                "row_count": int,
                "columns": [
                    {
                        "name": str,           # sanitised — used in SQL
                        "display_name": str,   # original — shown in UI
                        "type": "NUMBER"|"DATE"|"VARCHAR",
                        "nullable": bool,
                    }
                ],
                "preview_rows": [[v, ...]],    # first 10 rows, JSON-safe
            }
        ]
    }

After save_upload(), the file lives in S3 under
    prisma-treasury/uploads/<user_id>/u_<id>.xlsx
and the metadata is stored on the manifest as manifest.uploads[].
"""
from __future__ import annotations

import io
import logging
import re
import secrets
from typing import BinaryIO

import pandas as pd

log = logging.getLogger(__name__)


# ── Limits (decided with the user) ───────────────────────────────────────────

MAX_UPLOAD_BYTES = 10 * 1024 * 1024     # 10 MB hard cap
MAX_SHEETS_PER_FILE = 5                 # First 5 sheets only, rest ignored
PREVIEW_ROW_COUNT = 10                  # First 10 rows shown in UI


# ── Public IDs ───────────────────────────────────────────────────────────────

def new_upload_id() -> str:
    """`u_<10-char-alnum>` — short, URL-safe, ~57 bits of entropy.
    Alphanumeric only (no `_` or `-`) so the splitter in
    `upload__<id>__<sheet>` is unambiguous on the first `__`."""
    import string
    alphabet = string.ascii_letters + string.digits
    token = "".join(secrets.choice(alphabet) for _ in range(10))
    return "u_" + token


# ── Column / sheet name sanitisation ─────────────────────────────────────────

_TURKISH_MAP = str.maketrans({
    "ç": "c", "Ç": "C",
    "ğ": "g", "Ğ": "G",
    "ı": "i", "İ": "I",
    "ö": "o", "Ö": "O",
    "ş": "s", "Ş": "S",
    "ü": "u", "Ü": "U",
})


def sanitise_identifier(raw: str, fallback: str = "col") -> str:
    """Convert an arbitrary string into a safe SQL identifier.

      "Müşteri ID"  → "MUSTERI_ID"
      "Tarih (Ay)"  → "TARIH_AY"
      "2026 Q4"     → "C_2026_Q4"      (leading digit gets a c_ prefix)
      ""            → "COL"            (fallback)
    """
    if not isinstance(raw, str) or not raw.strip():
        return fallback.upper()

    s = raw.strip().translate(_TURKISH_MAP)
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return fallback.upper()
    if s[0].isdigit():
        s = "C_" + s
    return s.upper()


def dedupe_names(names: list[str]) -> list[str]:
    """Append _1, _2, ... to repeated names so SQL doesn't collapse them."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen[n] = 0
            out.append(n)
        else:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
    return out


def next_paste_name(existing_names: set[str], base: str = "yapistirilan") -> str:
    """Compute the next free 'Yapıştırılan Tablo' / '..._1' / '..._2' name.
    Used when the user pastes without supplying a name."""
    if base not in existing_names:
        return base
    i = 1
    while f"{base}_{i}" in existing_names:
        i += 1
    return f"{base}_{i}"


# ── Type inference ───────────────────────────────────────────────────────────

def _infer_type(series: pd.Series) -> str:
    """Map a pandas Series to one of {NUMBER, DATE, VARCHAR}."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return "DATE"
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return "NUMBER"
    return "VARCHAR"


# ── JSON-safe serialisation of cell values ───────────────────────────────────

def _jsonable(v):
    """Coerce pandas/numpy/datetime scalars to plain Python primitives."""
    import math
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        return v
    # pandas / numpy / datetime
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return str(v)


# ── Header detection ─────────────────────────────────────────────────────────

def looks_like_header(first_row: list, second_row: list | None) -> bool:
    """Heuristic: True if first row looks like labels and second row looks like data.

    Rules of thumb (all must hold):
    - First row has no nulls
    - First row is mostly strings (>50%)
    - Second row (if present) has at least one cell of a different type than
      the corresponding first-row cell
    """
    if not first_row:
        return False
    if any(v is None or (isinstance(v, float) and v != v) for v in first_row):
        return False

    string_share = sum(1 for v in first_row if isinstance(v, str)) / len(first_row)
    if string_share < 0.5:
        return False

    if second_row is None:
        return True   # only one row, default to "yes header" — caller can override

    type_differs = False
    for a, b in zip(first_row, second_row):
        if b is None or (isinstance(b, float) and b != b):
            continue
        if isinstance(a, str) and not isinstance(b, str):
            type_differs = True
            break
        if not isinstance(a, str) and isinstance(b, str):
            type_differs = True
            break

    return type_differs or string_share >= 0.8


# ── Excel parser ─────────────────────────────────────────────────────────────

def parse_xlsx(file_bytes: bytes) -> list[dict]:
    """Parse an .xlsx blob into a list of sheet dicts.

    Caps at MAX_SHEETS_PER_FILE; remaining sheets are dropped with a log warning.
    Raises ValueError on a clearly malformed file.
    """
    if len(file_bytes) == 0:
        raise ValueError("Boş dosya.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Dosya çok büyük: {len(file_bytes) // 1024 // 1024} MB "
            f"(maksimum {MAX_UPLOAD_BYTES // 1024 // 1024} MB)."
        )

    try:
        xf = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    except Exception as exc:
        raise ValueError(f"Excel dosyası okunamadı: {exc}") from exc

    sheet_names = xf.sheet_names[:MAX_SHEETS_PER_FILE]
    dropped = len(xf.sheet_names) - len(sheet_names)
    if dropped > 0:
        log.info("parse_xlsx: %d sheet ignored (cap=%d)", dropped, MAX_SHEETS_PER_FILE)

    sheets = []
    seen_sheet_names: set[str] = set()
    for sheet_name in sheet_names:
        try:
            df = pd.read_excel(xf, sheet_name=sheet_name)
        except Exception as exc:
            log.warning("parse_xlsx: failed to read sheet %r: %s", sheet_name, exc)
            continue
        sheets.append(_sheet_to_dict(df, sheet_name, seen_sheet_names))
    return sheets


def _sheet_to_dict(df: pd.DataFrame, sheet_name: str, seen_sheet_names: set[str]) -> dict:
    """Convert a DataFrame to the unified sheet shape."""
    raw_cols = [str(c) for c in df.columns]
    sanitised = [sanitise_identifier(c, fallback=f"COL_{i+1}") for i, c in enumerate(raw_cols)]
    sanitised = dedupe_names(sanitised)

    sheet_display = sheet_name
    sheet_sanitised = sanitise_identifier(sheet_name, fallback="SHEET").lower()
    # Avoid collisions across sheets in the same file.
    base = sheet_sanitised
    i = 1
    while sheet_sanitised in seen_sheet_names:
        sheet_sanitised = f"{base}_{i}"
        i += 1
    seen_sheet_names.add(sheet_sanitised)

    columns = [
        {
            "name":         sanitised[i],
            "display_name": raw_cols[i],
            "type":         _infer_type(df.iloc[:, i]),
            "nullable":     bool(df.iloc[:, i].isnull().any()),
        }
        for i in range(len(raw_cols))
    ]

    preview = df.head(PREVIEW_ROW_COUNT)
    preview_rows = [
        [_jsonable(v) for v in row]
        for row in preview.itertuples(index=False, name=None)
    ]

    return {
        "name":         sheet_sanitised,
        "display_name": sheet_display,
        "row_count":    int(len(df)),
        "columns":      columns,
        "preview_rows": preview_rows,
    }


# ── TSV (paste-from-Excel) parser ────────────────────────────────────────────

def parse_pasted_tsv(
    raw_text: str,
    *,
    table_name: str,
    has_header: bool | None = None,
) -> dict:
    """Parse Excel-paste output (tab-separated values).

    Returns a single-sheet dict in the same shape as parse_xlsx() sheets, with
    the sheet name = sanitised table_name.

    `has_header=None` triggers auto-detection.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("Yapıştırılan içerik boş.")

    # Excel uses \r\n or \n; rows separated by newline, cells by \t.
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    rows = [line.split("\t") for line in raw_text.split("\n")]
    if not rows or not rows[0]:
        raise ValueError("Yapıştırılan içerik ayrıştırılamadı.")

    # Normalise widths — pad short rows with None
    width = max(len(r) for r in rows)
    rows = [r + [None] * (width - len(r)) for r in rows]

    # Coerce-then-rebuild a DataFrame so we get pandas type inference.
    if has_header is None:
        # Auto-detect: cast first row to strings, second row to coerced types
        first = rows[0]
        second = _coerce_row(rows[1]) if len(rows) > 1 else None
        has_header = looks_like_header(first, second)

    if has_header:
        header_row = rows[0]
        data_rows = rows[1:]
    else:
        header_row = [f"col_{i+1}" for i in range(width)]
        data_rows = rows

    df = pd.DataFrame(data_rows, columns=header_row)

    # Coerce each column: try numeric → datetime → leave as string
    for col in df.columns:
        df[col] = _coerce_series(df[col])

    return _sheet_to_dict(
        df,
        sheet_name=table_name or "yapistirilan",
        seen_sheet_names=set(),
    )


def _coerce_row(row: list) -> list:
    """Best-effort cast of a single row's cells (used for auto-header check)."""
    out = []
    for v in row:
        if v is None or v == "":
            out.append(None)
            continue
        # Try int → float → datetime → string
        try:
            out.append(int(v))
            continue
        except (TypeError, ValueError):
            pass
        try:
            out.append(float(str(v).replace(",", ".")))
            continue
        except (TypeError, ValueError):
            pass
        out.append(str(v))
    return out


def _coerce_series(s: pd.Series) -> pd.Series:
    """Best-effort cast of an entire string Series. Numeric > datetime > text."""
    # Empty cells → NaN
    s = s.replace({"": None})

    # Try numeric (handles both "1234" and "1.234,56" → 1234.56 with Turkish locale)
    numeric_attempt = pd.to_numeric(
        s.astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
        errors="coerce",
    )
    # Apply only if a high share of non-null values were successfully cast
    non_null_input = s.notna().sum()
    if non_null_input > 0:
        cast_rate = numeric_attempt.notna().sum() / non_null_input
        if cast_rate >= 0.8:
            return numeric_attempt

    # Try datetime
    dt_attempt = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if non_null_input > 0:
        cast_rate = dt_attempt.notna().sum() / non_null_input
        if cast_rate >= 0.8:
            return dt_attempt

    return s.astype(str).where(s.notna(), None)


# ── Save / load / delete (S3-backed) ─────────────────────────────────────────
#
# These are stubs: pass the function the bytes you want to write, and a
# `s3_writer` callable that does `(key, body_bytes) -> None`. The caller wires
# it to DataClient (or wherever S3 actually lives).
#
# Read-side is symmetric: `s3_reader(key) -> bytes`.

def upload_s3_key(user_id: str, upload_id: str) -> str:
    """The canonical S3 key for a given user's upload."""
    return f"prisma-treasury/uploads/{user_id}/{upload_id}.xlsx"


def df_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Sheet1") -> bytes:
    """Serialise a DataFrame to .xlsx bytes (used for the paste path —
    we normalise pasted TSV → xlsx so the storage shape is uniform)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=sheet_name[:31] or "Sheet1", index=False)
    return buf.getvalue()


def load_sheet_from_xlsx_bytes(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    """Read a single sheet from xlsx bytes — used at SQL-execution time."""
    return pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet_name, engine="openpyxl")
"""generate_table_docs.py — bootstrap TableDoc YAMLs from Oracle metadata.

Pulls ALL_TABLES, ALL_TAB_COLUMNS, ALL_TAB_COMMENTS, ALL_COL_COMMENTS, and
ALL_PART_KEY_COLUMNS for the given owners (default: EDW, HIST, A16438) and
writes one Phase 6.5.b-shaped YAML per table to:

    presentations/catalog/tables/<SCHEMA>/<TABLE>.yaml

Each YAML carries:
  - table, schema
  - description       (from ALL_TAB_COMMENTS, if any — else absent)
  - partition_column  (from ALL_PART_KEY_COLUMNS, COLUMN_POSITION=1, if any)
  - columns:
      <COL>:
        type:        formatted Oracle type, e.g. VARCHAR2(20), NUMBER(18,2)
        description: from ALL_COL_COMMENTS, if any

Filterability hints, semantic tags, lookups, and concept_bindings are LEFT
EMPTY — the data team fills them in by hand on top of this backbone.

Default behavior is **skip-if-exists** so re-running never clobbers a hand-
edited YAML; pass --overwrite to force a rebuild.

Invocation (corporate machine where DataClient can reach Oracle):

    python -m jobs.generate_table_docs                          # all default owners
    python -m jobs.generate_table_docs --owners EDW             # one owner
    python -m jobs.generate_table_docs --schema EDW --table FOO # one table
    python -m jobs.generate_table_docs --dry-run                # preview only
    python -m jobs.generate_table_docs --overwrite              # rebuild existing
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from presentations.table_docs.schema import ColumnDoc, TableDoc  # noqa: E402


log = logging.getLogger("jobs.generate_table_docs")


DEFAULT_OWNERS: tuple[str, ...] = ("EDW", "HIST", "A16438")
OUTPUT_BASE = _ROOT / "presentations" / "catalog" / "tables"

_OWNER_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")
_COLNAME_RE = re.compile(r"^[A-Z_][A-Z0-9_$#]*$")


# ── YAML serializer (drops default fields for clean output) ───────────────
# We don't reuse presentations.table_docs.store._serialise because that one
# preserves Pydantic defaults (e.g. ``filterable: false``, ``visible_in_ui:
# true``). For a freshly-generated backbone YAML we want only the fields the
# user cares about: table, schema, description, partition_column, columns →
# {type, description}. Filter affordances, semantic tags, lookups stay
# absent — the user adds them by hand when relevant.

def _serialise_clean(doc: TableDoc) -> bytes:
    payload = doc.model_dump(
        by_alias=True,
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    )
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).encode("utf-8")


# ── Oracle metadata queries ───────────────────────────────────────────────
# Owners are validated against _OWNER_RE before interpolation, so the literal
# IN-list is safe. Bind variables don't compose cleanly with IN-lists in
# oracledb without per-item placeholders; literal is simpler here.

def _owners_in_clause(owners: tuple[str, ...]) -> str:
    return ", ".join(f"'{o}'" for o in owners)


def _sql_tables(owners: tuple[str, ...]) -> str:
    return (
        "SELECT OWNER, TABLE_NAME, NUM_ROWS, PARTITIONED "
        "FROM ALL_TABLES "
        f"WHERE OWNER IN ({_owners_in_clause(owners)}) "
        "ORDER BY OWNER, TABLE_NAME"
    )


def _sql_columns(owners: tuple[str, ...]) -> str:
    # ALL_TAB_COLS surfaces hidden / system-generated columns (SYS_NCxxxx$
    # for function-based indexes, etc.). ALL_TAB_COLUMNS hides them by
    # default — use that, and the result is clean.
    return (
        "SELECT OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
        "       DATA_LENGTH, DATA_PRECISION, DATA_SCALE, COLUMN_ID "
        "FROM ALL_TAB_COLUMNS "
        f"WHERE OWNER IN ({_owners_in_clause(owners)}) "
        "ORDER BY OWNER, TABLE_NAME, COLUMN_ID"
    )


def _sql_tab_comments(owners: tuple[str, ...]) -> str:
    return (
        "SELECT OWNER, TABLE_NAME, COMMENTS "
        "FROM ALL_TAB_COMMENTS "
        f"WHERE OWNER IN ({_owners_in_clause(owners)})"
    )


def _sql_col_comments(owners: tuple[str, ...]) -> str:
    return (
        "SELECT OWNER, TABLE_NAME, COLUMN_NAME, COMMENTS "
        "FROM ALL_COL_COMMENTS "
        f"WHERE OWNER IN ({_owners_in_clause(owners)})"
    )


def _sql_partition_keys(owners: tuple[str, ...]) -> str:
    # COLUMN_POSITION=1 picks the leading partition key. TableDoc supports a
    # single partition_column, so composites just use the first column.
    return (
        "SELECT OWNER, NAME AS TABLE_NAME, COLUMN_NAME "
        "FROM ALL_PART_KEY_COLUMNS "
        f"WHERE OWNER IN ({_owners_in_clause(owners)}) "
        "  AND OBJECT_TYPE = 'TABLE' "
        "  AND COLUMN_POSITION = 1"
    )


# ── Type formatting ───────────────────────────────────────────────────────

def format_oracle_type(
    data_type: str | None,
    length: int | float | None,
    precision: int | float | None,
    scale: int | float | None,
) -> str:
    """Render an ALL_TAB_COLUMNS row into a canonical Oracle type string.

    Examples:
        VARCHAR2 / 20 / None / None  → "VARCHAR2(20)"
        NUMBER   / *  / 18   / 2     → "NUMBER(18,2)"
        NUMBER   / *  / 18   / 0     → "NUMBER(18)"
        NUMBER   / *  / None / None  → "NUMBER"
        DATE                          → "DATE"
        TIMESTAMP(6)                  → "TIMESTAMP(6)"  (Oracle already serialises it)
    """
    dt = (data_type or "").upper().strip()
    if not dt:
        return "UNKNOWN"

    def _i(v) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    L = _i(length)
    P = _i(precision)
    S = _i(scale)

    if dt in ("VARCHAR2", "VARCHAR", "CHAR", "NCHAR", "NVARCHAR2"):
        return f"{dt}({L})" if L else dt
    if dt == "NUMBER":
        if P is None:
            return "NUMBER"
        if S is None or S == 0:
            return f"NUMBER({P})"
        return f"NUMBER({P},{S})"
    if dt == "FLOAT":
        return f"FLOAT({P})" if P else "FLOAT"
    if dt == "RAW":
        return f"RAW({L})" if L else "RAW"

    # DATE / TIMESTAMP(N) / CLOB / BLOB / BFILE / ROWID / LONG / INTERVAL ... /
    # XMLTYPE / user-defined ADTs: pass through. ColumnDoc.type caps at 64 chars.
    return dt[:64]


# ── TableDoc construction ─────────────────────────────────────────────────

def build_table_doc(
    owner: str,
    table: str,
    columns_rows: list[dict],
    table_comment: str | None,
    column_comments: dict[str, str | None],
    partition_column: str | None,
) -> TableDoc | None:
    """Construct a TableDoc from the raw row sets. Returns None if the table
    has no readable columns (dropped, no grants, all hidden-system columns)."""
    if not columns_rows:
        return None

    columns: dict[str, ColumnDoc] = {}
    for r in columns_rows:
        col_name = str(r.get("COLUMN_NAME") or "")
        # ALL_TAB_COLUMNS occasionally surfaces quoted lowercase or hidden
        # columns (SYS_NCxxxx$, etc.) that the Pydantic regex rejects. Skip
        # those rather than crashing the whole table.
        if not _COLNAME_RE.match(col_name):
            log.debug("skip non-standard column %s.%s.%s", owner, table, col_name)
            continue
        col_type = format_oracle_type(
            r.get("DATA_TYPE"),
            r.get("DATA_LENGTH"),
            r.get("DATA_PRECISION"),
            r.get("DATA_SCALE"),
        )
        raw_desc = column_comments.get(col_name)
        desc = raw_desc.strip() if isinstance(raw_desc, str) else None
        columns[col_name] = ColumnDoc(
            type=col_type,
            description=desc or None,
        )

    if not columns:
        return None

    # partition_column must be one of the declared columns (TableDoc validator).
    if partition_column and partition_column not in columns:
        partition_column = None

    desc = table_comment.strip() if isinstance(table_comment, str) else None

    return TableDoc(
        table=table,
        schema=owner,
        description=desc or None,
        partition_column=partition_column,
        columns=columns,
    )


# ── Metadata pull ─────────────────────────────────────────────────────────

def _validate_owners(owners: tuple[str, ...]) -> tuple[str, ...]:
    upper = tuple(o.upper() for o in owners)
    bad = [o for o in upper if not _OWNER_RE.match(o)]
    if bad:
        raise SystemExit(f"invalid owner name(s): {bad}")
    return upper


def _pull_metadata(dc, owners: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Run the five metadata queries on a single Oracle connection."""
    conn = dc.get_connection()
    try:
        log.info("→ ALL_TABLES")
        tables = dc.edw_query_to_pandas(conn, _sql_tables(owners), {})
        log.info("   %d row(s)", len(tables))

        log.info("→ ALL_TAB_COLUMNS")
        cols = dc.edw_query_to_pandas(conn, _sql_columns(owners), {})
        log.info("   %d row(s)", len(cols))

        log.info("→ ALL_TAB_COMMENTS")
        tab_c = dc.edw_query_to_pandas(conn, _sql_tab_comments(owners), {})
        log.info("   %d row(s)", len(tab_c))

        log.info("→ ALL_COL_COMMENTS")
        col_c = dc.edw_query_to_pandas(conn, _sql_col_comments(owners), {})
        log.info("   %d row(s)", len(col_c))

        log.info("→ ALL_PART_KEY_COLUMNS")
        parts = dc.edw_query_to_pandas(conn, _sql_partition_keys(owners), {})
        log.info("   %d row(s)", len(parts))
    finally:
        dc.drop_connection(conn)

    return {
        "tables": tables,
        "columns": cols,
        "tab_comments": tab_c,
        "col_comments": col_c,
        "partition_keys": parts,
    }


# ── Driver ────────────────────────────────────────────────────────────────

def run(
    *,
    owners: tuple[str, ...] = DEFAULT_OWNERS,
    only_schema: str | None = None,
    only_table: str | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> int:
    owners = _validate_owners(owners)

    # Defer DataClient import so --help works on machines without pyaim/oracledb.
    from DataClient import DataClient

    dc = DataClient()
    meta = _pull_metadata(dc, owners)

    tables_df = meta["tables"]
    if only_schema:
        tables_df = tables_df[tables_df["OWNER"] == only_schema.upper()]
    if only_table:
        tables_df = tables_df[tables_df["TABLE_NAME"] == only_table.upper()]
    tables_df = tables_df.reset_index(drop=True)

    if tables_df.empty:
        log.info("no tables matched the filter")
        return 0

    # Group by (OWNER, TABLE_NAME) for O(1) per-table lookup.
    cols_by_key: dict[tuple[str, str], list[dict]] = {}
    for r in meta["columns"].to_dict("records"):
        cols_by_key.setdefault((r["OWNER"], r["TABLE_NAME"]), []).append(r)

    tab_comments: dict[tuple[str, str], str | None] = {
        (r["OWNER"], r["TABLE_NAME"]): r.get("COMMENTS")
        for r in meta["tab_comments"].to_dict("records")
    }

    col_comments_by_key: dict[tuple[str, str], dict[str, str | None]] = {}
    for r in meta["col_comments"].to_dict("records"):
        col_comments_by_key.setdefault(
            (r["OWNER"], r["TABLE_NAME"]), {}
        )[r["COLUMN_NAME"]] = r.get("COMMENTS")

    partition_cols: dict[tuple[str, str], str] = {
        (r["OWNER"], r["TABLE_NAME"]): r["COLUMN_NAME"]
        for r in meta["partition_keys"].to_dict("records")
    }

    written = 0
    skipped_exists = 0
    skipped_empty = 0
    errors = 0

    for _, t in tables_df.iterrows():
        owner = str(t["OWNER"])
        table = str(t["TABLE_NAME"])
        key = (owner, table)

        target = OUTPUT_BASE / owner / f"{table}.yaml"

        if target.exists() and not overwrite:
            log.info("[SKIP exists] %s.%s", owner, table)
            skipped_exists += 1
            continue

        try:
            doc = build_table_doc(
                owner=owner,
                table=table,
                columns_rows=cols_by_key.get(key, []),
                table_comment=tab_comments.get(key),
                column_comments=col_comments_by_key.get(key, {}),
                partition_column=partition_cols.get(key),
            )
        except Exception as exc:
            log.error("[ERR build]  %s.%s — %s", owner, table, exc)
            errors += 1
            continue

        if doc is None:
            log.warning("[SKIP empty] %s.%s — no readable columns", owner, table)
            skipped_empty += 1
            continue

        if dry_run:
            log.info("[DRY]        %s.%s (%d cols)", owner, table, len(doc.columns))
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_serialise_clean(doc))
        except Exception as exc:
            log.error("[ERR write]  %s.%s — %s", owner, table, exc)
            errors += 1
            continue

        log.info("[WROTE]      %s.%s (%d cols)", owner, table, len(doc.columns))
        written += 1

    log.info(
        "done. wrote=%d skipped_exists=%d skipped_empty=%d errors=%d (dry_run=%s)",
        written, skipped_exists, skipped_empty, errors, dry_run,
    )
    return 0 if errors == 0 else 1


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--owners",
        default=",".join(DEFAULT_OWNERS),
        help=f"Comma-separated Oracle owners. Default: {','.join(DEFAULT_OWNERS)}",
    )
    parser.add_argument("--schema", default=None, help="Restrict to one owner.")
    parser.add_argument("--table", default=None, help="Restrict to one table (requires --schema).")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Rebuild YAMLs that already exist (default: skip, so hand-edits survive).",
    )
    args = parser.parse_args()

    if args.table and not args.schema:
        parser.error("--table requires --schema")

    owners = tuple(s.strip() for s in args.owners.split(",") if s.strip())
    if not owners:
        parser.error("--owners is empty")

    return run(
        owners=owners,
        only_schema=args.schema,
        only_table=args.table,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    sys.exit(_main())

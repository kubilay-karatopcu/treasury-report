"""Nightly cron — refresh ``distinct_values_sample`` for migrated table docs.

For each TableDoc with at least one ``filterable: true`` + ``filter_role:
dimension`` column, this script:

1. Reads the TableDoc from the store (S3 in prod, local in DEV).
2. For each dimension column, runs ``SELECT DISTINCT <col> FROM <schema>.<table>
   FETCH FIRST 50 ROWS ONLY`` (Oracle) against ``DataClient`` and captures the
   result set.
3. Writes ``distinct_values_sample`` and ``distinct_values_sampled_at`` back
   into the TableDoc YAML.

Invocation:
    python -m jobs.sample_distinct_values [--schema EDW] [--table DEPOSITS_DAILY] [--dry-run]

When ``--schema`` and ``--table`` are both given, refreshes just that one
table; otherwise walks every migrated table. ``--dry-run`` shows what would
change without writing.

Runtime: ~1 minute per table on a warm Oracle. Designed to run nightly via
an OpenShift CronJob with the same env vars as ``app.py``.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make the parent project importable when run as `python -m jobs.sample_distinct_values`.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


log = logging.getLogger("jobs.sample_distinct_values")


# How many distinct values to keep per column. High-cardinality columns
# (e.g. customer_id) are pointless to sample exhaustively; 50 is plenty for
# the LLM prompt and for the filter UI's autocomplete.
_SAMPLE_LIMIT = 50


def _build_data_client():
    """Pick between the production DataClient and the DEV stub.

    The stub uses ``app._StubDataClient`` (when run via the app); we don't
    have an app object in this CLI, so we hand-roll one when DEV_MODE is on,
    or fall back to the prod ``DataClient.DataClient`` factory.
    """
    if os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes"):
        import duckdb
        import fake_db

        class _DevDataClient:
            def __init__(self):
                self._conn = duckdb.connect(":memory:")
                seen = set()
                for tid in fake_db.known_tables():
                    if "." in tid:
                        schema, tbl = tid.split(".", 1)
                        if schema not in seen:
                            self._conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                            seen.add(schema)
                        view = f"_fake_{tid.replace('.', '_')}"
                        self._conn.register(view, fake_db.get(tid))
                        self._conn.execute(
                            f'CREATE OR REPLACE VIEW "{schema}"."{tbl}" AS SELECT * FROM {view}'
                        )

            def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
                return self._conn.execute(query).fetchdf()

        return _DevDataClient()

    from DataClient import DataClient
    return DataClient()


def _build_store():
    """Pick the appropriate TableDoc store.

    DEV → ``dev_data/table_docs/`` filesystem.
    PROD → ``S3TableDocStore`` via ``DataClient``.
    """
    from presentations.table_docs.store import LocalTableDocStore, S3TableDocStore

    if os.environ.get("DEV_MODE", "").lower() in ("1", "true", "yes"):
        return LocalTableDocStore(base_dir=_ROOT / "dev_data" / "table_docs")
    from DataClient import DataClient
    return S3TableDocStore(dc=DataClient())


def _sample_one_column(dc, schema: str, table: str, column: str) -> list[Any]:
    """Run ``SELECT DISTINCT col FROM schema.table FETCH FIRST <N> ROWS ONLY``.

    Returns the values in encounter order (preserves whatever ordering Oracle
    used; the cron doesn't sort, since "first 50" by frequency would require
    a GROUP BY COUNT(*) which is more expensive).
    """
    # Oracle and DuckDB both accept this. Quoted identifiers preserve case.
    sql = (
        f'SELECT DISTINCT "{column}" '
        f'FROM "{schema}"."{table}" '
        f'WHERE "{column}" IS NOT NULL '
        f'FETCH FIRST {_SAMPLE_LIMIT} ROWS ONLY'
    )
    started = time.perf_counter()
    df = dc.get_data(base_prefix=None, dataset=f"{schema}.{table}", query=sql, query_params={})
    elapsed = time.perf_counter() - started
    if df is None or len(df.columns) == 0:
        return []
    raw = df.iloc[:, 0].dropna().tolist()
    # JSON-friendly: stringify Decimal / Timestamp where YAML would choke.
    out: list[Any] = []
    for v in raw:
        if hasattr(v, "isoformat"):
            out.append(v.isoformat())
        elif isinstance(v, (int, float, str, bool)):
            out.append(v)
        else:
            out.append(str(v))
    log.info(
        "sampled %s.%s.%s -> %d values in %.2fs",
        schema, table, column, len(out), elapsed,
    )
    return out


def refresh_doc(doc, dc, *, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Refresh distinct-value samples for every dimension column on ``doc``.

    Returns ``(changed, log_lines)`` so the caller can decide whether to
    persist and what to print.
    """
    changed = False
    log_lines: list[str] = []
    now = datetime.now(timezone.utc)

    for col_name, col in doc.columns.items():
        if not col.filterable:
            continue
        if col.filter_role != "dimension":
            continue
        try:
            values = _sample_one_column(dc, doc.schema_name, doc.table, col_name)
        except Exception as exc:
            log_lines.append(f"  [WARN] {col_name}: sample failed — {exc}")
            continue
        if not values:
            log_lines.append(f"  [SKIP] {col_name}: no values")
            continue
        before = col.distinct_values_sample
        if before == values:
            log_lines.append(f"  [OK]   {col_name}: unchanged ({len(values)} values)")
            continue
        if dry_run:
            log_lines.append(f"  [DRY]  {col_name}: would update ({len(values)} values)")
            continue
        # Mutate in-place; Pydantic re-validates on TableDoc serialisation.
        col.distinct_values_sample = values
        col.distinct_values_sampled_at = now
        log_lines.append(f"  [SET]  {col_name}: {len(values)} values")
        changed = True

    return changed, log_lines


def run(*, schema: str | None = None, table: str | None = None, dry_run: bool = False) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    store = _build_store()
    dc = _build_data_client()

    tables: list[tuple[str, str]]
    if schema and table:
        tables = [(schema, table)]
    else:
        tables = store.list_tables(schema=schema)
    if not tables:
        log.info("No migrated table docs found%s.",
                 f" for schema={schema}" if schema else "")
        return 0

    log.info("Will refresh %d table doc(s)%s", len(tables), " (dry-run)" if dry_run else "")
    refreshed = 0
    for s, t in tables:
        try:
            doc = store.load(s, t)
        except Exception as exc:
            log.error("load failed %s.%s: %s", s, t, exc)
            continue
        log.info("→ %s.%s", s, t)
        changed, lines = refresh_doc(doc, dc, dry_run=dry_run)
        for line in lines:
            log.info("%s", line)
        if changed and not dry_run:
            store.save(doc)
            refreshed += 1

    log.info("done. %d table(s) updated%s.", refreshed, " (dry-run)" if dry_run else "")
    return 0


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", default=None, help="Restrict to one schema (Oracle owner).")
    parser.add_argument("--table", default=None, help="Restrict to one table (requires --schema).")
    parser.add_argument("--dry-run", action="store_true", help="Show changes but don't write.")
    args = parser.parse_args()
    if args.table and not args.schema:
        parser.error("--table requires --schema")
    return run(schema=args.schema, table=args.table, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(_main())

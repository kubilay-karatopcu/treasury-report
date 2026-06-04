"""Filter-aware post-scope size estimation via Oracle EXPLAIN PLAN (madde 4).

The catalog-only estimator in :mod:`presentations.scope.routing`
(``estimate_post_scope_size``) only shrinks a table when a pinned ``between``
filter targets the documented *partition column*'s concept. Every other
predicate — a ``status IN (...)``, a ``currency = 'TRY'`` — leaves the estimate
untouched, so the node size badge over-reports for the common case.

This module refines that estimate with the Oracle optimizer's **cardinality**:

    EXPLAIN PLAN SET STATEMENT_ID = '<id>' FOR <the projected, filtered SELECT>
    SELECT cardinality FROM plan_table WHERE statement_id = '<id>' AND id = 0

``id = 0`` is the root (SELECT STATEMENT) row; its ``cardinality`` is the
optimizer's estimated number of rows the query returns. EXPLAIN PLAN **does not
execute** the query (no data scan, sub-second), and it reasons about arbitrary
predicates — exactly what we want. Accuracy is bounded by the freshness of the
table's optimizer statistics (a stale ``DBMS_STATS`` run → a stale estimate),
which is an acceptable trade for "filter-aware without scanning".

Bind values are kept as **parameterised binds**, never concatenated into the
SQL (locked decision §4). A side effect: at EXPLAIN PLAN time Oracle does not
peek bind values, so equality / IN predicates fall back to ``1/NDV`` from
column stats (value-independent but still filter-aware), and range predicates
to the optimizer's default range selectivity. That is strictly better than the
partition-only estimate and never worse.

Execution model:

- The two statements MUST run on the **same** Oracle session: ``PLAN_TABLE`` is
  a session-private global temporary table, so a pooled :meth:`DataClient.get_data`
  (a fresh connection per call) would read an empty table. We therefore take one
  dedicated connection via ``dc.get_connection()`` and close it — which also
  drops the GTT rows, so no explicit cleanup is needed.
- It is **background-only** (run from the :class:`RefreshDispatcher` thread pool,
  deduped per fingerprint) so the per-call connection setup never blocks a
  request. Results land in a :class:`SizeEstimateStore` the refine endpoint reads.

An exact ``COUNT(*)`` refinement (only for already-cached-eligible results, to
avoid a full-scan footgun) is a documented backlog item — EXPLAIN PLAN is the
primary, locked path here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)


# ── Fingerprint ─────────────────────────────────────────────────────────────

def fingerprint(sql: str, binds: dict[str, Any]) -> str:
    """Stable key for a (composed SQL, bind values) pair.

    The composed SELECT already encodes the table, projection and predicate
    columns; the binds carry the filter *values*. Two scopes that compile to the
    same SQL + binds share a cardinality, so they share a cache slot and a
    dedup key. Bind values are stringified (dates aren't JSON-native)."""
    norm_binds = {k: str(v) for k, v in sorted(binds.items())}
    payload = sql.strip() + "␟" + json.dumps(norm_binds, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ── EXPLAIN PLAN cardinality ────────────────────────────────────────────────

def explain_plan_rows(dc, sql: str, binds: dict[str, Any]) -> int | None:
    """Optimizer-estimated row count for ``sql`` with ``binds``, or ``None``.

    Returns ``None`` (caller keeps the partition-only estimate) when the
    DataClient can't open an Oracle connection (a DEV/stub client without
    ``get_connection``) or anything goes wrong — EXPLAIN PLAN is best-effort.
    """
    get_conn = getattr(dc, "get_connection", None)
    if get_conn is None:
        return None
    # Alphanumeric, ≤30 chars (PLAN_TABLE.statement_id is VARCHAR2(30)). Generated
    # locally, so inlining it into the EXPLAIN PLAN literal is injection-safe.
    stmt_id = "hz" + uuid.uuid4().hex[:26]
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {sql}", binds)
        cur.execute(
            "SELECT cardinality FROM plan_table "
            "WHERE statement_id = :sid AND id = 0",
            {"sid": stmt_id},
        )
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])
    except Exception:
        log.warning("size_estimate: EXPLAIN PLAN failed", exc_info=True)
        return None
    finally:
        if conn is not None:
            try:
                conn.close()  # closing the session also clears its PLAN_TABLE rows
            except Exception:
                pass


def estimate_bytes_via_explain(
    dc, sql: str, binds: dict[str, Any], bytes_per_row: int,
) -> dict[str, Any] | None:
    """Run EXPLAIN PLAN and turn the row estimate into a byte estimate.

    ``bytes_per_row`` comes from the catalog projection (the same width the
    routing estimator uses). Returns ``{"rows", "estimated_bytes"}`` or
    ``None`` when the cardinality is unavailable.
    """
    rows = explain_plan_rows(dc, sql, binds)
    if rows is None:
        return None
    rows = max(0, rows)
    return {"rows": rows, "estimated_bytes": int(rows * max(1, bytes_per_row))}


# ── Result store ────────────────────────────────────────────────────────────

class SizeEstimateStore:
    """Thread-safe TTL cache of refined size estimates, keyed by fingerprint.

    Lives once per Flask app (``app.config["SIZE_ESTIMATE_STORE"]``). The
    refine endpoint reads it synchronously (returning any fresh hit
    immediately) and the dispatcher's ``on_success`` writes to it after a
    background EXPLAIN PLAN completes. Entries expire after ``ttl_seconds`` so a
    table whose stats / data drift get re-estimated; a small ``max_entries``
    LRU-ish cap (drop oldest) keeps it bounded.
    """

    def __init__(self, ttl_seconds: int = 600, max_entries: int = 2000):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            e = self._data.get(key)
            if e is None:
                return None
            if (time.time() - e["computed_at"]) > self._ttl:
                self._data.pop(key, None)
                return None
            return dict(e)

    def put(self, key: str, *, rows: int, estimated_bytes: int, source: str) -> None:
        with self._lock:
            if key not in self._data and len(self._data) >= self._max:
                oldest = min(self._data.items(), key=lambda kv: kv[1]["computed_at"])[0]
                self._data.pop(oldest, None)
            self._data[key] = {
                "rows": rows,
                "estimated_bytes": estimated_bytes,
                "source": source,
                "computed_at": time.time(),
            }

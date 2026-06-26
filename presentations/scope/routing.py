"""Per-table cached/lazy routing decision (spec §3.1).

A table small enough after scope (projection + pinned filters) is materialised
into DuckDB (``cached``, fast); a larger one stays on Oracle (``lazy``, queried
on demand). The decision is made **from catalog metadata only** — never an
Oracle call — which is what keeps 8.a fast and testable. The actual fetch /
lazy query path lands in 8.d.

The estimate is intentionally conservative: an overestimate sends a table lazy
(slightly worse UX) while an underestimate would let DuckDB fill up and churn
the LRU cache, so when in doubt we estimate high.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal

from presentations.scope.catalog import Catalog, TableMeta
from presentations.scope.schema import PinnedFilter, Projection, TableRef


# Live defaults — the threshold matches the spec sample; the hard ceiling is
# overridable via the ``PRESENTATIONS_ROUTING_HARD_CEILING_BYTES`` config key.
DEFAULT_THRESHOLD_BYTES = 500_000_000        # 500 MB
DEFAULT_HARD_CEILING_BYTES = 10_000_000_000  # 10 GB
DEFAULT_HORIZON_DAYS = 365                   # when no pinned date range narrows the table

# Rough per-type byte widths used only when the catalog has no ``avg_bytes``
# (Phase 6.5.b table docs carry the Oracle type, not a sampled width).
_TYPE_BYTES = {
    "DATE": 8,
    "TIMESTAMP": 12,
    "NUMBER": 16,
    "FLOAT": 16,
    "INTEGER": 8,
}


class RoutingCeilingError(ValueError):
    """A user override forcing ``cached`` exceeds the hard ceiling (§3.1)."""

    def __init__(self, estimated_bytes: int, hard_ceiling_bytes: int):
        super().__init__(
            f"Cannot force cached: estimated {estimated_bytes} bytes exceeds the "
            f"hard ceiling of {hard_ceiling_bytes} bytes. Keep the table lazy or "
            "narrow the scope (tighter pinned filters / fewer columns)."
        )
        self.estimated_bytes = estimated_bytes
        self.hard_ceiling_bytes = hard_ceiling_bytes


@dataclass(frozen=True)
class RoutingDecision:
    decision: Literal["cached", "lazy"]
    estimated_bytes: int
    decided_by: Literal["system", "user"] = "system"
    threshold_bytes: int = DEFAULT_THRESHOLD_BYTES
    # D2 (Oturum N6) — `estimated_bytes` GERÇEK bir tahmin mi, yoksa "boyut
    # bilinmiyor → güvenli tarafta lazy" sentinel'i (threshold+1) mi? "catalog" =
    # gerçek katalog tahmini; "unknown" = tablo kataloglanmamış / satır istatistiği
    # yok → sentinel. UI sentinel'i "?" gösterir, sahte "500 MB" yerine.
    estimate_source: str = "catalog"


# ── Byte / row estimators ──────────────────────────────────────────────────

def _bytes_from_type(type_str: str | None) -> int:
    if not type_str:
        return 16
    t = type_str.strip().upper()
    base = t.split("(", 1)[0]
    if base in _TYPE_BYTES:
        return _TYPE_BYTES[base]
    if base in ("CHAR", "VARCHAR2", "VARCHAR", "NCHAR", "NVARCHAR2"):
        # Declared length is the conservative upper bound (overestimate ok).
        if "(" in t:
            inner = t[t.index("(") + 1: t.rindex(")")] if ")" in t else ""
            num = inner.split(",")[0].strip().rstrip(" BYTE").rstrip(" CHAR").strip()
            try:
                return max(1, int(num))
            except ValueError:
                return 16
        return 16
    return 16


def _bytes_per_row(table_meta: TableMeta, projection: Projection) -> int:
    if projection.include_all or not projection.columns:
        cols = list(table_meta.columns.keys())
    else:
        cols = list(projection.columns)
    total = 0
    for c in cols:
        cm = table_meta.columns.get(c)
        if cm is None:
            total += 16  # unknown column — conservative fixed width.
            continue
        total += cm.avg_bytes if cm.avg_bytes is not None else _bytes_from_type(cm.type)
    return total or 50


def _as_date(v) -> date | None:
    # Accept date objects, ISO strings, and the relative grammar (today,
    # today - 30d, start_of_month …) so a relative range shrinks the size
    # estimate just like an absolute one (re-resolved on each recompute).
    if isinstance(v, date):
        return v
    try:
        from presentations.variables.resolver import parse_date_expr
        return parse_date_expr(v)
    except Exception:
        return None


def _days_in_range(pinned: PinnedFilter) -> int | None:
    lo = _as_date(pinned.from_)
    hi = _as_date(pinned.to)
    if lo is None or hi is None or hi < lo:
        return None
    return (hi - lo).days + 1


def estimate_post_scope_size(
    table_meta: TableMeta,
    projection: Projection,
    pinned_filters: Iterable[PinnedFilter],
    *,
    default_horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int:
    """Estimate the post-scope byte size of one table (spec §3.1)."""
    bytes_per_row = _bytes_per_row(table_meta, projection)

    # If the table is partitioned on a date column and a pinned `between`
    # filter targets that column's concept, rows = daily_rows * days_in_range.
    partition_concept = (
        table_meta.column_concept(table_meta.partition_column)
        if table_meta.partition_column else None
    )
    rows: int | None = None
    if partition_concept and table_meta.estimated_daily_rows:
        for pf in pinned_filters:
            if pf.op == "between" and pf.concept == partition_concept:
                days = _days_in_range(pf)
                if days is not None:
                    rows = table_meta.estimated_daily_rows * days
                    break

    if rows is None:
        if table_meta.estimated_total_rows:
            rows = table_meta.estimated_total_rows
        elif table_meta.estimated_daily_rows:
            rows = table_meta.estimated_daily_rows * default_horizon_days
        else:
            rows = 0

    return int(rows * bytes_per_row)


# ── Decision ────────────────────────────────────────────────────────────────

def decide_routing(
    table_ref: TableRef,
    projection: Projection,
    pinned_filters: Iterable[PinnedFilter],
    threshold_bytes: int = DEFAULT_THRESHOLD_BYTES,
    *,
    catalog: Catalog,
    default_horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> RoutingDecision:
    """Decide cached vs lazy for a single basket table.

    ``cached`` iff ``estimated_bytes <= threshold_bytes``. The estimate reads
    only from ``catalog``; a table **missing** from the catalog can't be sized,
    so — per this module's "when in doubt, estimate high" rule — it is treated
    as over the threshold and routed ``lazy`` (fetched on demand, bounded by the
    row cap in ``scope.fetch``). Estimating it at 0 used to route it ``cached``,
    eagerly materialising a possibly-huge un-onboarded table into DuckDB (OOM);
    the user can still force ``cached`` via an explicit override (8.b badge).

    The same rule applies to a table that IS documented but carries **no row
    statistics** (neither ``estimated_total_rows`` nor ``estimated_daily_rows``):
    the size formula would yield 0 bytes and route it ``cached``, pulling the
    whole table uncapped at build (the "Sunum'a geç çok yavaş" bug — resolve-plan
    source mains flipped to cached this way). Unknown size → ``lazy``.
    """
    pinned_list = list(pinned_filters)
    table_meta = catalog.table_meta(table_ref.schema_name, table_ref.name)
    if table_meta is None:
        # Kataloglanmamış → boyutlanamaz, güvenli tarafta lazy. Sentinel: gerçek
        # tahmin DEĞİL → "unknown" (UI "?" gösterir, sahte "500 MB" değil).
        estimated, source = threshold_bytes + 1, "unknown"
    elif (table_meta.estimated_total_rows is None
          and table_meta.estimated_daily_rows is None):
        # Documented but unsized — cannot estimate, so estimate high (lazy).
        estimated, source = threshold_bytes + 1, "unknown"
    else:
        estimated = estimate_post_scope_size(
            table_meta, projection, pinned_list,
            default_horizon_days=default_horizon_days,
        )
        source = "catalog"

    decision: Literal["cached", "lazy"] = (
        "cached" if estimated <= threshold_bytes else "lazy"
    )
    return RoutingDecision(
        decision=decision,
        estimated_bytes=estimated,
        decided_by="system",
        threshold_bytes=threshold_bytes,
        estimate_source=source,
    )


def apply_user_override(
    decision: RoutingDecision,
    forced_decision: Literal["cached", "lazy"],
    *,
    hard_ceiling_bytes: int = DEFAULT_HARD_CEILING_BYTES,
) -> RoutingDecision:
    """Apply a user override of the system routing decision (§3.1).

    Forcing ``lazy`` always succeeds. Forcing ``cached`` is refused when the
    estimate exceeds ``hard_ceiling_bytes`` (raises :class:`RoutingCeilingError`).
    """
    if forced_decision == "cached" and decision.estimated_bytes > hard_ceiling_bytes:
        raise RoutingCeilingError(decision.estimated_bytes, hard_ceiling_bytes)
    return RoutingDecision(
        decision=forced_decision,
        estimated_bytes=decision.estimated_bytes,
        decided_by="user",
        threshold_bytes=decision.threshold_bytes,
        estimate_source=decision.estimate_source,
    )

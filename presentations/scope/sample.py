"""Oracle row-sampling for design-time Hazırlık work (Oturum 1, A5).

A basket table's *sample* — a proportional Oracle ``SAMPLE(pct)`` capped at a
row ceiling — is materialised into the session DuckDB so all design-time work
(preview, transform, derivations) runs locally and sub-second, with no Oracle
round-trip per interaction. The *full* data is pulled only at build (Sunum).

Samples never reach S3 parquet — they are session-DuckDB-only, so the parquet
store stays full-only and cron / Sunum viewers are unaffected. Fidelity (sample
vs full) is tracked per alias in the session DuckDB via
:func:`presentations.scope.materialize.record_fidelity`.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from presentations.scope.catalog import Catalog
from presentations.scope.fetch import compose_cached_sql
from presentations.scope.schema import BasketItem, ScopeContract

# Karar A: tablo başına %10 oransal sample, mutlak tavan 200k satır. Çok büyük
# tablolarda interaktif gecikmeyi asıl koruyan tavandır (10%'un kendisi
# kozmetik kalabilir). İkisi de config'ten geçilebilir (endpoint override eder).
DEFAULT_SAMPLE_PCT = 10
DEFAULT_SAMPLE_CEILING_ROWS = 200_000


def sample_fingerprint(sql: str, binds: dict[str, Any]) -> str:
    """Stable hash of the composed sample SQL + binds — the sample's staleness
    key. Re-materialise when projection / pinned / raw filters change the SQL so
    a stale preview can't survive a scope edit."""
    payload = (sql or "") + "" + json.dumps(binds or {}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compose_sample_sql(
    scope: ScopeContract, item: BasketItem, catalog: Catalog | None = None,
    *,
    concept_registry=None, binding_catalog=None,
    fraction: int = DEFAULT_SAMPLE_PCT,
    ceiling_rows: int = DEFAULT_SAMPLE_CEILING_ROWS,
) -> tuple[str, dict[str, Any], str]:
    """Compose the projected, sampled Oracle SELECT for a basket source item.

    - Raw ``table_ref`` items use Oracle ``SAMPLE(fraction)`` (proportional)
      with a ``FETCH FIRST ceiling_rows`` cap, reusing ``compose_cached_sql`` so
      projection + pinned/raw pushdown are identical to the full fetch.
    - ``sql`` (manual-SQL) items can't be wrapped in a ``SAMPLE`` clause, so they
      fall back to a top-N ``FETCH FIRST`` cap (documented inconsistency: a
      manual-SQL sample is the first N rows, not a proportional sample).

    Derived nodes (filter / aggregate / calculated / join / python) are NOT
    sampled here — they are previewed by re-running their derivation over the
    already-sampled source relations.

    Returns ``(sql, binds, fingerprint)``.
    """
    if item.table_ref is not None:
        sql, binds = compose_cached_sql(
            scope, item, catalog,
            concept_registry=concept_registry, binding_catalog=binding_catalog,
            max_rows=ceiling_rows, sample_pct=fraction,
        )
    elif item.sql is not None:
        sql = f"SELECT * FROM (\n{item.sql}\n) FETCH FIRST {int(ceiling_rows)} ROWS ONLY"
        binds = {}
    else:
        raise ValueError(
            f"compose_sample_sql: '{item.alias}' is neither a table_ref nor a sql "
            "item; derived nodes are sampled by re-running their derivation over "
            "sampled sources, not by this composer."
        )
    return sql, binds, sample_fingerprint(sql, binds)

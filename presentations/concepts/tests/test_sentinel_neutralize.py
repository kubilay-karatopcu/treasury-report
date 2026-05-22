"""Phase 7 — {{concept_filters}} sentinel neutralization.

A block may carry the sentinel but run in a path with no active concept
filter (manual run, preview, apply-filters miss). The literal token must be
neutralized to a no-op so the SQL stays valid.
"""
from __future__ import annotations

from presentations.concepts.integration import strip_concept_sentinel, SENTINEL


def test_strip_replaces_sentinel():
    sql = f"SELECT * FROM T WHERE CCY = 'USD' AND {SENTINEL}"
    assert strip_concept_sentinel(sql) == "SELECT * FROM T WHERE CCY = 'USD' AND 1 = 1"


def test_strip_noop_when_absent():
    sql = "SELECT * FROM T WHERE CCY = 'USD'"
    assert strip_concept_sentinel(sql) is sql or strip_concept_sentinel(sql) == sql


def test_strip_idempotent():
    sql = f"SELECT 1 FROM dual WHERE {SENTINEL}"
    once = strip_concept_sentinel(sql)
    assert strip_concept_sentinel(once) == once
    assert SENTINEL not in once


def test_strip_only_where_sentinel():
    # Sentinel as the entire WHERE → valid 1 = 1.
    assert strip_concept_sentinel(f"SELECT * FROM T WHERE {SENTINEL}") \
        == "SELECT * FROM T WHERE 1 = 1"

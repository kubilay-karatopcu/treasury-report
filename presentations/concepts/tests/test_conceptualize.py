"""Phase 7 — conceptualize_query: lift literal predicates → concept filters."""
from __future__ import annotations

from pathlib import Path

import pytest

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.integration import conceptualize_query


@pytest.fixture(scope="module")
def registry():
    return ConceptRegistry.from_dir(Path(presentations.__file__).parent / "catalog" / "concepts")


@pytest.fixture(scope="module")
def catalog():
    return BindingCatalog.from_dir(Path(presentations.__file__).parent / "catalog" / "tables")


def _conv(sql, registry, catalog, schema="EDW", table="DEPOSITS_DAILY"):
    return conceptualize_query(sql, schema, table, catalog, registry)


# ── identity concept (segment via map, RETAIL is a known table value) ──────

def test_eq_segment_lifted(registry, catalog):
    r = _conv("SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY WHERE SEGMENT = 'RETAIL'",
              registry, catalog)
    assert "{{concept_filters}}" in r["rewritten_sql"]
    assert "SEGMENT = 'RETAIL'" not in r["rewritten_sql"]
    assert len(r["seeded_filters"]) == 1
    f = r["seeded_filters"][0]
    assert f["semantic_tag"] == "segment"
    assert f["default"] == ["RETAIL"]
    assert r["converted"][0] == {"column": "SEGMENT", "concept": "segment", "values": ["RETAIL"]}


def test_map_value_translated_to_canonical(registry, catalog):
    # Table stores CORPORATE; map binding → canonical CORP in the seeded filter.
    r = _conv("SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY WHERE SEGMENT = 'CORPORATE'",
              registry, catalog)
    assert r["seeded_filters"][0]["default"] == ["CORP"]


def test_in_list_lifted(registry, catalog):
    r = _conv("SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY WHERE SEGMENT IN ('RETAIL', 'SME')",
              registry, catalog)
    assert "{{concept_filters}}" in r["rewritten_sql"]
    assert r["seeded_filters"][0]["default"] == ["RETAIL", "SME"]


# ── non-concept predicate stays hardcoded ──────────────────────────────────

def test_non_concept_predicate_kept(registry, catalog):
    r = _conv("SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY WHERE STATUS = 'ACTIVE'",
              registry, catalog)
    # STATUS not concept-bound → unchanged, no filters.
    assert r["rewritten_sql"].count("STATUS = 'ACTIVE'") == 1
    assert r["seeded_filters"] == []
    assert "{{concept_filters}}" not in r["rewritten_sql"]


def test_mixed_concept_and_literal(registry, catalog):
    r = _conv("SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY "
              "WHERE SEGMENT = 'RETAIL' AND STATUS = 'ACTIVE'", registry, catalog)
    # SEGMENT lifted, STATUS kept, sentinel added.
    assert "STATUS = 'ACTIVE'" in r["rewritten_sql"]
    assert "{{concept_filters}}" in r["rewritten_sql"]
    assert "SEGMENT = 'RETAIL'" not in r["rewritten_sql"]
    assert r["seeded_filters"][0]["semantic_tag"] == "segment"


def test_mixed_preserves_group_by(registry, catalog):
    r = _conv("SELECT SEGMENT, SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY "
              "WHERE SEGMENT = 'RETAIL' AND STATUS = 'ACTIVE' GROUP BY SEGMENT", registry, catalog)
    assert r["rewritten_sql"].rstrip().endswith("GROUP BY SEGMENT")
    assert "STATUS = 'ACTIVE' AND {{concept_filters}}" in r["rewritten_sql"]


# ── safety: don't touch OR or non-bound tables ─────────────────────────────

def test_or_where_skipped(registry, catalog):
    sql = "SELECT v FROM EDW.DEPOSITS_DAILY WHERE SEGMENT = 'RETAIL' OR BALANCE_TRY > 0"
    r = _conv(sql, registry, catalog)
    assert r["rewritten_sql"] == sql           # untouched
    assert r["seeded_filters"] == []
    assert r["skipped"]


def test_unbound_table_no_conversion(registry, catalog):
    sql = "SELECT v FROM ODS_RISK.PD_SCORES WHERE SEGMENT = 'RETAIL'"
    r = _conv(sql, registry, catalog, schema="ODS_RISK", table="PD_SCORES")
    assert r["seeded_filters"] == []           # no bindings for that table
    assert r["rewritten_sql"] == sql


def test_no_where_noop(registry, catalog):
    sql = "SELECT SUM(BALANCE_TRY) AS v FROM EDW.DEPOSITS_DAILY"
    r = _conv(sql, registry, catalog)
    assert r["rewritten_sql"] == sql
    assert r["seeded_filters"] == []

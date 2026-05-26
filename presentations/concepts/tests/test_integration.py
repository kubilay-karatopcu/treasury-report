"""Phase 7.b.3 — concept→dashboard integration (bridge + injection)."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.integration import (
    SENTINEL,
    dashboard_filters_to_resolved,
    apply_concepts_to_block,
)

_CATALOG = Path(presentations.__file__).parent / "catalog"


@pytest.fixture(scope="module")
def registry():
    return ConceptRegistry.from_dir(_CATALOG / "concepts")


@pytest.fixture(scope="module")
def catalog():
    return BindingCatalog.from_dir(_CATALOG / "tables")


# ── Bridge: dashboard filters → ResolvedFilter ─────────────────────────────

def test_bridge_enum_multi(registry):
    filters = [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}]
    state = {"f0": ["USD", "EUR"]}
    out = dashboard_filters_to_resolved(filters, state, registry)
    assert len(out) == 1
    assert out[0].concept == "currency"
    assert out[0].operator == "in"
    assert out[0].values == ["USD", "EUR"]
    assert out[0].filter_id == "f0"


def test_bridge_date_range_to_between(registry):
    # ISO bounds resolve to date objects (never raw strings → Oracle would
    # choke on a literal "today" bind otherwise).
    filters = [{"id": "fd", "semantic_tag": "trade_time", "type": "date_range"}]
    state = {"fd": {"from": "2026-01-01", "to": "2026-01-31"}}
    out = dashboard_filters_to_resolved(filters, state, registry)
    assert out[0].operator == "between"
    assert out[0].values == [date(2026, 1, 1), date(2026, 1, 31)]


def test_bridge_date_range_resolves_relative_exprs(registry):
    # Regression: relative exprs ("today", "today - 30d") must be resolved to
    # concrete dates at the bridge, not passed raw to the compiler/Oracle.
    filters = [{"id": "fd", "semantic_tag": "trade_time", "type": "date_range"}]
    state = {"fd": {"from": "today - 30d", "to": "today"}}
    out = dashboard_filters_to_resolved(filters, state, registry)
    assert out[0].operator == "between"
    frm, to = out[0].values
    assert isinstance(frm, date) and isinstance(to, date)
    assert to == date.today()
    assert frm == date.today() - timedelta(days=30)


def test_bridge_date_range_skips_unparseable_bound(registry):
    filters = [{"id": "fd", "semantic_tag": "trade_time", "type": "date_range"}]
    state = {"fd": {"from": "garbage", "to": "today"}}
    out = dashboard_filters_to_resolved(filters, state, registry)
    assert out == []   # bad bound → filter skipped, apply doesn't crash


def test_bridge_concept_ref_wins_over_semantic_tag(registry):
    filters = [{"id": "f0", "semantic_tag": "other", "concept_ref": "currency",
                "type": "enum_multi"}]
    out = dashboard_filters_to_resolved(filters, {"f0": ["USD"]}, registry)
    assert out[0].concept == "currency"


def test_bridge_skips_unknown_concept(registry):
    filters = [{"id": "f0", "semantic_tag": "not_a_concept", "type": "enum_multi"}]
    out = dashboard_filters_to_resolved(filters, {"f0": ["X"]}, registry)
    assert out == []


def test_bridge_uses_default_when_no_state(registry):
    filters = [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi",
                "default": ["TRY"]}]
    out = dashboard_filters_to_resolved(filters, {}, registry)
    assert out[0].values == ["TRY"]


# ── Injection ──────────────────────────────────────────────────────────────

def _block(query, tables=None):
    return {"id": "b1", "type": "bar_chart", "query": query,
            "source_tables": tables or [{"schema": "ODS_TREASURY",
                                         "table": "FX_SWAP_DEALS"}]}


def test_inject_at_sentinel(registry, catalog):
    block = _block(f"SELECT * FROM FX_SWAP_DEALS WHERE {SENTINEL}")
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD", "EUR"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.injected is True
    assert "CCY IN (:f0_currency_0, :f0_currency_1)" in inj.sql
    assert SENTINEL not in inj.sql
    assert inj.params == {"f0_currency_0": "USD", "f0_currency_1": "EUR"}
    assert inj.blind == []


def test_no_sentinel_injects_into_where(registry, catalog):
    # No sentinel: the predicate is injected into the WHERE directly so the
    # filter still applies (user expectation).
    block = _block("SELECT CCY FROM FX_SWAP_DEALS WHERE 1=1")
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.injected is True
    assert "CCY IN (:f0_currency_0)" in inj.sql
    assert "AND (CCY IN (:f0_currency_0))" in inj.sql   # ANDed onto existing WHERE
    assert inj.params == {"f0_currency_0": "USD"}


def test_no_sentinel_no_where_adds_where(registry, catalog):
    block = _block("SELECT CCY, SUM(NOTIONAL_TRY) FROM FX_SWAP_DEALS GROUP BY CCY")
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.injected is True
    # WHERE added before GROUP BY.
    assert "WHERE CCY IN (:f0_currency_0) GROUP BY CCY" in inj.sql


def test_no_source_tables_noop(registry, catalog):
    block = {"id": "b1", "type": "bar_chart",
             "query": f"SELECT 1 WHERE {SENTINEL}"}  # no source_tables
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.injected is False
    assert inj.applied == []


def test_blind_table_reported(registry, catalog):
    # PD_MODEL_SCORES has no binding → currency is blind.
    block = _block(f"SELECT * FROM PD_MODEL_SCORES WHERE {SENTINEL}",
                   tables=[{"schema": "ODS_RISK", "table": "PD_MODEL_SCORES"}])
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.blind == ["currency"]
    assert inj.injected is False           # nothing usable to inject
    # sentinel left intact since there were no usable predicates
    assert SENTINEL in inj.sql


def test_empty_selection_injects_false(registry, catalog):
    block = _block(f"SELECT * FROM FX_SWAP_DEALS WHERE {SENTINEL}")
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": []}, registry)   # empty selection
    inj = apply_concepts_to_block(block, block["query"], {}, resolved, registry, catalog)
    assert inj.empty is True
    assert "1 = 0" in inj.sql


def test_merges_base_params(registry, catalog):
    block = _block(f"SELECT * FROM FX_SWAP_DEALS WHERE x = :base AND {SENTINEL}")
    resolved = dashboard_filters_to_resolved(
        [{"id": "f0", "semantic_tag": "currency", "type": "enum_multi"}],
        {"f0": ["USD"]}, registry)
    inj = apply_concepts_to_block(block, block["query"], {"base": 1}, resolved,
                                  registry, catalog)
    assert inj.params["base"] == 1
    assert inj.params["f0_currency_0"] == "USD"

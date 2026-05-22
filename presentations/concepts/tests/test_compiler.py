"""Phase 7.b.2 — filter compiler: golden snapshots + unit + determinism."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import presentations
from presentations.concepts.registry import ConceptRegistry
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.compiler import (
    ResolvedFilter,
    compile_filters,
    compile_filter_for_table,
)


GOLDEN_DIR = Path(__file__).parent / "golden"
_CATALOG = Path(presentations.__file__).parent / "catalog"


@pytest.fixture(scope="module")
def registry() -> ConceptRegistry:
    return ConceptRegistry.from_dir(_CATALOG / "concepts")


@pytest.fixture(scope="module")
def catalog() -> BindingCatalog:
    return BindingCatalog.from_dir(_CATALOG / "tables")


# ── Golden snapshots ───────────────────────────────────────────────────────

def _golden_files():
    return sorted(GOLDEN_DIR.glob("*.yaml"))


@pytest.mark.parametrize("golden_path", _golden_files(), ids=lambda p: p.stem)
def test_golden_snapshot(golden_path, registry, catalog):
    spec = yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    filters = [
        ResolvedFilter(concept=f["concept"], operator=f["operator"],
                       values=f["values"], filter_id=f["filter_id"])
        for f in spec["filter_state"]
    ]
    tables = [(t["schema"], t["table"]) for t in spec["tables_in_play"]]

    result = compile_filters(filters, tables, registry, catalog)

    for exp_table in spec["expected"]["per_table_predicates"]:
        key = (exp_table["schema"], exp_table["table"])
        assert key in result, f"missing table {key}"
        compiled = {(p.filter_id, p.concept): p for p in result[key]}
        for exp in exp_table["predicates"]:
            p = compiled.get((exp["filter_id"], exp["concept"]))
            assert p is not None, f"missing predicate {exp} for {key}"
            assert p.blind == exp["blind"], f"blind mismatch for {key}/{exp['concept']}"
            assert p.sql.strip() == exp["sql"].strip(), (
                f"SQL mismatch for {key}/{exp['concept']}:\n"
                f"  got: {p.sql!r}\n  exp: {exp['sql']!r}"
            )
            assert p.params == (exp.get("params") or {}), (
                f"params mismatch for {key}/{exp['concept']}:\n"
                f"  got: {p.params}\n  exp: {exp.get('params')}"
            )


# ── Determinism ────────────────────────────────────────────────────────────

def test_determinism(registry, catalog):
    filters = [ResolvedFilter("currency", "in", ["USD", "EUR"], "f0"),
               ResolvedFilter("maturity", "in", ["1M", "3M"], "f1")]
    tables = [("ODS_TREASURY", "TRD_BRANCH_POSITION"),
              ("ODS_TREASURY", "FX_SWAP_DEALS")]
    runs = [compile_filters(filters, tables, registry, catalog) for _ in range(20)]
    first = {k: [(p.sql, tuple(sorted(p.params.items()))) for p in v]
             for k, v in runs[0].items()}
    for r in runs[1:]:
        cur = {k: [(p.sql, tuple(sorted(p.params.items()))) for p in v]
               for k, v in r.items()}
        assert cur == first


# ── Transform-kind units ───────────────────────────────────────────────────

def test_identity_single_value(registry, catalog):
    p = compile_filter_for_table(
        ResolvedFilter("currency", "in", ["USD"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.sql == "CCY IN (:f0_currency_0)"
    assert p.params == {"f0_currency_0": "USD"}


def test_lookup_subquery(registry, catalog):
    p = compile_filter_for_table(
        ResolvedFilter("branch", "in", ["0123", "0456"], "f0"),
        "ODS_TREASURY", "TRD_BRANCH_POSITION", registry, catalog)
    assert p.sql == (
        "BRANCH_ID IN (SELECT BRANCH_ID FROM DIM_BRANCH "
        "WHERE BRANCH_CODE IN (:f0_branch_0, :f0_branch_1))"
    )
    assert p.params == {"f0_branch_0": "0123", "f0_branch_1": "0456"}


def test_bucket_open_top(registry, catalog):
    # "10Y+" has day_range [3650, null] → open-ended high bound.
    p = compile_filter_for_table(
        ResolvedFilter("maturity", "in", ["10Y+"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.sql == "(MATURITY_DAYS >= :f0_maturity_0_lo)"
    assert p.params == {"f0_maturity_0_lo": 3650}


def test_time_truncation_between(registry, catalog):
    p = compile_filter_for_table(
        ResolvedFilter("trade_time", "between", ["2026-01-01", "2026-01-31"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.sql == "TRUNC(TRADE_DATE) BETWEEN :f0_trade_time_from AND :f0_trade_time_to"
    assert p.params == {"f0_trade_time_from": "2026-01-01",
                        "f0_trade_time_to": "2026-01-31"}


def test_identity_date_between(registry, catalog):
    # value_time on FX_SWAP_DEALS is identity (plain DATE column).
    p = compile_filter_for_table(
        ResolvedFilter("value_time", "between", ["2026-01-01", "2026-01-31"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.sql == "VALUE_DATE BETWEEN :f0_value_time_from AND :f0_value_time_to"


def test_concept_blind(registry, catalog):
    p = compile_filter_for_table(
        ResolvedFilter("currency", "in", ["USD"], "f0"),
        "ODS_RISK", "PD_MODEL_SCORES", registry, catalog)
    assert p.blind is True
    assert p.sql == ""
    assert p.params == {}


def test_empty_values_short_circuits(registry, catalog):
    p = compile_filter_for_table(
        ResolvedFilter("currency", "in", [], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.empty is True
    assert p.sql == "1 = 0"


def test_unknown_value_dropped(registry, catalog):
    # "ZZZ" is not a canonical currency → dropped; only USD remains.
    p = compile_filter_for_table(
        ResolvedFilter("currency", "in", ["USD", "ZZZ"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.sql == "CCY IN (:f0_currency_0)"
    assert p.params == {"f0_currency_0": "USD"}


def test_alias_resolved_before_emit(registry, catalog):
    # "US Dollar" alias → canonical USD before hitting SQL.
    p = compile_filter_for_table(
        ResolvedFilter("currency", "in", ["US Dollar"], "f0"),
        "ODS_TREASURY", "FX_SWAP_DEALS", registry, catalog)
    assert p.params == {"f0_currency_0": "USD"}


def test_filter_id_prevents_collision(registry, catalog):
    # Two filters of the same concept → distinct bind names.
    filters = [ResolvedFilter("currency", "in", ["USD"], "f0"),
               ResolvedFilter("currency", "in", ["EUR"], "f1")]
    result = compile_filters(filters, [("ODS_TREASURY", "FX_SWAP_DEALS")],
                             registry, catalog)
    preds = result[("ODS_TREASURY", "FX_SWAP_DEALS")]
    names = set()
    for p in preds:
        names |= set(p.params.keys())
    assert names == {"f0_currency_0", "f1_currency_0"}

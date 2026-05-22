"""Tests for the §6R redesign data-model deltas: raw filters, node positions,
join-sourced columns, and the raw-filter validator."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentations.scope.catalog import DictCatalog
from presentations.scope.schema import (
    BasketItem, Filters, JoinedColumn, NodePosition, RawFilter,
    dump_scope_yaml, load_scope_from_dict, load_scope_yaml,
)
from presentations.scope.validators import rule_raw_filters


def _catalog():
    return DictCatalog.from_excerpt({
        "tables": {"TRD_BRANCH_POSITION": {
            "schema": "ODS_TREASURY", "partition_column": "AS_OF_DATE",
            "columns": {"AS_OF_DATE": {"concept": "as_of_time"},
                        "BRANCH_ID": {"concept": "branch"},
                        "NET_POSITION": {"concept": None}},
        }},
        "concepts": {"as_of_time": {"type": "date"}},
    })


def _scope(raw_filters):
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z",
        "basket": [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["AS_OF_DATE", "NET_POSITION"], "include_all": False},
            "routing": {"decision": "cached", "estimated_bytes": 0},
        }],
        "filters": {"raw": raw_filters},
    })


# ── Schema ───────────────────────────────────────────────────────────────────

class TestSchema:
    def test_raw_filter_parses_and_normalises_dates(self):
        rf = RawFilter.model_validate(
            {"id": "rf_created", "alias": "positions", "column": "CREATED_AT",
             "op": "between", "from": __import__("datetime").date(2025, 1, 1), "to": "2025-12-31"})
        assert rf.from_ == "2025-01-01" and rf.to == "2025-12-31"

    def test_raw_filter_id_prefix_enforced(self):
        with pytest.raises(ValidationError):
            RawFilter.model_validate({"id": "bad_id", "alias": "positions",
                                      "column": "X", "op": "eq", "value": 1})

    def test_node_position_on_basket_item(self):
        item = BasketItem.model_validate({
            "table_ref": {"schema": "S", "name": "T"}, "alias": "positions",
            "projection": {"columns": ["A"]},
            "routing": {"decision": "cached", "estimated_bytes": 0},
            "layout": {"x": 120.5, "y": -40.0},
        })
        assert item.layout.x == 120.5 and item.layout.y == -40.0

    def test_joined_column_alias(self):
        jc = JoinedColumn.model_validate({"via_join": "j_pos_branch", "column": "BRANCH_NAME", "as": "branch"})
        assert jc.via_join == "j_pos_branch" and jc.as_ == "branch"
        assert jc.model_dump(by_alias=True)["as"] == "branch"

    def test_redesign_fields_roundtrip(self):
        sc = _scope([{"id": "rf_x", "alias": "positions", "column": "NET_POSITION",
                      "op": "between", "from": "2025-01-01", "to": "2025-02-01"}])
        sc.basket[0].layout = NodePosition(x=10, y=20)
        sc.basket[0].projection.joined = [JoinedColumn(via_join="j_x", column="C")]
        reloaded = load_scope_yaml(dump_scope_yaml(sc))
        assert reloaded == sc
        assert dump_scope_yaml(reloaded) == dump_scope_yaml(sc)

    def test_filters_default_empty_lists(self):
        f = Filters()
        assert f.pinned == [] and f.interactive == [] and f.raw == []


# ── Validator (rule 8: raw filters) ──────────────────────────────────────────

class TestRawFilterValidator:
    def test_alias_not_in_basket(self):
        scope = _scope([{"id": "rf_bad", "alias": "ghost", "column": "X", "op": "eq", "value": 1}])
        errors, _ = rule_raw_filters(scope, _catalog())
        assert "Raw filter 'rf_bad': alias 'ghost' not in basket" in errors

    def test_column_not_on_table(self):
        scope = _scope([{"id": "rf_bad", "alias": "positions", "column": "NOPE", "op": "eq", "value": 1}])
        errors, _ = rule_raw_filters(scope, _catalog())
        assert any("column 'NOPE' does not exist on TRD_BRANCH_POSITION" in e for e in errors)

    def test_between_inverted(self):
        scope = _scope([{"id": "rf_bad", "alias": "positions", "column": "AS_OF_DATE",
                         "op": "between", "from": "2025-12-31", "to": "2025-01-01"}])
        errors, _ = rule_raw_filters(scope, _catalog())
        assert any("between requires from <= to" in e for e in errors)

    def test_valid_raw_filter_passes(self):
        scope = _scope([{"id": "rf_ok", "alias": "positions", "column": "NET_POSITION",
                         "op": "between", "from": "2025-01-01", "to": "2025-12-31"}])
        errors, warnings = rule_raw_filters(scope, _catalog())
        assert errors == [] and warnings == []

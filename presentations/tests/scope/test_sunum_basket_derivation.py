"""Sunum sidebar basket derivation: manual-SQL and derived (filter / aggregate /
calculated) nodes built in Hazırlık must reach the Sunum "Veri Kaynakları" list.

Node visibility (active/passive) is owned by ``ScopeContract.inactive_aliases``
and applied by the Hazırlık ``_finalisedScope`` before build, so the scope that
reaches ``_manifest_basket_from_scope`` is already active-only — this helper just
maps every basket item to its Sunum sidebar entry.
"""
from __future__ import annotations

from presentations.routes_scope import _manifest_basket_from_scope
from presentations.scope.schema import load_scope_from_dict

_BASE = {
    "table_ref": {"schema": "EDW", "name": "DEPOSITS"}, "alias": "deposits",
    "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
    "routing": {"decision": "cached", "estimated_bytes": 0},
}
_SQL = {
    "sql": "SELECT 1 AS X FROM DUAL", "alias": "custom_sql",
    "routing": {"decision": "cached", "estimated_bytes": 0},
}
_AGG = {
    "derivation": {"kind": "aggregate", "source_alias": "deposits",
                   "group_by": ["BRANCH_CODE"],
                   "measures": [{"column": "BALANCE_TRY", "fn": "sum", "as": "TOTAL"}]},
    "alias": "deposits_by_branch",
    "projection": {"columns": ["BRANCH_CODE", "TOTAL"], "include_all": False},
    "routing": {"decision": "cached", "estimated_bytes": 0},
}


def _scope(basket):
    return load_scope_from_dict({
        "presentation_id": "p_x", "version": 1, "created_by": "A16438",
        "created_at": "2026-06-15T10:00:00Z", "basket": basket,
    })


class TestManifestBasketFromScope:
    def test_table_sql_and_derived_all_surface(self):
        scope = _scope([_BASE, _SQL, _AGG])
        out = _manifest_basket_from_scope(scope)
        by_alias = {e["alias"]: e for e in out}
        assert set(by_alias) == {"deposits", "custom_sql", "deposits_by_branch"}

        assert by_alias["deposits"]["table"] == "EDW.DEPOSITS"
        assert by_alias["deposits"]["source"] == "table"

        assert by_alias["custom_sql"]["source"] == "sql"
        assert by_alias["custom_sql"]["table"] == "custom_sql"  # alias = view name

        deriv = by_alias["deposits_by_branch"]
        assert deriv["source"] == "derived"
        assert deriv["derivation_kind"] == "aggregate"

    def test_table_projection_columns_carried(self):
        out = _manifest_basket_from_scope(_scope([_BASE]))
        assert out[0]["columns"] == ["BRANCH_CODE", "BALANCE_TRY"]


def test_manifest_basket_carries_column_concepts():
    # #4 — Hazırlık'ta kolona bağlanan concept Sunum manifest basket'ine taşınır
    # (filtre önerileri görsün diye).
    main = {**_BASE, "column_concepts": {"BRANCH_CODE": "branch"}}
    scope = _scope([main])
    out = _manifest_basket_from_scope(scope)
    entry = next(e for e in out if e["alias"] == "deposits")
    assert entry["column_concepts"] == {"BRANCH_CODE": "branch"}


def test_manifest_basket_column_concepts_default_empty():
    out = _manifest_basket_from_scope(_scope([_SQL]))
    assert out[0]["column_concepts"] == {}

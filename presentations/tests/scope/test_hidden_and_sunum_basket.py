"""Sunum sidebar derivation + hidden-node handling.

Covers the fix for: manual-SQL / derived nodes built in Hazırlık never reaching
the Sunum "Veri Kaynakları" sidebar, and hidden ("gizle") nodes still showing
there / still being fetched.
"""
from __future__ import annotations

from presentations.routes_scope import (
    _manifest_basket_from_scope,
    _scope_for_fetch,
)
from presentations.scope.schema import load_scope_from_dict, scope_to_dict

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


# ── Schema: hidden field round-trips and stays None-default ──────────────────

class TestHiddenField:
    def test_default_none_omitted_in_serialisation(self):
        scope = _scope([_BASE])
        dumped = scope_to_dict(scope)["scope"]
        assert "hidden" not in dumped["basket"][0]

    def test_hidden_true_round_trips(self):
        scope = _scope([{**_BASE, "hidden": True}])
        assert scope.basket[0].hidden is True
        dumped = scope_to_dict(scope)["scope"]
        assert dumped["basket"][0]["hidden"] is True


# ── manifest.basket derivation for the Sunum sidebar ─────────────────────────

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

    def test_hidden_items_excluded(self):
        scope = _scope([_BASE, {**_SQL, "hidden": True}])
        out = _manifest_basket_from_scope(scope)
        assert [e["alias"] for e in out] == ["deposits"]


# ── _scope_for_fetch prunes only *safe* hidden nodes ─────────────────────────

class TestScopeForFetch:
    def test_no_hidden_returns_same_object(self):
        scope = _scope([_BASE, _AGG])
        assert _scope_for_fetch(scope) is scope

    def test_unreferenced_hidden_table_dropped(self):
        ghost = {
            "table_ref": {"schema": "EDW", "name": "GHOST"}, "alias": "ghost",
            "routing": {"decision": "cached", "estimated_bytes": 0},
            "hidden": True,
        }
        scope = _scope([_BASE, ghost])
        active = _scope_for_fetch(scope)
        assert [b.alias for b in active.basket] == ["deposits"]
        # Original is untouched (full scope is still persisted).
        assert len(scope.basket) == 2

    def test_hidden_source_of_visible_derivation_kept(self):
        # deposits is hidden but a visible aggregate derives from it → must stay
        # in the fetch scope, otherwise the derivation has no input.
        scope = _scope([{**_BASE, "hidden": True}, _AGG])
        active = _scope_for_fetch(scope)
        assert set(b.alias for b in active.basket) == {"deposits", "deposits_by_branch"}

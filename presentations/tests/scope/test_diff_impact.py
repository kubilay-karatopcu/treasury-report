"""Phase 8.e tests — scope diff + block impact analysis.

Acceptance (§10.e):
  - Adding a new table → produces scope_v<N+1> with correct parent_version.
  - Only the new table is fetched; existing cached tables remain in DuckDB.
  - Removing a table used by blocks shows the warning UI.
  - Changing a filter from pinned to interactive does not require re-fetch.
  - Changing a pinned filter value triggers re-fetch for tables it applies_to.

The pure-function pieces (diff + impact) are exercised here; partial fetch
is exercised separately in test_fetch.py (existing) via the new
``refetch_only`` / ``drop_aliases`` parameters.
"""
from __future__ import annotations

import copy

import pytest

from presentations.scope.diff import (
    PinStateFlip,
    ScopeDiff,
    diff_scopes,
    serialise_diff,
)
from presentations.scope.impact import (
    AffectedBlock,
    compute_affected_blocks,
    serialise_affected,
    summarise,
)
from presentations.scope.schema import load_scope_from_dict


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def v1_scope_dict() -> dict:
    """v1: deposits_daily basket + no filters."""
    return {
        "scope": {
            "presentation_id": "p_t", "version": 1,
            "created_by": "A", "created_at": "2026-05-24T00:00:00Z",
            "basket": [{
                "alias": "deposits_daily",
                "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
                "projection": {"columns": ["DAT", "SEGMENT", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1_000_000},
            }],
            "filters": {"pinned": [], "interactive": [], "raw": []},
            "joins": [],
        }
    }


def _scope(d: dict):
    return load_scope_from_dict(d)


# ── Diff: add / remove / change basket items ────────────────────────────────

class TestBasketDiff:

    def test_first_build_treats_everything_as_added(self, v1_scope_dict):
        new = _scope(v1_scope_dict)
        d = diff_scopes(None, new)
        assert set(d.added_aliases) == {"deposits_daily"}
        assert d.removed_aliases == {}
        assert d.changed_aliases == {}
        assert d.is_empty is False

    def test_identical_scope_is_empty_diff(self, v1_scope_dict):
        old = _scope(v1_scope_dict)
        new = _scope(copy.deepcopy(v1_scope_dict))
        d = diff_scopes(old, new)
        assert d.is_empty is True
        assert d.affected_aliases == set()
        assert d.has_breaking_changes is False

    def test_added_alias(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"].append({
            "alias": "branch_dim",
            "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
            "projection": {"columns": ["BRANCH_CODE", "BRANCH_NAME"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 1_000},
        })
        d = diff_scopes(_scope(v1_scope_dict), _scope(v2))
        assert set(d.added_aliases) == {"branch_dim"}
        assert d.removed_aliases == {}
        assert d.affected_aliases == {"branch_dim"}
        assert d.has_breaking_changes is False

    def test_removed_alias_is_breaking(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"] = []
        d = diff_scopes(_scope(v1_scope_dict), _scope(v2))
        assert set(d.removed_aliases) == {"deposits_daily"}
        assert d.has_breaking_changes is True

    def test_projection_change_is_changed_not_added(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"][0]["projection"]["columns"] = ["DAT", "SEGMENT"]
        d = diff_scopes(_scope(v1_scope_dict), _scope(v2))
        assert set(d.changed_aliases) == {"deposits_daily"}
        assert "deposits_daily" in d.affected_aliases

    def test_routing_decision_change_is_changed(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"][0]["routing"]["decision"] = "lazy"
        d = diff_scopes(_scope(v1_scope_dict), _scope(v2))
        assert "deposits_daily" in d.changed_aliases


# ── Diff: pinned filters + pin-state flips ─────────────────────────────────

class TestFilterDiff:

    def test_added_pinned_filter(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["filters"]["pinned"].append({
            "id": "pf_q4", "concept": "as_of_time", "op": "between",
            "from": "2025-10-01", "to": "2025-12-31",
            "applies_to": ["deposits_daily"],
        })
        d = diff_scopes(_scope(v1_scope_dict), _scope(v2))
        assert len(d.filter_changes) == 1
        fc = d.filter_changes[0]
        assert fc.filter_id == "pf_q4"
        assert fc.old is None and fc.new is not None
        # applies_to: ["deposits_daily"] → alias must be re-fetched
        assert "deposits_daily" in d.affected_aliases

    def test_modified_pinned_value_triggers_alias_refetch(self, v1_scope_dict):
        v1 = copy.deepcopy(v1_scope_dict)
        v1["scope"]["filters"]["pinned"].append({
            "id": "pf_q4", "concept": "as_of_time", "op": "between",
            "from": "2025-10-01", "to": "2025-12-31",
            "applies_to": ["deposits_daily"],
        })
        v2 = copy.deepcopy(v1)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["filters"]["pinned"][0]["to"] = "2026-03-31"
        d = diff_scopes(_scope(v1), _scope(v2))
        # Same id, value changed → modify, alias re-fetched.
        assert d.filter_changes[0].old is not None
        assert d.filter_changes[0].new is not None
        assert "deposits_daily" in d.affected_aliases

    def test_pin_state_flip_does_not_force_refetch(self, v1_scope_dict):
        """Per §10.e bullet 5: moving a concept pinned→interactive doesn't
        re-fetch. Schema enforces distinct id prefixes (pf_/if_), so flips
        are detected by concept-equality, not id-equality."""
        v1 = copy.deepcopy(v1_scope_dict)
        v1["scope"]["filters"]["pinned"].append({
            "id": "pf_cur", "concept": "currency", "op": "in",
            "values": ["TRY"], "applies_to": [],
        })
        v2 = copy.deepcopy(v1)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["filters"]["pinned"] = []
        # New interactive filter on the same concept, with a fresh if_* id.
        v2["scope"]["filters"]["interactive"].append({
            "id": "if_cur", "concept": "currency", "op": "in",
            "default_values": ["TRY"], "applies_to": [],
        })
        d = diff_scopes(_scope(v1), _scope(v2))
        assert any(p.direction == "pinned_to_interactive" for p in d.pin_state_flips)
        # applies_to was empty → no alias forced into re-fetch.
        assert d.affected_aliases == set()


# ── Impact: blocks affected by diff ─────────────────────────────────────────

def _manifest_with(blocks: list[dict]) -> dict:
    return {"blocks": blocks}


class TestImpact:

    def test_block_referencing_removed_alias_is_breaking(self):
        diff = ScopeDiff(removed_aliases={"deposits_daily": None})
        manifest = _manifest_with([{
            "id": "h", "type": "section_header", "title": "S", "children": [{
                "id": "b1", "type": "bar_chart", "title": "Şube",
                "data_source": {"sql": "SELECT * FROM deposits_daily LIMIT 10"},
            }],
        }])
        out = compute_affected_blocks(diff, manifest)
        assert len(out) == 1
        assert out[0].severity == "breaking"
        assert "deposits_daily" in out[0].reasons[0]

    def test_block_referencing_changed_alias_is_warning(self):
        diff = ScopeDiff(changed_aliases={"deposits_daily": (None, None)})
        manifest = _manifest_with([{
            "id": "h", "type": "section_header", "title": "S", "children": [{
                "id": "b1", "type": "kpi", "title": "Toplam",
                "data_source": {"sql": "SELECT SUM(BALANCE_TRY) FROM deposits_daily"},
            }],
        }])
        out = compute_affected_blocks(diff, manifest)
        assert len(out) == 1
        assert out[0].severity == "warning"

    def test_variable_binding_to_removed_filter_is_breaking(self):
        from presentations.scope.diff import FilterChange
        diff = ScopeDiff(filter_changes=[FilterChange("pf_q4", old=object(), new=None)])
        manifest = _manifest_with([{
            "id": "h", "type": "section_header", "title": "S", "children": [{
                "id": "b1", "type": "kpi", "title": "X",
                "data_source": {"sql": "SELECT 1 FROM other_alias"},
                "variable_bindings": {"period": {"from_scope_filter": "pf_q4"}},
            }],
        }])
        out = compute_affected_blocks(diff, manifest)
        assert len(out) == 1
        assert out[0].severity == "breaking"

    def test_pin_state_flip_is_warning_not_breaking(self):
        diff = ScopeDiff(pin_state_flips=[PinStateFlip("pf_cur", "pinned_to_interactive")])
        manifest = _manifest_with([{
            "id": "h", "type": "section_header", "title": "S", "children": [{
                "id": "b1", "type": "kpi", "title": "X",
                "data_source": {"sql": "SELECT 1 FROM other_alias"},
                "variable_bindings": {"cur": {"from_scope_filter": "pf_cur"}},
            }],
        }])
        out = compute_affected_blocks(diff, manifest)
        assert len(out) == 1
        assert out[0].severity == "warning"

    def test_unrelated_block_not_flagged(self):
        diff = ScopeDiff(removed_aliases={"deposits_daily": None})
        manifest = _manifest_with([{
            "id": "h", "type": "section_header", "title": "S", "children": [{
                "id": "narrative_1", "type": "narrative", "title": "Note",
                # no data_source / no variable_bindings — unaffected.
            }],
        }])
        out = compute_affected_blocks(diff, manifest)
        assert out == []

    def test_empty_diff_yields_empty_affected(self):
        out = compute_affected_blocks(ScopeDiff(), _manifest_with([]))
        assert out == []

    def test_summarise_counts(self):
        blocks = [
            AffectedBlock("b1", "T1", "kpi", "breaking", ["r"]),
            AffectedBlock("b2", "T2", "bar_chart", "warning", ["r"]),
            AffectedBlock("b3", "T3", "kpi", "warning", ["r"]),
        ]
        assert summarise(blocks) == {"breaking": 1, "warning": 2, "total": 3}

    def test_serialise_round_trip(self):
        blocks = [AffectedBlock("b1", "T1", "kpi", "breaking", ["r1", "r2"])]
        out = serialise_affected(blocks)
        assert out == [{
            "block_id": "b1", "block_title": "T1", "block_type": "kpi",
            "severity": "breaking", "reasons": ["r1", "r2"],
        }]


# ── Diff serialisation for the UI ───────────────────────────────────────────

class TestDiffSerialise:

    def test_added_alias_serialises(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"].append({
            "alias": "branch_dim",
            "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
            "projection": {"columns": ["BRANCH_CODE"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system", "estimated_bytes": 100},
        })
        out = serialise_diff(diff_scopes(_scope(v1_scope_dict), _scope(v2)))
        assert out["added"] == ["branch_dim"]
        assert out["breaking"] is False

    def test_removed_alias_serialises_with_breaking(self, v1_scope_dict):
        v2 = copy.deepcopy(v1_scope_dict)
        v2["scope"]["version"] = 2
        v2["scope"]["parent_version"] = 1
        v2["scope"]["basket"] = []
        out = serialise_diff(diff_scopes(_scope(v1_scope_dict), _scope(v2)))
        assert out["removed"] == ["deposits_daily"]
        assert out["breaking"] is True

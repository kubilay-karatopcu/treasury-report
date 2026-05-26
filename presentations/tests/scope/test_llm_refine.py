"""Phase 8.f tests: scope-refinement LLM client + mutators.

Acceptance §10.f:
  - Stage 2 chat returns JSON matching the contract in §5.3 for representative
    user messages.
  - Invalid LLM output is caught and retried once with error feedback (existing
    Phase 3 pattern).
  - "Apply" on each suggestion kind correctly mutates the scope contract draft.

We exercise both sides:
  - ``FakeLLM.suggest_scope_refinements`` for the 5 canned-intent paths.
  - ``_mutate_scope_with_suggestion`` for each ``kind`` (and a few rejection
    rules that the apply endpoint relies on for safety).
"""
from __future__ import annotations

import json
import re

import pytest

from presentations.llm import FakeLLM, _parse_scope_output
from presentations.routes_scope import (
    _ApplyError,
    _mutate_scope_with_suggestion,
)


# ── Shared draft scope used as a starting point ────────────────────────────

@pytest.fixture
def draft_scope() -> dict:
    """A minimal valid draft: 2 raw basket items + 1 existing interactive
    filter on as_of_time. Mirrors what the frontend would POST to the chat
    endpoint."""
    return {
        "presentation_id": "p_test",
        "version": 1,
        "owner_id": "A16438",
        "created_by": "A16438",
        "basket": [
            {
                "alias": "deposits_daily",
                "table_ref": {"schema": "EDW", "name": "DEPOSITS_DAILY"},
                "projection": {"columns": ["DAT", "SEGMENT", "BALANCE_TRY"], "include_all": False},
                "routing": {"decision": "cached", "decided_by": "system"},
            },
            {
                "alias": "branch_dim",
                "table_ref": {"schema": "EDW", "name": "BRANCH_DIM"},
                "projection": {"columns": ["BRANCH_CODE", "BRANCH_NAME"], "include_all": False},
                "routing": {"decision": "cached", "decided_by": "system"},
            },
        ],
        "filters": {
            "pinned": [],
            "interactive": [{
                "id": "if_period",
                "concept": "as_of_time",
                "op": "between",
                "default_values": ["2025-10-01", "2025-12-31"],
                "applies_to": [],
            }],
            "raw": [],
        },
        "joins": [],
    }


@pytest.fixture
def bound_concepts() -> list[dict]:
    return [
        {"concept": "as_of_time", "bound_in": ["deposits_daily.DAT"]},
        {"concept": "currency", "bound_in": ["deposits_daily.CUR"]},
        {"concept": "branch", "bound_in": ["deposits_daily.BRANCH_CODE", "branch_dim.BRANCH_CODE"]},
    ]


# ── FakeLLM contract coverage (§10.f bullet 1) ──────────────────────────────

class TestFakeLLMContract:
    """FakeLLM emits §5.3-shaped JSON for every supported intent. The real
    QwenClient uses the same prompt + parser, so contract correctness here
    propagates."""

    def test_response_shape(self, draft_scope, bound_concepts):
        f = FakeLLM()
        r = f.suggest_scope_refinements(draft_scope, "merhaba", bound_concepts, [])
        assert isinstance(r, dict)
        assert "explanation" in r and isinstance(r["explanation"], str)
        assert "suggestions" in r and isinstance(r["suggestions"], list)

    def test_q4_pin_intent(self, draft_scope, bound_concepts):
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "Q4 2025'e kilitle", bound_concepts, [])
        assert len(r["suggestions"]) == 1
        s = r["suggestions"][0]
        assert s["kind"] == "add_filter"
        assert s["mode"] == "pinned"
        assert s["concept"] == "as_of_time"
        assert s["op"] == "between"
        assert s["from"] == "2025-10-01" and s["to"] == "2025-12-31"

    def test_currency_filter_intent(self, draft_scope, bound_concepts):
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "sadece TL cinsi göster", bound_concepts, [])
        kinds = [s["kind"] for s in r["suggestions"]]
        assert "add_filter" in kinds
        f = next(s for s in r["suggestions"] if s["kind"] == "add_filter")
        assert f["concept"] == "currency"
        assert f["values"] == ["TRY"]

    def test_aggregate_intent(self, draft_scope, bound_concepts):
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "şube bazında topla", bound_concepts, [])
        assert any(s["kind"] == "create_aggregate" for s in r["suggestions"])
        s = next(s for s in r["suggestions"] if s["kind"] == "create_aggregate")
        assert s["source_alias"] == "deposits_daily"
        assert s["group_by"] == ["BRANCH_CODE"]
        # Alias regex requires ASCII (no Turkish chars).
        assert re.fullmatch(r"[a-z][a-z0-9_]*", s["new_alias"])
        assert s["measures"][0]["fn"] == "sum"

    def test_new_table_intent_is_rejected(self, draft_scope, bound_concepts):
        """Stage 2 LLM never proposes new tables — that's Stage 1's job."""
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "loans tablosunu da ekle", bound_concepts, [])
        assert r["suggestions"] == []
        assert "kataloğ" in r["explanation"].lower() or "katalog" in r["explanation"].lower()

    def test_unbound_concept_not_suggested(self, draft_scope):
        """Without a bound concept, the LLM must clarify rather than fabricate."""
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "Q4 2025'e kilitle", bound_concepts=[], catalog_excerpt=[])
        # Should NOT emit an as_of_time filter — no binding to attach it to.
        assert all(
            not (s.get("kind") == "add_filter" and s.get("concept") == "as_of_time")
            for s in r["suggestions"]
        )


# ── Parser robustness (§10.f bullet 2: invalid output handling) ─────────────

class TestScopeOutputParser:
    def test_plain_json(self):
        r = _parse_scope_output('{"explanation":"x","suggestions":[]}')
        assert r == {"explanation": "x", "suggestions": []}

    def test_fenced_code_block(self):
        r = _parse_scope_output('```json\n{"explanation":"y","suggestions":[]}\n```')
        assert r["explanation"] == "y"

    def test_prose_around_json(self):
        r = _parse_scope_output('here is your answer: {"explanation":"z","suggestions":[]} ok?')
        assert r["explanation"] == "z"

    def test_garbage_marks_invalid(self):
        r = _parse_scope_output("not json at all")
        assert "_invalid" in r

    def test_non_object_marks_invalid(self):
        r = _parse_scope_output('"a string"')
        assert "_invalid" in r

    def test_non_list_suggestions_invalid(self):
        r = _parse_scope_output('{"explanation":"x","suggestions":"oops"}')
        assert "_invalid" in r


# ── Mutator coverage (§10.f bullet 3) ───────────────────────────────────────

class TestMutators:
    def test_add_filter_pinned(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_filter",
            "mode": "pinned",
            "concept": "currency",
            "op": "in",
            "values": ["TRY"],
            "applies_to": [],
        })
        pin = out["filters"]["pinned"]
        assert len(pin) == 1
        assert pin[0]["concept"] == "currency"
        assert pin[0]["values"] == ["TRY"]
        assert re.fullmatch(r"pf_[a-z0-9_-]+", pin[0]["id"])

    def test_add_filter_interactive(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_filter",
            "mode": "interactive",
            "concept": "branch",
            "op": "in",
            "default_values": ["B01"],
            "applies_to": ["deposits_daily"],
        })
        ic = out["filters"]["interactive"]
        # 1 pre-existing + 1 new.
        assert len(ic) == 2
        new = next(f for f in ic if f["concept"] == "branch")
        assert re.fullmatch(r"if_[a-z0-9_-]+", new["id"])
        assert new["default_values"] == ["B01"]

    def test_pin_filter_promotes_interactive(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "pin_filter", "filter_id": "if_period",
        })
        assert out["filters"]["interactive"] == []
        assert len(out["filters"]["pinned"]) == 1
        pf = out["filters"]["pinned"][0]
        assert pf["concept"] == "as_of_time"
        assert pf["op"] == "between"
        assert pf["from"] == "2025-10-01"
        assert pf["to"] == "2025-12-31"

    def test_pin_filter_unknown_id_rejected(self, draft_scope):
        with pytest.raises(_ApplyError, match="if_nope"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "pin_filter", "filter_id": "if_nope",
            })

    def test_add_projection_column(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_projection_column",
            "alias": "deposits_daily",
            "column": "BRANCH_CODE",
        })
        cols = next(b for b in out["basket"] if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert "BRANCH_CODE" in cols
        # Idempotent: applying twice doesn't duplicate.
        out2 = _mutate_scope_with_suggestion(out, {
            "kind": "add_projection_column",
            "alias": "deposits_daily",
            "column": "BRANCH_CODE",
        })
        cols2 = next(b for b in out2["basket"] if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert cols2.count("BRANCH_CODE") == 1

    def test_add_projection_column_unknown_alias_rejected(self, draft_scope):
        with pytest.raises(_ApplyError, match="x_alias"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "add_projection_column", "alias": "x_alias", "column": "C",
            })

    def test_confirm_join(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "confirm_join",
            "left_alias": "deposits_daily", "left_column": "BRANCH_CODE",
            "right_alias": "branch_dim", "right_column": "BRANCH_CODE",
            "kind_of_join": "lookup",
        })
        assert len(out["joins"]) == 1
        j = out["joins"][0]
        assert j["left"] == {"alias": "deposits_daily", "column": "BRANCH_CODE"}
        assert j["right"] == {"alias": "branch_dim", "column": "BRANCH_CODE"}
        assert j["kind"] == "lookup"
        assert re.fullmatch(r"j_[a-z0-9_-]+", j["id"])

    def test_confirm_join_duplicate_rejected(self, draft_scope):
        s = dict(draft_scope)
        s["joins"] = [{
            "id": "j_existing",
            "left": {"alias": "deposits_daily", "column": "BRANCH_CODE"},
            "right": {"alias": "branch_dim", "column": "BRANCH_CODE"},
            "kind": "lookup",
        }]
        with pytest.raises(_ApplyError, match="zaten kayıtlı"):
            _mutate_scope_with_suggestion(s, {
                "kind": "confirm_join",
                "left_alias": "deposits_daily", "left_column": "BRANCH_CODE",
                "right_alias": "branch_dim", "right_column": "BRANCH_CODE",
                "kind_of_join": "lookup",
            })

    def test_confirm_join_unknown_alias_rejected(self, draft_scope):
        with pytest.raises(_ApplyError, match="basket'te yok"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "confirm_join",
                "left_alias": "ghost", "left_column": "X",
                "right_alias": "deposits_daily", "right_column": "DAT",
            })

    def test_create_aggregate(self, draft_scope):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "create_aggregate",
            "source_alias": "deposits_daily",
            "new_alias": "deposits_by_branch",
            "group_by": ["BRANCH_CODE"],
            "measures": [{"column": "BALANCE_TRY", "fn": "sum", "as": "SUM_BAL"}],
        })
        new = next(b for b in out["basket"] if b.get("alias") == "deposits_by_branch")
        assert new["derivation"]["kind"] == "aggregate"
        assert new["derivation"]["source_alias"] == "deposits_daily"
        assert new["derivation"]["group_by"] == ["BRANCH_CODE"]
        assert new["derivation"]["measures"][0]["as"] == "SUM_BAL"
        # Projection mirrors the derivation output.
        assert new["projection"]["columns"] == ["BRANCH_CODE", "SUM_BAL"]
        assert new["projection"]["include_all"] is False

    def test_create_aggregate_from_derived_rejected(self, draft_scope):
        s = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "create_aggregate",
            "source_alias": "deposits_daily",
            "new_alias": "deposits_by_branch",
            "group_by": ["BRANCH_CODE"],
            "measures": [{"column": "BALANCE_TRY", "fn": "sum", "as": "SUM_BAL"}],
        })
        # Chaining: aggregate of an aggregate is rejected — must be from raw.
        with pytest.raises(_ApplyError, match="türetilmiş tablo olamaz"):
            _mutate_scope_with_suggestion(s, {
                "kind": "create_aggregate",
                "source_alias": "deposits_by_branch",
                "new_alias": "second_agg",
                "group_by": ["BRANCH_CODE"],
                "measures": [{"column": "SUM_BAL", "fn": "max", "as": "MAX"}],
            })

    def test_create_aggregate_alias_clash_rejected(self, draft_scope):
        with pytest.raises(_ApplyError, match="zaten mevcut"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "create_aggregate",
                "source_alias": "deposits_daily",
                "new_alias": "branch_dim",   # already in basket
                "group_by": ["BRANCH_CODE"],
                "measures": [],
            })

    def test_unknown_kind_rejected(self, draft_scope):
        with pytest.raises(_ApplyError, match="Bilinmeyen"):
            _mutate_scope_with_suggestion(draft_scope, {"kind": "no_such_kind"})

    def test_mutator_is_pure(self, draft_scope):
        """The mutator must not mutate its input (deep copies internally)."""
        snapshot = json.dumps(draft_scope, sort_keys=True, default=str)
        _ = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_filter", "mode": "pinned",
            "concept": "currency", "op": "in", "values": ["TRY"],
        })
        assert json.dumps(draft_scope, sort_keys=True, default=str) == snapshot

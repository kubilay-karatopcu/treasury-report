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

    def test_aggregate_intent_emits_python_node(self, draft_scope, bound_concepts):
        """Politika: tek-kaynak agregasyon HER ZAMAN Python — stub da
        create_aggregate DEĞİL create_python_node üretmeli (apply reddediyor)."""
        r = FakeLLM().suggest_scope_refinements(
            draft_scope, "şube bazında topla", bound_concepts, [])
        assert any(s["kind"] == "create_python_node" for s in r["suggestions"])
        assert not any(s["kind"] == "create_aggregate" for s in r["suggestions"])
        s = next(s for s in r["suggestions"] if s["kind"] == "create_python_node")
        assert s["source_alias"] == "deposits_daily"
        assert "groupby('BRANCH_CODE'" in s["python_code"]
        assert "output_node_df" in s["python_code"]
        # Alias regex requires ASCII (no Turkish chars).
        assert re.fullmatch(r"[a-z][a-z0-9_]*", s["new_alias"])

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

    def test_create_aggregate_rejected(self, draft_scope):
        """Politika: create_aggregate kaldırıldı — apply net redle Python'a
        yönlendirir (sessizce SQL agregasyon uygulamak yasak)."""
        with pytest.raises(_ApplyError, match="create_python_node"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "create_aggregate",
                "source_alias": "deposits_daily",
                "new_alias": "deposits_by_branch",
                "group_by": ["BRANCH_CODE"],
                "measures": [{"column": "BALANCE_TRY", "fn": "sum", "as": "SUM_BAL"}],
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


# ── Kolon kanonikleştirme + LLM bağlam kolon-hint'i (M3 "kolon yok" fix'i) ──

DOC_COLS = [{"name": n} for n in
            ["DAT", "SEGMENT", "BALANCE_TRY", "BRANCH_CODE", "CCY_CODE"]]


@pytest.fixture
def patched_columns_for(monkeypatch):
    """_columns_for'u app-context'siz, sabit doc kolonlarıyla stub'la."""
    import presentations.routes_scope as rs
    monkeypatch.setattr(rs, "_columns_for", lambda schema, name: list(DOC_COLS))


class TestColumnCanonicalization:
    """LLM'in verdiği kolon adları apply'da doc'a göre kanonikleşir: case
    farkı düzelir, gerçekten olmayan kolon actionable hatayla reddedilir,
    evren BİLİNEMİYORSA asla false-block yapılmaz (kullanıcının 'kolon
    vardı ama yok dedi' semptomunun apply ayağı)."""

    def test_projection_column_case_is_canonicalized(self, draft_scope, patched_columns_for):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_projection_column",
            "alias": "deposits_daily",
            "column": "branch_code",       # LLM küçük harf yazdı
        })
        cols = next(b for b in out["basket"]
                    if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert "BRANCH_CODE" in cols and "branch_code" not in cols

    def test_projection_column_beyond_projection_is_allowed(self, draft_scope, patched_columns_for):
        """Projection'da OLMAYAN ama doc'ta OLAN kolon eklenebilmeli —
        projection alt kümesi validasyon evreni DEĞİLDİR."""
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_projection_column",
            "alias": "deposits_daily",
            "column": "CCY_CODE",          # projection'da yok, doc'ta var
        })
        cols = next(b for b in out["basket"]
                    if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert "CCY_CODE" in cols

    def test_truly_unknown_column_rejected_with_available_list(self, draft_scope, patched_columns_for):
        with pytest.raises(_ApplyError, match="Mevcut kolonlar.*BRANCH_CODE"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "add_projection_column",
                "alias": "deposits_daily",
                "column": "GHOST_COL",
            })

    def test_unknown_universe_passes_through(self, draft_scope, monkeypatch):
        """Doc yok / app context yok → evren bilinemez → dokunma (false-red yok)."""
        import presentations.routes_scope as rs
        monkeypatch.setattr(rs, "_columns_for",
                            lambda schema, name: (_ for _ in ()).throw(RuntimeError()))
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "add_projection_column",
            "alias": "deposits_daily",
            "column": "WHATEVER_COL",
        })
        cols = next(b for b in out["basket"]
                    if b["alias"] == "deposits_daily")["projection"]["columns"]
        assert "WHATEVER_COL" in cols

    def test_confirm_join_canonicalizes_both_sides(self, draft_scope, patched_columns_for):
        out = _mutate_scope_with_suggestion(draft_scope, {
            "kind": "confirm_join",
            "left_alias": "deposits_daily", "left_column": "branch_code",
            "right_alias": "branch_dim", "right_column": " BRANCH_CODE ",
            "kind_of_join": "lookup",
        })
        j = out["joins"][0]
        assert j["left"]["column"] == "BRANCH_CODE"
        assert j["right"]["column"] == "BRANCH_CODE"

    def test_confirm_join_unknown_column_rejected(self, draft_scope, patched_columns_for):
        with pytest.raises(_ApplyError, match="confirm_join\\(left\\)"):
            _mutate_scope_with_suggestion(draft_scope, {
                "kind": "confirm_join",
                "left_alias": "deposits_daily", "left_column": "NOPE",
                "right_alias": "branch_dim", "right_column": "BRANCH_CODE",
            })


class TestColumnsHintForAlias:
    """scope_chat ODAK bağlamı: türetilmiş node'ların kolonları LLM'e gitsin
    ('kolon yok' semptomunun bağlam ayağı)."""

    def _basket(self):
        return [
            {"alias": "daily",
             "table_ref": {"schema": "EDW", "name": "MYU_DAILY_RES"},
             "projection": {"columns": ["RES_ID"], "include_all": False}},
            {"alias": "py_node",
             "derivation": {"kind": "python", "source_alias": "daily",
                            "python_code": "output_node_df = input_node_df",
                            "output_columns": ["CCY_CODE", "TOTAL", "PAY_PCT"]},
             "projection": {"columns": [], "include_all": True}},
            {"alias": "agg_node",
             "derivation": {"kind": "aggregate", "source_alias": "daily",
                            "group_by": ["CCY_CODE"],
                            "measures": [{"column": "AMT", "fn": "sum", "as": "TOTAL_AMT"}]},
             "projection": {"columns": [], "include_all": True}},
            {"alias": "f_node",
             "derivation": {"kind": "filter", "source_alias": "py_node",
                            "filters": {"raw": [{"id": "rf_1", "alias": "py_node",
                                                 "column": "CCY_CODE", "op": "eq",
                                                 "value": "TRY"}]}},
             "projection": {"columns": [], "include_all": True}},
        ]

    def test_python_node_uses_output_columns(self):
        from presentations.routes_scope import _columns_hint_for_alias
        assert _columns_hint_for_alias(self._basket(), "py_node") == \
            ["CCY_CODE", "TOTAL", "PAY_PCT"]

    def test_aggregate_node_derives_from_definition(self):
        from presentations.routes_scope import _columns_hint_for_alias
        assert _columns_hint_for_alias(self._basket(), "agg_node") == \
            ["CCY_CODE", "TOTAL_AMT"]

    def test_filter_node_recurses_to_source(self):
        from presentations.routes_scope import _columns_hint_for_alias
        assert _columns_hint_for_alias(self._basket(), "f_node") == \
            ["CCY_CODE", "TOTAL", "PAY_PCT"]

    def test_unrun_python_node_is_unknown_in_strict_mode(self):
        from presentations.routes_scope import _columns_hint_for_alias
        basket = self._basket()
        basket[1]["derivation"]["output_columns"] = []
        basket[1]["projection"] = {"columns": ["STALE"], "include_all": False}
        # strict (validasyon): bilinemez → None (asla projection'ı evren sayma)
        assert _columns_hint_for_alias(basket, "py_node", strict=True) is None
        # hint (chat): projection'a düşebilir
        assert _columns_hint_for_alias(basket, "py_node") == ["STALE"]

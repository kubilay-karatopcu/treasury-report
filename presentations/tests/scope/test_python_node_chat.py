"""Faz P-3 — node-scope chat: create_python_node önerisi + apply mutator + bağlam.

- FakeLLM.suggest_scope_refinements selected_alias + veri-işlem mesajıyla
  create_python_node üretir.
- compose_scope_user_message seçili node bloğunu (ODAK) ekler.
- _mutate_scope_with_suggestion create_python_node'u uygular; kötü script /
  eksik kaynak reddedilir (validate_python guard).
"""
from __future__ import annotations

import pytest

from presentations.llm import FakeLLM, compose_scope_user_message
from presentations.routes_scope import _ApplyError, _mutate_scope_with_suggestion


def _scope():
    return {
        "presentation_id": "p_test", "version": 1, "created_by": "A16438",
        "basket": [
            {"alias": "deposits", "table_ref": {"schema": "EDW", "name": "DEPOSITS"},
             "projection": {"columns": ["BRANCH_CODE", "BALANCE_TRY"], "include_all": False},
             "routing": {"decision": "cached", "decided_by": "system"}},
        ],
        "filters": {"pinned": [], "interactive": [], "raw": []}, "joins": [],
    }


# ── Bağlam (compose) ────────────────────────────────────────────────────────

def test_compose_includes_selected_node_block():
    msg = compose_scope_user_message(
        _scope(), "kümülatif topla",
        selected_alias="deposits", selected_columns=["BRANCH_CODE", "BALANCE_TRY"],
    )
    assert "Seçili node (ODAK)" in msg
    assert "deposits" in msg
    assert "create_python_node" in msg
    assert "BALANCE_TRY" in msg


def test_compose_without_selection_has_no_focus_block():
    msg = compose_scope_user_message(_scope(), "merhaba")
    assert "Seçili node (ODAK)" not in msg


# ── FakeLLM stub ────────────────────────────────────────────────────────────

def test_stub_suggests_python_node_in_node_scope():
    llm = FakeLLM()
    out = llm.suggest_scope_refinements(
        _scope(), "bunu python ile kümülatif topla",
        selected_alias="deposits", selected_columns=["BALANCE_TRY"],
    )
    kinds = [s["kind"] for s in out["suggestions"]]
    assert "create_python_node" in kinds
    sg = out["suggestions"][0]
    assert sg["source_alias"] == "deposits"
    assert "output_node_df" in sg["python_code"]


def test_stub_no_python_without_selection():
    llm = FakeLLM()
    out = llm.suggest_scope_refinements(_scope(), "python ile hesapla")  # node seçili değil
    kinds = [s["kind"] for s in out["suggestions"]]
    assert "create_python_node" not in kinds


# ── Apply mutator ───────────────────────────────────────────────────────────

def _sugg(**kw):
    base = {"kind": "create_python_node", "source_alias": "deposits",
            "new_alias": "deposits_py",
            "python_code": "output_node_df = input_node_df.head(5)"}
    base.update(kw)
    return base


def test_apply_adds_python_node():
    out = _mutate_scope_with_suggestion(_scope(), _sugg())
    py = next(b for b in out["basket"] if b["alias"] == "deposits_py")
    assert py["derivation"]["kind"] == "python"
    assert py["derivation"]["source_alias"] == "deposits"


def test_apply_rejects_unknown_source():
    with pytest.raises(_ApplyError):
        _mutate_scope_with_suggestion(_scope(), _sugg(source_alias="nope"))


def test_apply_rejects_bad_script():
    with pytest.raises(_ApplyError):
        _mutate_scope_with_suggestion(_scope(), _sugg(python_code="import os\noutput_node_df = input_node_df"))


def test_apply_rejects_missing_output():
    with pytest.raises(_ApplyError):
        _mutate_scope_with_suggestion(_scope(), _sugg(python_code="x = input_node_df"))


def test_apply_uniquifies_alias_collision():
    s = _scope()
    s["basket"].append({
        "alias": "deposits_py", "table_ref": {"schema": "EDW", "name": "X"},
        "projection": {"columns": [], "include_all": True},
        "routing": {"decision": "cached", "decided_by": "system"}})
    out = _mutate_scope_with_suggestion(s, _sugg())
    aliases = [b["alias"] for b in out["basket"]]
    assert "deposits_py_2" in aliases

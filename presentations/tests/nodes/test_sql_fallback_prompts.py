"""_build_sql_fallback_prompts — fallback SQL mini-call'unun prompt sözleşmesi:
{{concept_filters}} sentinel'i + Hazırlık-DuckDB lehçe kuralı + view bağlamı."""
from __future__ import annotations

from presentations.nodes.generate_patch import (
    _build_sql_fallback_prompts, _scope_views_context,
)


def test_system_prompt_carries_both_rules():
    sys, _ = _build_sql_fallback_prompts("toplamı getir", {"type": "kpi"}, None)
    assert "{{concept_filters}}" in sys
    assert "DuckDB lehçesinde" in sys
    assert "FETCH FIRST yerine LIMIT" in sys


def test_views_context_rendered_in_user_prompt():
    _, user = _build_sql_fallback_prompts(
        "ccy bazında topla", {"type": "bar_chart", "title": "CCY"},
        None, views_ctx="- agg_ccy: CCY_CODE, TOTAL_AMT")
    assert "# Hazırlık view'ları" in user
    assert "agg_ccy: CCY_CODE, TOTAL_AMT" in user


def test_no_views_no_section():
    _, user = _build_sql_fallback_prompts("x", {"type": "kpi"}, None, views_ctx="")
    assert "# Hazırlık view'ları" not in user


def test_scope_views_context_reads_projection_and_output_columns():
    class _Proj:
        columns = ["A", "B"]

    class _Deriv:
        output_columns = ["C", "D"]

    class _Main:
        alias, projection, derivation = "daily", _Proj(), None

    class _Py:
        alias, derivation = "py_node", _Deriv()
        class projection:  # boş projection (include_all)
            columns = []

    class _State:
        class scope_contract:
            basket = [_Main(), _Py()]

    ctx = _scope_views_context(_State())
    assert "- daily: A, B" in ctx
    assert "- py_node: C, D" in ctx

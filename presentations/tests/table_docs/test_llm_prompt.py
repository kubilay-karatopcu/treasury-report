"""Tests for the Phase 6.5.b LLM prompt context injection.

Acceptance §10.b: 'LLM block-authoring chat includes suggested_variable,
suggested_semantic_tag, distinct_values_sample in its context.'

We don't invoke a real LLM here — that's the test_llm_smoke marker. This
suite verifies the prompt *string* the LLM would see carries the right
fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from presentations.llm import _table_docs_section, compose_user_message
from presentations.table_docs.schema import ColumnDoc, TableDoc


def _example_doc():
    return TableDoc(
        table="DEPOSITS_DAILY",
        schema="EDW",
        description="Günlük mevduat snapshot'ı.",
        partition_column="DATE",
        columns={
            "DATE": ColumnDoc(
                type="DATE",
                filterable=True,
                filter_role="time_axis",
                suggested_variable="as_of_date",
                suggested_semantic_tag="as_of_time",
            ),
            "SEGMENT": ColumnDoc(
                type="VARCHAR2(20)",
                filterable=True,
                filter_role="dimension",
                suggested_variable="segments",
                suggested_semantic_tag="segment",
                distinct_values_sample=["RETAIL", "CORPORATE", "SME"],
                distinct_values_sampled_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
            ),
            "BALANCE_TRY": ColumnDoc(
                type="NUMBER(18,2)",
                filterable=False,
                aggregatable=True,
            ),
            "CREATED_AT": ColumnDoc(
                type="TIMESTAMP",
                visible_in_ui=False,
            ),
        },
    )


class TestTableDocsSection:
    def test_empty_input_returns_empty(self):
        assert _table_docs_section(None) == ""
        assert _table_docs_section([]) == ""

    def test_includes_table_header(self):
        out = _table_docs_section([_example_doc()])
        assert "EDW.DEPOSITS_DAILY" in out
        assert "partitioned: DATE" in out

    def test_includes_suggested_variable(self):
        out = _table_docs_section([_example_doc()])
        assert ":as_of_date" in out
        assert ":segments" in out

    def test_includes_semantic_tag(self):
        out = _table_docs_section([_example_doc()])
        assert "semantic_tag=as_of_time" in out
        assert "semantic_tag=segment" in out

    def test_includes_distinct_values_sample(self):
        out = _table_docs_section([_example_doc()])
        assert "'RETAIL'" in out
        assert "'CORPORATE'" in out
        assert "'SME'" in out

    def test_skips_invisible_columns(self):
        out = _table_docs_section([_example_doc()])
        # CREATED_AT is visible_in_ui=False — should not appear at all.
        assert "CREATED_AT" not in out

    def test_skips_non_filterable_columns(self):
        out = _table_docs_section([_example_doc()])
        # BALANCE_TRY isn't filterable, so it shouldn't appear in the
        # filter-affordances listing the LLM sees.
        assert "BALANCE_TRY" not in out


class TestComposeUserMessage:
    def test_table_docs_section_appears_when_provided(self):
        msg = compose_user_message(
            manifest={"blocks": []},
            selected_block_id=None,
            user_message="bana segment dağılımı çıkar",
            table_docs=[_example_doc()],
        )
        assert "## Tablo dokümantasyonu" in msg
        assert "EDW.DEPOSITS_DAILY" in msg

    def test_absent_when_no_table_docs(self):
        msg = compose_user_message(
            manifest={"blocks": []},
            selected_block_id=None,
            user_message="anything",
        )
        assert "## Tablo dokümantasyonu" not in msg

    def test_phase_65_hint_in_user_message_section(self):
        # The talep section needs to include the user message body.
        msg = compose_user_message(
            manifest={"blocks": []},
            selected_block_id=None,
            user_message="UYGUN_TALEP",
            table_docs=[_example_doc()],
        )
        assert "UYGUN_TALEP" in msg

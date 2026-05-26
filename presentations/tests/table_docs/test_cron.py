"""Tests for jobs.sample_distinct_values cron behaviour (dry-run + writeback).

We exercise the ``refresh_doc`` function directly against a stub DataClient,
so the test runs without DuckDB or Oracle plumbing. The stub returns
fixed value sets per column.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from jobs.sample_distinct_values import refresh_doc
from presentations.table_docs.schema import ColumnDoc, TableDoc


class _StubDC:
    """Returns predetermined distinct-value sets keyed by column name."""

    def __init__(self, by_column: dict[str, list]):
        self.by_column = by_column
        self.calls: list[str] = []

    def get_data(self, base_prefix=None, dataset=None, query=None, query_params=None, **kw):
        self.calls.append(query)
        # Extract column name from the SELECT DISTINCT "col" clause.
        import re
        m = re.search(r'SELECT DISTINCT "([^"]+)"', query)
        if not m:
            return pd.DataFrame()
        col = m.group(1)
        vals = self.by_column.get(col, [])
        return pd.DataFrame({col: vals})


def _example_doc():
    return TableDoc(
        table="DEPOSITS_DAILY",
        schema="EDW",
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
                distinct_values_sample=["RETAIL"],
                distinct_values_sampled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            "PRODUCT_CODE": ColumnDoc(
                type="VARCHAR2(10)",
                filterable=True,
                filter_role="dimension",
                suggested_variable="products",
                suggested_semantic_tag="product_group",
            ),
        },
    )


class TestRefreshDoc:
    def test_dry_run_does_not_mutate(self):
        doc = _example_doc()
        dc = _StubDC({"SEGMENT": ["RETAIL", "CORPORATE", "SME"],
                       "PRODUCT_CODE": ["TR", "FX"]})
        before_segment = doc.columns["SEGMENT"].distinct_values_sample
        changed, lines = refresh_doc(doc, dc, dry_run=True)
        # dry_run reports the deltas but does not write them back.
        assert changed is False
        # Underlying values are unchanged.
        assert doc.columns["SEGMENT"].distinct_values_sample == before_segment
        assert doc.columns["PRODUCT_CODE"].distinct_values_sample is None

    def test_writeback_updates_dimension_columns(self):
        doc = _example_doc()
        dc = _StubDC({"SEGMENT": ["RETAIL", "CORPORATE", "SME"],
                       "PRODUCT_CODE": ["TR", "FX"]})
        changed, lines = refresh_doc(doc, dc, dry_run=False)
        assert changed is True
        assert doc.columns["SEGMENT"].distinct_values_sample == [
            "RETAIL", "CORPORATE", "SME",
        ]
        assert doc.columns["PRODUCT_CODE"].distinct_values_sample == ["TR", "FX"]
        # Timestamp was set.
        assert doc.columns["PRODUCT_CODE"].distinct_values_sampled_at is not None

    def test_time_axis_columns_skipped(self):
        # Spec: cron updates filter_role=dimension only. time_axis columns
        # are continuous (dates); a sample is meaningless.
        doc = _example_doc()
        dc = _StubDC({"DATE": ["2026-01-01", "2026-01-02"]})
        refresh_doc(doc, dc, dry_run=False)
        # DATE column should be untouched.
        assert doc.columns["DATE"].distinct_values_sample is None
        # The cron should NOT have queried DATE.
        assert not any('"DATE"' in q for q in dc.calls)

    def test_unchanged_values_not_re_written(self):
        doc = _example_doc()
        # SEGMENT already has ["RETAIL"]; cron returns the same.
        dc = _StubDC({"SEGMENT": ["RETAIL"], "PRODUCT_CODE": ["TR"]})
        changed, _ = refresh_doc(doc, dc, dry_run=False)
        # PRODUCT_CODE was empty → changed. SEGMENT was equal → unchanged.
        assert changed is True
        # Confirm only PRODUCT_CODE got a new timestamp (SEGMENT's stays
        # at its original).
        seg_ts = doc.columns["SEGMENT"].distinct_values_sampled_at
        assert seg_ts == datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_empty_result_does_not_clobber(self):
        doc = _example_doc()
        dc = _StubDC({"SEGMENT": [], "PRODUCT_CODE": []})
        changed, _ = refresh_doc(doc, dc, dry_run=False)
        assert changed is False
        # Existing SEGMENT sample preserved.
        assert doc.columns["SEGMENT"].distinct_values_sample == ["RETAIL"]

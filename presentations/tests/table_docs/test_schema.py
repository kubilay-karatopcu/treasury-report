"""Tests for presentations.table_docs.schema — Phase 6.5.b."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from presentations.table_docs.schema import (
    ColumnDoc,
    LookupRef,
    TableDoc,
    load_table_doc_from_dict,
)


REPO = Path(__file__).resolve().parents[3]
TABLE_DOCS_DIR = REPO / "examples" / "table_docs"


@pytest.fixture(scope="module")
def sample_table_doc_raw():
    """The canonical fixture from §2.3 of the spec."""
    p = REPO / "examples" / "phase_6_5" / "sample_table_doc.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def migrated_dev_docs():
    """All migrated DEV table docs (top-5)."""
    docs: list[dict] = []
    for f in sorted((TABLE_DOCS_DIR / "EDW").glob("*.yaml")):
        docs.append(yaml.safe_load(f.read_text(encoding="utf-8")))
    return docs


# ── Spec fixture loads ────────────────────────────────────────────────────

class TestSpecFixtureLoads:
    def test_sample_table_doc_parses(self, sample_table_doc_raw):
        doc = load_table_doc_from_dict(sample_table_doc_raw)
        assert doc.table == "TRD_BRANCH_POSITION"
        assert doc.schema_name == "ODS_TREASURY"
        assert doc.partition_column == "AS_OF_DATE"
        assert doc.estimated_daily_rows == 12000
        # Spec column count: AS_OF_DATE, BRANCH_ID, CCY, MATURITY_BUCKET,
        # PRODUCT_GROUP, NET_POSITION, GROSS_INFLOW, GROSS_OUTFLOW, CREATED_AT.
        assert len(doc.columns) == 9
        # CREATED_AT is invisible (internal audit column).
        assert doc.columns["CREATED_AT"].visible_in_ui is False

    def test_filterable_columns_view(self, sample_table_doc_raw):
        doc = load_table_doc_from_dict(sample_table_doc_raw)
        filterable = doc.filterable_columns()
        assert set(filterable) == {"AS_OF_DATE", "BRANCH_ID", "CCY",
                                     "MATURITY_BUCKET", "PRODUCT_GROUP"}

    def test_time_axis_accessor(self, sample_table_doc_raw):
        doc = load_table_doc_from_dict(sample_table_doc_raw)
        name, col = doc.time_axis_column()
        assert name == "AS_OF_DATE"
        assert col.filter_role == "time_axis"

    def test_round_trip_yaml(self, sample_table_doc_raw):
        doc = load_table_doc_from_dict(sample_table_doc_raw)
        shape = doc.to_yaml_shape()
        # alias 'schema' (not 'schema_name') in the on-disk form
        assert "schema" in shape
        assert "schema_name" not in shape
        reparsed = load_table_doc_from_dict(shape)
        assert reparsed.table == doc.table
        assert reparsed.partition_column == doc.partition_column


# ── DEV migrations all load ───────────────────────────────────────────────

class TestDevMigrations:
    def test_all_dev_docs_load(self, migrated_dev_docs):
        assert len(migrated_dev_docs) == 5
        for raw in migrated_dev_docs:
            doc = load_table_doc_from_dict(raw)
            assert doc.schema_name == "EDW"

    def test_at_least_one_filterable_column_each(self, migrated_dev_docs):
        for raw in migrated_dev_docs:
            doc = load_table_doc_from_dict(raw)
            assert doc.filterable_columns(), (
                f"{doc.table} has no filterable columns — migration incomplete"
            )

    def test_every_filterable_dimension_has_suggested_tag(self, migrated_dev_docs):
        for raw in migrated_dev_docs:
            doc = load_table_doc_from_dict(raw)
            for cname, col in doc.columns.items():
                if col.filterable and col.filter_role == "dimension":
                    assert col.suggested_variable, (
                        f"{doc.table}.{cname}: missing suggested_variable"
                    )
                    assert col.suggested_semantic_tag, (
                        f"{doc.table}.{cname}: missing suggested_semantic_tag"
                    )


# ── Validation: semantic_tag allow-list ───────────────────────────────────

class TestSemanticTagEnforcement:
    def test_unknown_tag_rejected(self):
        with pytest.raises(ValidationError) as exc:
            ColumnDoc(
                type="VARCHAR2(20)",
                filterable=True,
                filter_role="dimension",
                suggested_variable="x",
                suggested_semantic_tag="not_a_real_tag",
            )
        assert "not_a_real_tag" in str(exc.value)

    def test_other_tag_allowed(self):
        col = ColumnDoc(
            type="VARCHAR2(20)",
            filterable=True,
            filter_role="dimension",
            suggested_variable="misc_thing",
            suggested_semantic_tag="other",
        )
        assert col.suggested_semantic_tag == "other"

    def test_filter_role_requires_filterable(self):
        with pytest.raises(ValidationError):
            ColumnDoc(type="DATE", filter_role="time_axis")  # filterable=False default

    def test_suggested_tag_without_var_rejected(self):
        with pytest.raises(ValidationError):
            ColumnDoc(
                type="DATE",
                filterable=True,
                filter_role="time_axis",
                suggested_semantic_tag="as_of_time",
                # suggested_variable missing
            )


# ── Validation: distinct_values_sample timestamps ─────────────────────────

class TestDistinctValuesPairing:
    def test_sample_requires_timestamp(self):
        with pytest.raises(ValidationError) as exc:
            ColumnDoc(
                type="VARCHAR2(3)",
                filterable=True,
                filter_role="dimension",
                suggested_variable="ccys",
                suggested_semantic_tag="currency",
                distinct_values_sample=["TRY", "USD"],
                # distinct_values_sampled_at missing
            )
        assert "distinct_values_sampled_at" in str(exc.value)

    def test_timestamp_alone_is_fine(self):
        from datetime import datetime, timezone
        col = ColumnDoc(
            type="VARCHAR2(3)",
            filterable=True,
            filter_role="dimension",
            suggested_variable="ccys",
            suggested_semantic_tag="currency",
            distinct_values_sampled_at=datetime.now(timezone.utc),
        )
        assert col.distinct_values_sample is None


# ── Validation: partition column must be declared ─────────────────────────

class TestPartitionColumn:
    def test_partition_must_be_in_columns(self):
        with pytest.raises(ValidationError):
            TableDoc(
                table="TEST",
                schema="EDW",
                partition_column="NOPE",
                columns={"AS_OF_DATE": ColumnDoc(type="DATE")},
            )

    def test_partition_column_resolves(self):
        doc = TableDoc(
            table="TEST",
            schema="EDW",
            partition_column="AS_OF_DATE",
            columns={"AS_OF_DATE": ColumnDoc(type="DATE")},
        )
        assert doc.partition_column == "AS_OF_DATE"


# ── Validation: identifier shape ──────────────────────────────────────────

class TestIdentifierShape:
    def test_lowercase_table_rejected(self):
        with pytest.raises(ValidationError):
            TableDoc(table="lowercase_bad", schema="EDW", columns={})

    def test_special_chars_in_table_rejected(self):
        with pytest.raises(ValidationError):
            TableDoc(table="HAS-DASH", schema="EDW", columns={})

    def test_oracle_dollar_sign_allowed(self):
        # Oracle identifiers may contain $ and # (e.g. SYS_$STATS).
        doc = TableDoc(table="SYS$STATS", schema="SYS", columns={})
        assert doc.table == "SYS$STATS"


# ── Lookup refs ───────────────────────────────────────────────────────────

class TestLookupRef:
    def test_lookup_loads(self, sample_table_doc_raw):
        doc = load_table_doc_from_dict(sample_table_doc_raw)
        lookup = doc.columns["BRANCH_ID"].lookup
        assert isinstance(lookup, LookupRef)
        assert lookup.table == "DIM_BRANCH"
        assert lookup.key == "BRANCH_ID"
        assert lookup.display == "BRANCH_NAME"

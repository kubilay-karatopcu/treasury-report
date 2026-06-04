"""Tests for presentations.table_docs.store — LocalTableDocStore + CachedTableDocStore."""
from __future__ import annotations

import pytest

from presentations.table_docs.schema import ColumnDoc, TableDoc
from presentations.table_docs.store import (
    CachedTableDocStore,
    LocalTableDocStore,
    TableDocNotFoundError,
    TableDocStoreError,
)


@pytest.fixture
def store(tmp_path):
    return LocalTableDocStore(tmp_path)


def _example_doc(table="TEST_TABLE", schema="EDW") -> TableDoc:
    return TableDoc(
        table=table,
        schema=schema,
        description="Test table",
        partition_column="AS_OF_DATE",
        columns={
            "AS_OF_DATE": ColumnDoc(
                type="DATE",
                filterable=True,
                filter_role="time_axis",
                suggested_variable="as_of_date",
                suggested_semantic_tag="as_of_time",
            ),
            "CCY": ColumnDoc(
                type="VARCHAR2(3)",
                filterable=True,
                filter_role="dimension",
                suggested_variable="ccys",
                suggested_semantic_tag="currency",
            ),
        },
    )


# ── LocalTableDocStore ────────────────────────────────────────────────────

class TestLocalTableDocStore:
    def test_save_and_load(self, store):
        doc = _example_doc()
        store.save(doc)
        loaded = store.load("EDW", "TEST_TABLE")
        assert loaded.table == doc.table
        assert loaded.schema_name == doc.schema_name
        assert loaded.partition_column == "AS_OF_DATE"
        assert set(loaded.columns) == {"AS_OF_DATE", "CCY"}

    def test_load_missing_raises(self, store):
        with pytest.raises(TableDocNotFoundError):
            store.load("EDW", "NOPE")

    def test_exists(self, store):
        assert not store.exists("EDW", "TEST_TABLE")
        store.save(_example_doc())
        assert store.exists("EDW", "TEST_TABLE")

    def test_list_tables(self, store):
        store.save(_example_doc("A_TABLE"))
        store.save(_example_doc("B_TABLE"))
        store.save(_example_doc("OTHER_TABLE", schema="DM"))
        listed = store.list_tables()
        assert ("DM", "OTHER_TABLE") in listed
        assert ("EDW", "A_TABLE") in listed
        assert ("EDW", "B_TABLE") in listed

    def test_list_tables_filter_by_schema(self, store):
        store.save(_example_doc("A_TABLE"))
        store.save(_example_doc("OTHER_TABLE", schema="DM"))
        assert store.list_tables(schema="DM") == [("DM", "OTHER_TABLE")]

    def test_list_all_docs(self, store):
        store.save(_example_doc("A_TABLE"))
        store.save(_example_doc("B_TABLE"))
        docs = store.list_all_docs()
        assert len(docs) == 2
        assert {d.table for d in docs} == {"A_TABLE", "B_TABLE"}

    def test_invalid_identifier_rejected(self, store):
        with pytest.raises(TableDocStoreError):
            store.load("lowercase_bad", "ALSO_BAD")


# ── CachedTableDocStore ───────────────────────────────────────────────────

class TestCachedTableDocStore:
    def test_cache_hit_after_first_load(self, store):
        store.save(_example_doc())
        cached = CachedTableDocStore(store)
        first = cached.load("EDW", "TEST_TABLE")
        # Mutate underlying disk between reads — cache hit should not see it.
        import shutil
        shutil.rmtree(store.base_dir / "EDW")
        second = cached.load("EDW", "TEST_TABLE")
        assert first is second  # same object reference

    def test_clear_invalidates_cache(self, store):
        store.save(_example_doc())
        cached = CachedTableDocStore(store)
        cached.load("EDW", "TEST_TABLE")
        # Save updates the cache too.
        new_doc = _example_doc()
        new_doc = TableDoc(
            **{**new_doc.model_dump(by_alias=True), "description": "updated"}
        )
        cached.save(new_doc)
        again = cached.load("EDW", "TEST_TABLE")
        assert again.description == "updated"

    def test_list_all_docs_refreshes_after_expiry(self, store):
        # Regression: a permanent list cache hid tables documented by another
        # gunicorn worker until pod restart ("kolon tanımı yok" in Sunum). The
        # list must re-read the inner store once its TTL window lapses.
        store.save(_example_doc("A_TABLE"))
        cached = CachedTableDocStore(store)
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE"}
        # Another worker writes straight to the shared (inner) store.
        store.save(_example_doc("B_TABLE"))
        # Still within the TTL window → serves the cached snapshot.
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE"}
        # Force the list TTL to lapse → re-reads inner, now sees B_TABLE.
        cached._listed_until = cached._now() - 1.0
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE", "B_TABLE"}

    def test_ttl_zero_disables_cache(self, store):
        store.save(_example_doc("A_TABLE"))
        cached = CachedTableDocStore(store, ttl_seconds=0)
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE"}
        store.save(_example_doc("B_TABLE"))
        # No caching → every list re-reads the inner store immediately.
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE", "B_TABLE"}

    def test_clear_forces_relist(self, store):
        store.save(_example_doc("A_TABLE"))
        cached = CachedTableDocStore(store)
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE"}
        store.save(_example_doc("B_TABLE"))
        cached.clear()  # what tablo_save triggers on the writing worker
        assert {d.table for d in cached.list_all_docs()} == {"A_TABLE", "B_TABLE"}

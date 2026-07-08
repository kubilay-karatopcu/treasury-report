"""Phase 9.a — CatalogLoader: corporate + user uploads + cache behaviour."""
from __future__ import annotations

import time

import pytest

from presentations.catalog.loader import CatalogLoader, _user_schema_marker


# ── Corporate-only ────────────────────────────────────────────────────────


def test_load_corporate_only(loader):
    entries = loader.load(user_sicil=None)
    names = {e.name for e in entries}
    # All fixture tables present.
    assert {"DEPOSITS_DAILY", "DEPOSITS_BY_BRANCH", "DIM_BRANCH",
            "NII_MONTHLY", "NII_QUARTERLY", "COMPETITOR_RATES"} <= names
    assert all(e.source == "corporate" for e in entries)


def test_corporate_entry_shape(loader):
    entries = loader.load()
    deposits = next(e for e in entries if e.name == "DEPOSITS_DAILY")
    assert deposits.schema_name == "EDW"
    assert deposits.department == "treasury"
    assert deposits.partition_column == "DATE"
    assert deposits.row_count_basis == "daily"  # partitioned → daily
    assert "branch" in deposits.concepts_bound
    assert "as_of_time" in deposits.concepts_bound
    assert deposits.doc_url.endswith("/EDW/DEPOSITS_DAILY")


def test_non_partitioned_table_uses_total_basis(loader):
    """DEPOSITS_BY_BRANCH has no partition_column → row count is total."""
    entries = loader.load()
    by_branch = next(e for e in entries if e.name == "DEPOSITS_BY_BRANCH")
    assert by_branch.partition_column is None
    assert by_branch.row_count_basis == "total"


# ── Detail load (with_details=True) ───────────────────────────────────────


def test_get_returns_columns_and_lookups(loader):
    entry = loader.get("EDW", "DEPOSITS_DAILY")
    assert entry is not None
    assert entry.columns is not None
    col_names = {c.name for c in entry.columns}
    assert {"BRANCH_CODE", "DATE", "BALANCE_TRY"} <= col_names
    # The fixture's BRANCH_CODE declares a lookup to DIM_BRANCH.
    assert entry.lookups is not None
    assert any(lk.to_table == "DIM_BRANCH" for lk in entry.lookups)


def test_get_missing_returns_none(loader):
    assert loader.get("EDW", "DOES_NOT_EXIST") is None


# ── Concept derivation ────────────────────────────────────────────────────


def test_concepts_bound_dedupes_per_table(loader):
    """Multiple columns sharing the same suggested_semantic_tag must
    collapse to a single concept entry (no duplicates)."""
    entries = loader.load()
    for entry in entries:
        assert len(entry.concepts_bound) == len(set(entry.concepts_bound))


def test_unbound_empty_without_concept_universe(loader):
    """With no concept registry wired, every entry's unbound is empty."""
    entries = loader.load()
    for entry in entries:
        assert entry.concepts_unbound == []


def test_unbound_derived_from_universe(fixture_store, fake_dc):
    loader = CatalogLoader(
        table_doc_store=fixture_store,
        data_client=fake_dc,
        all_concepts=["branch", "as_of_time", "currency", "counterparty"],
    )
    deposits = next(e for e in loader.load() if e.name == "DEPOSITS_DAILY")
    # DEPOSITS_DAILY binds branch + as_of_time + segment + product_group;
    # currency and counterparty are in the universe but unbound on this table.
    assert "currency" in deposits.concepts_unbound
    assert "counterparty" in deposits.concepts_unbound
    assert "branch" not in deposits.concepts_unbound


class _StubRegistry:
    """Minimal CONCEPT_REGISTRY stand-in: .all_concepts() → objects with .id."""

    def __init__(self, ids):
        self._ids = list(ids)

    def all_concepts(self):
        from types import SimpleNamespace
        return [SimpleNamespace(id=i) for i in self._ids]


def test_empty_registry_hides_all_concepts(fixture_store, fake_dc):
    """Registry wired but EMPTY → the registry is the single source of
    truth, so suggested_semantic_tag hints must NOT surface as concepts
    (the 'Keşif shows ghost concepts after catalog wipe' bug)."""
    loader = CatalogLoader(
        table_doc_store=fixture_store,
        data_client=fake_dc,
        concept_registry=_StubRegistry([]),
    )
    for entry in loader.load():
        assert entry.concepts_bound == []
        assert entry.concepts_unbound == []


def test_bound_filtered_to_registry_universe(fixture_store, fake_dc):
    """Tags whose concept id isn't in the registry are dropped; the rest
    survive. Registry queried live — no restart needed after edits."""
    registry = _StubRegistry(["branch", "as_of_time"])
    loader = CatalogLoader(
        table_doc_store=fixture_store,
        data_client=fake_dc,
        concept_registry=registry,
    )
    deposits = next(e for e in loader.load() if e.name == "DEPOSITS_DAILY")
    assert "branch" in deposits.concepts_bound
    assert "as_of_time" in deposits.concepts_bound
    # segment / product_group are tagged on columns but absent from the
    # registry → filtered out.
    assert "segment" not in deposits.concepts_bound
    assert "product_group" not in deposits.concepts_bound


def test_registry_growth_reflected_without_restart(fixture_store, fake_dc):
    registry = _StubRegistry([])
    loader = CatalogLoader(
        table_doc_store=fixture_store,
        data_client=fake_dc,
        concept_registry=registry,
        ttl_seconds=0,  # bypass loader TTL so the live query is visible
    )
    assert all(e.concepts_bound == [] for e in loader.load())
    registry._ids = ["branch"]
    loader.invalidate()
    deposits = next(e for e in loader.load() if e.name == "DEPOSITS_DAILY")
    assert deposits.concepts_bound == ["branch"]


# ── User uploads ──────────────────────────────────────────────────────────


def _write_upload_fixture(fake_dc, sicil="A16438", upload_id="u_test1"):
    """Write a doc.yaml + meta.yaml pair so loader picks it up."""
    import yaml as _yaml
    doc = {
        "table": upload_id,
        "schema": _user_schema_marker(sicil),
        "description": "User upload — test fixture",
        "estimated_total_rows": 500,
        "source": "user_upload",
        "columns": {
            "branch_id": {
                "type": "VARCHAR", "description": "Şube",
                "filterable": True, "filter_role": "dimension",
                "concept": "branch",
            },
            "metric": {
                "type": "NUMBER", "filterable": False,
                "aggregatable": True,
            },
        },
    }
    meta = {
        "upload": {
            "id": upload_id, "user": sicil,
            "original_filename": "test.xlsx",
            "uploaded_at": "2026-05-01T10:00:00Z",
            "row_count": 500,
            "inference_status": "confirmed",
        }
    }
    fake_dc._upload_bytes(
        f"uploads/{sicil}/{upload_id}/doc.yaml",
        _yaml.safe_dump(doc, allow_unicode=True).encode("utf-8"),
    )
    fake_dc._upload_bytes(
        f"uploads/{sicil}/{upload_id}/meta.yaml",
        _yaml.safe_dump(meta, allow_unicode=True).encode("utf-8"),
    )


def test_user_uploads_appear_with_sicil(loader, fake_dc):
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    loader.invalidate()
    entries = loader.load(user_sicil="A16438")
    uploads = [e for e in entries if e.source == "user_upload"]
    assert len(uploads) == 1
    assert uploads[0].name == "u_alpha"
    assert uploads[0].schema_name == "__user_A16438__"
    assert uploads[0].department is None
    assert "branch" in uploads[0].concepts_bound
    assert uploads[0].original_filename == "test.xlsx"
    assert uploads[0].inference_status == "confirmed"


def test_user_uploads_invisible_to_other_users(loader, fake_dc):
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    loader.invalidate()
    entries = loader.load(user_sicil="B99999")
    assert all(e.source == "corporate" for e in entries)


def test_user_uploads_filtered_when_no_sicil(loader, fake_dc):
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    loader.invalidate()
    entries = loader.load(user_sicil=None)
    assert all(e.source == "corporate" for e in entries)


def test_soft_deleted_uploads_dropped(loader, fake_dc):
    import yaml as _yaml
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    # Override meta to mark deleted.
    meta = {"deleted": True, "upload": {"id": "u_alpha"}}
    fake_dc._upload_bytes(
        "uploads/A16438/u_alpha/meta.yaml",
        _yaml.safe_dump(meta).encode("utf-8"),
    )
    loader.invalidate()
    entries = loader.load(user_sicil="A16438")
    assert not any(e.source == "user_upload" for e in entries)


# ── Per-user upload detail ────────────────────────────────────────────────


def test_get_user_upload_detail(loader, fake_dc):
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    loader.invalidate()
    entry = loader.get("__user_A16438__", "u_alpha", user_sicil="A16438")
    assert entry is not None
    assert entry.source == "user_upload"
    assert entry.columns is not None
    assert {c.name for c in entry.columns} == {"branch_id", "metric"}


def test_get_user_upload_blocked_for_other_user(loader, fake_dc):
    _write_upload_fixture(fake_dc, "A16438", "u_alpha")
    loader.invalidate()
    # Another user can't access A16438's upload — loader returns None.
    assert loader.get("__user_A16438__", "u_alpha", user_sicil="B99999") is None


# ── Cache TTL ─────────────────────────────────────────────────────────────


def test_cache_serves_repeat_calls(fixture_store, fake_dc):
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc, ttl_seconds=60)
    e1 = loader.load(user_sicil="A16438")
    # Drop the underlying fixture file; cache should still serve the prior result.
    schema_dir = fixture_store.base_dir / "EDW"
    target = schema_dir / "NII_MONTHLY.yaml"
    target.unlink()
    # Underlying CachedTableDocStore inside the LocalTableDocStore isn't used —
    # we use raw LocalTableDocStore which re-reads each call. So our own cache
    # is the one keeping the result stable.
    e2 = loader.load(user_sicil="A16438")
    assert [x.name for x in e1] == [x.name for x in e2]


def test_cache_invalidates_on_refresh(fixture_store, fake_dc):
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc, ttl_seconds=60)
    loader.load(user_sicil="A16438")
    (fixture_store.base_dir / "EDW" / "NII_MONTHLY.yaml").unlink()
    fresh = loader.load(user_sicil="A16438", refresh=True)
    assert not any(e.name == "NII_MONTHLY" for e in fresh)


def test_cache_expires_after_ttl(fixture_store, fake_dc):
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc, ttl_seconds=0.05)
    loader.load(user_sicil="A16438")
    (fixture_store.base_dir / "EDW" / "NII_MONTHLY.yaml").unlink()
    time.sleep(0.1)
    fresh = loader.load(user_sicil="A16438")
    assert not any(e.name == "NII_MONTHLY" for e in fresh)


# ── Resilience ────────────────────────────────────────────────────────────


def test_empty_store_returns_empty_list(empty_loader):
    assert empty_loader.load() == []


def test_malformed_yaml_is_skipped(fixture_store, fake_dc, tmp_path):
    """A broken YAML file in the store shouldn't crash the loader."""
    broken = fixture_store.base_dir / "EDW" / "BROKEN.yaml"
    broken.write_text("this: is: not: valid: yaml:::", encoding="utf-8")
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc)
    entries = loader.load()
    names = {e.name for e in entries}
    assert "BROKEN" not in names
    # Other tables still load.
    assert "DEPOSITS_DAILY" in names


# ── Binding kataloğu → concepts_bound birleşimi (ofis senaryosu) ──────────


def test_binding_catalog_concepts_merge_into_bound(fixture_store, fake_dc):
    """Tablo dokümanında suggested_semantic_tag OLMASA bile Konseptler
    UI'ından yapılmış human_verified binding keşifte concept olarak görünür."""
    from presentations.concepts.bindings import BindingCatalog

    cat = BindingCatalog.from_dicts([{
        "table": "DEPOSITS_BY_BRANCH", "schema": "EDW",
        "concept_bindings": [{
            "concept": "region", "column": "REGION_CODE",
            "transform": {"kind": "identity"},
            "confidence": "human_verified",
        }],
    }])
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc,
                           binding_catalog=cat)
    entry = next(e for e in loader.load() if e.name == "DEPOSITS_BY_BRANCH")
    assert "region" in entry.concepts_bound


def test_binding_catalog_unverified_bindings_ignored(fixture_store, fake_dc):
    from presentations.concepts.bindings import BindingCatalog

    cat = BindingCatalog.from_dicts([{
        "table": "DEPOSITS_BY_BRANCH", "schema": "EDW",
        "concept_bindings": [{
            "concept": "region", "column": "REGION_CODE",
            "transform": {"kind": "identity"},
            "confidence": "llm_proposed",   # gated — compiler'a da keşfe de girmez
        }],
    }])
    loader = CatalogLoader(table_doc_store=fixture_store, data_client=fake_dc,
                           binding_catalog=cat)
    entry = next(e for e in loader.load() if e.name == "DEPOSITS_BY_BRANCH")
    assert "region" not in entry.concepts_bound

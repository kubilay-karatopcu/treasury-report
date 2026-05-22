"""Scope store tests — LocalScopeStore (filesystem) and S3ScopeStore (against
an in-memory fake DataClient). Covers write, read, version bump, immutability,
load_latest, list_versions."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from presentations.scope.schema import ScopeContract
from presentations.scope.store import (
    LocalScopeStore,
    S3ScopeStore,
    ScopeNotFoundError,
)


# ── In-memory fake DataClient (mirrors the helper surface store.py uses) ─────

class FakeDC:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def _upload_bytes(self, key, data, content_type=None):
        self.objects[key] = data

    def read_text(self, key, encoding="utf-8"):
        if key not in self.objects:
            raise FileNotFoundError(f"NoSuchKey: {key}")
        return self.objects[key].decode(encoding)

    def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_file(self, key):
        self.objects.pop(key, None)


def _scope(version_hint=1, pid="p_store", user="A16438") -> ScopeContract:
    # version is set by the store; the hint here is irrelevant on save.
    return ScopeContract.model_validate({
        "presentation_id": pid,
        "version": version_hint,
        "created_by": user,
        "created_at": datetime(2026, 6, 15, tzinfo=timezone.utc).isoformat(),
        "basket": [{
            "table_ref": {"schema": "ODS_TREASURY", "name": "TRD_BRANCH_POSITION"},
            "alias": "positions",
            "projection": {"columns": ["AS_OF_DATE"], "include_all": False},
            "routing": {"decision": "cached", "decided_by": "system",
                        "estimated_bytes": 100, "threshold_bytes": 500_000_000},
        }],
    })


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalScopeStore(tmp_path / "scopes")
    return S3ScopeStore(dc=FakeDC())


class TestScopeStore:
    def test_save_returns_version_1_first(self, store):
        v = store.save(_scope())
        assert v == 1

    def test_save_bumps_version(self, store):
        assert store.save(_scope()) == 1
        assert store.save(_scope()) == 2
        assert store.save(_scope()) == 3
        assert store.list_versions("p_store") == [1, 2, 3]

    def test_save_stamps_version_on_contract(self, store):
        sc = _scope(version_hint=99)
        v = store.save(sc)
        assert v == 1
        assert sc.version == 1  # store overrides the hint.

    def test_load_roundtrip(self, store):
        store.save(_scope())
        loaded = store.load("p_store", 1)
        assert loaded.presentation_id == "p_store"
        assert loaded.version == 1
        assert loaded.basket[0].alias == "positions"

    def test_load_latest(self, store):
        store.save(_scope()); store.save(_scope()); store.save(_scope())
        latest = store.load_latest("p_store")
        assert latest is not None
        assert latest.version == 3

    def test_load_latest_none_when_absent(self, store):
        assert store.load_latest("p_missing") is None

    def test_list_versions_empty_for_unknown(self, store):
        assert store.list_versions("p_missing") == []

    def test_load_missing_version_raises(self, store):
        store.save(_scope())
        with pytest.raises(ScopeNotFoundError):
            store.load("p_store", 7)

    def test_versions_are_immutable(self, store):
        """Re-saving never clobbers an earlier version — it always bumps."""
        store.save(_scope())
        store.save(_scope())
        # v1 still loads and is independent of v2.
        v1 = store.load("p_store", 1)
        assert v1.version == 1
        assert store.list_versions("p_store") == [1, 2]

    def test_lineage_parent_version_stamped(self, store):
        # First version has no parent; bumps parent to the prior latest. This
        # also keeps the parent_version < version invariant so reload succeeds
        # even when the incoming contract carried a stale version/parent pair.
        sc1 = _scope()
        sc1.version, sc1.parent_version = 4, 3  # stale lineage from a copy.
        assert store.save(sc1) == 1
        assert sc1.parent_version is None
        loaded1 = store.load("p_store", 1)
        assert loaded1.version == 1 and loaded1.parent_version is None

        assert store.save(_scope()) == 2
        loaded2 = store.load("p_store", 2)
        assert loaded2.version == 2 and loaded2.parent_version == 1

    def test_separate_presentations_dont_collide(self, store):
        store.save(_scope(pid="p_a"))
        store.save(_scope(pid="p_b"))
        store.save(_scope(pid="p_a"))
        assert store.list_versions("p_a") == [1, 2]
        assert store.list_versions("p_b") == [1]


def test_s3_key_layout_matches_spec():
    dc = FakeDC()
    store = S3ScopeStore(dc=dc)
    store.save(_scope(pid="p_abc123", user="A16438"))
    keys = list(dc.objects)
    assert keys == ["prisma-treasury/presentations/A16438/p_abc123/scope_v1.yaml"]

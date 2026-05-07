import pytest

from presentations.store import LocalSnapshotStore, _gen_snapshot_id


# ── ID generation ─────────────────────────────────────────────────────────────

class TestSnapshotIdGeneration:
    def test_format(self):
        sid = _gen_snapshot_id()
        assert sid.startswith("s_")
        assert len(sid) >= 12

    def test_uniqueness(self):
        ids = {_gen_snapshot_id() for _ in range(1000)}
        assert len(ids) == 1000


# ── LocalSnapshotStore ────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    return LocalSnapshotStore(base_dir=tmp_path / "snapshots")


_MANIFEST = {
    "id": "p_test",
    "version": 7,
    "owner_id": "A16438",
    "meta": {"title": "Q4 Test", "eyebrow": "Test", "date": "2025", "author_label": "kubilay"},
    "basket": [],
    "blocks": [
        {"id": "b1", "type": "kpi", "title": "K", "locked": False,
         "config": {"value": 100.0, "unit": "TRY", "delta": 1.0, "delta_label": "L", "period": "P"}},
    ],
}


class TestLocalSnapshotStore:
    def test_save_returns_meta_with_id(self, store):
        meta = store.save(_MANIFEST, owner_id="A16438")
        assert meta["snapshot_id"].startswith("s_")
        assert meta["owner_id"] == "A16438"
        assert meta["presentation_id"] == "p_test"
        assert meta["manifest_version"] == 7
        assert meta["title"] == "Q4 Test"
        assert "created_at" in meta

    def test_save_writes_files(self, store):
        meta = store.save(_MANIFEST, owner_id="A16438")
        snap_dir = store.base_dir / meta["snapshot_id"]
        assert (snap_dir / "manifest.json").exists()
        assert (snap_dir / "meta.json").exists()

    def test_load_roundtrip(self, store):
        meta = store.save(_MANIFEST, owner_id="A16438")
        loaded = store.load(meta["snapshot_id"])
        assert loaded is not None
        # Frozen manifest carries snapshot metadata
        assert loaded["manifest"]["snapshot_id"] == meta["snapshot_id"]
        assert loaded["manifest"]["id"] == "p_test"
        assert loaded["manifest"]["blocks"][0]["config"]["value"] == 100.0
        assert loaded["meta"]["snapshot_id"] == meta["snapshot_id"]

    def test_load_returns_none_for_unknown(self, store):
        assert store.load("s_nonexistent") is None

    def test_save_does_not_mutate_input(self, store):
        before = dict(_MANIFEST)
        before_blocks_count = len(before["blocks"])
        store.save(_MANIFEST, owner_id="A16438")
        # Original manifest unchanged (no snapshot_id added)
        assert "snapshot_id" not in _MANIFEST
        assert len(_MANIFEST["blocks"]) == before_blocks_count

    def test_list_for_owner_filters(self, store):
        store.save(_MANIFEST, owner_id="A16438")
        store.save({**_MANIFEST, "id": "p_other"}, owner_id="A16438")
        store.save({**_MANIFEST, "id": "p_third"}, owner_id="B99999")

        mine = store.list_for_owner("A16438")
        assert len(mine) == 2
        assert all(m["owner_id"] == "A16438" for m in mine)

        theirs = store.list_for_owner("B99999")
        assert len(theirs) == 1

    def test_list_for_owner_empty(self, store):
        assert store.list_for_owner("nobody") == []

    def test_multiple_snapshots_get_unique_ids(self, store):
        ids = [store.save(_MANIFEST, owner_id="A16438")["snapshot_id"] for _ in range(5)]
        assert len(set(ids)) == 5

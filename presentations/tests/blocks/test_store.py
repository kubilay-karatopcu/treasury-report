"""Tests for presentations.blocks.store — LocalBlockStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from botocore.exceptions import ClientError

from presentations.blocks.schema import load_block_from_dict
from presentations.blocks.store import (
    BlockAlreadyExistsError,
    BlockNotFoundError,
    LocalBlockStore,
    S3BlockStore,
    block_key,
)


@pytest.fixture
def store(tmp_path: Path) -> LocalBlockStore:
    return LocalBlockStore(tmp_path)


def test_save_and_load_roundtrip(store: LocalBlockStore, sample_block_dict):
    block = load_block_from_dict(sample_block_dict)
    saved = store.save(block)
    loaded = store.load(saved.team, saved.id, saved.version)
    assert loaded.id == block.id
    assert loaded.version == block.version
    assert [v.name for v in loaded.variables] == [v.name for v in block.variables]


def test_save_same_version_raises(store: LocalBlockStore, sample_block_dict):
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    with pytest.raises(BlockAlreadyExistsError):
        store.save(block)


def test_save_new_version_bumps(store: LocalBlockStore, sample_block_dict):
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    bumped = store.save_new_version(block)
    assert bumped.version == 2
    assert store.list_versions(block.team, block.id) == [1, 2]


def test_load_missing_raises(store: LocalBlockStore):
    with pytest.raises(BlockNotFoundError):
        store.load("treasury", "missing_block", 1)


def test_load_latest(store: LocalBlockStore, sample_block_dict):
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    store.save_new_version(block)
    store.save_new_version(block)
    latest = store.load_latest(block.team, block.id)
    assert latest.version == 3


def test_list_blocks_filters_by_team(
    store: LocalBlockStore, sample_block_dict, sample_block_2_dict,
):
    store.save(load_block_from_dict(sample_block_dict))
    store.save(load_block_from_dict(sample_block_2_dict))

    summaries = store.list_blocks(team="treasury")
    assert len(summaries) == 1
    assert summaries[0].id == "fx_exposure_line"


def test_list_blocks_filters_by_tag(store: LocalBlockStore, sample_block_dict):
    store.save(load_block_from_dict(sample_block_dict))
    assert len(store.list_blocks(tag="weekly")) == 1
    assert len(store.list_blocks(tag="nonexistent")) == 0


def test_list_blocks_filters_by_viz_type(store: LocalBlockStore, sample_block_dict):
    store.save(load_block_from_dict(sample_block_dict))
    assert len(store.list_blocks(viz_type="bar_chart")) == 1
    assert len(store.list_blocks(viz_type="kpi")) == 0


def test_list_blocks_search_matches_title(store: LocalBlockStore, sample_block_dict):
    store.save(load_block_from_dict(sample_block_dict))
    assert len(store.list_blocks(search="şube")) == 1
    assert len(store.list_blocks(search="zzz")) == 0


def test_soft_delete_marks_block_deprecated_and_hides_by_default(
    store: LocalBlockStore, sample_block_dict,
):
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    assert len(store.list_blocks()) == 1

    dep = store.soft_delete(block.team, block.id)
    assert dep.deprecated is True
    assert len(store.list_blocks()) == 0
    assert len(store.list_blocks(include_deprecated=True)) == 1


def test_list_versions_empty_for_unknown_block(store: LocalBlockStore):
    assert store.list_versions("treasury", "nope") == []


def test_yaml_on_disk_uses_unicode(store: LocalBlockStore, sample_block_dict, tmp_path):
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    path = tmp_path / block.team / block.id / "v0001.yaml"
    content = path.read_text(encoding="utf-8")
    # Turkish characters must round-trip without ascii escaping.
    assert "Şube Net Pozisyon" in content


# ── S3BlockStore conditional-write / compare-and-swap coverage ──────────────

class _FakeS3DC:
    """S3 surface used by S3BlockStore, with conditional-PUT semantics.

    ``if_none_match=True`` raises 412 PreconditionFailed when the key exists
    (or 501 NotImplemented when ``supports_conditional=False``). ``hidden_keys``
    collide on a conditional PUT but are invisible to ``list_prefix`` until that
    collision — modelling a concurrent writer's object that this thread's
    version listing hasn't seen yet (the exact race save_new_version survives).
    """

    def __init__(self, *, supports_conditional: bool = True, hidden_keys=()):
        self.objects: dict[str, bytes] = {}
        self.supports_conditional = supports_conditional
        self._hidden: dict[str, bytes] = dict.fromkeys(hidden_keys, b"competing")

    def _upload_bytes(self, key, data, content_type=None, *, if_none_match=False):
        if if_none_match:
            if not self.supports_conditional:
                raise ClientError(
                    {"Error": {"Code": "NotImplemented"},
                     "ResponseMetadata": {"HTTPStatusCode": 501}}, "PutObject")
            if key in self.objects or key in self._hidden:
                # Reveal the competing object so the next list_prefix sees it.
                if key in self._hidden:
                    self.objects[key] = self._hidden.pop(key)
                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed"},
                     "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.objects[key] = bytes(data)

    def read_bytes(self, key):
        return self.objects.get(key, b"")

    def list_prefix(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_file(self, key):
        self.objects.pop(key, None)


def test_s3_conditional_collision_raises(sample_block_dict):
    store = S3BlockStore(_FakeS3DC())
    block = load_block_from_dict(sample_block_dict)
    store.save(block)
    with pytest.raises(BlockAlreadyExistsError):
        store.save(block)


def test_s3_save_new_version_retries_past_concurrent_writer(sample_block_dict):
    block = load_block_from_dict(sample_block_dict)
    v2_key = block_key(block.team, block.id, 2)  # a competing writer holds v2
    store = S3BlockStore(_FakeS3DC(hidden_keys=[v2_key]))
    store.save(block)                       # v1
    bumped = store.save_new_version(block)  # computes v2 → 412 → retry → v3
    assert bumped.version == 3
    assert sorted(store.list_versions(block.team, block.id)) == [1, 2, 3]


def test_s3_falls_back_when_conditional_unsupported(sample_block_dict):
    store = S3BlockStore(_FakeS3DC(supports_conditional=False))
    block = load_block_from_dict(sample_block_dict)
    store.save(block)  # 501 → best-effort check-then-write succeeds
    assert store.load(block.team, block.id, block.version).id == block.id
    with pytest.raises(BlockAlreadyExistsError):
        store.save(block)  # _exists() catches the duplicate on the fallback path

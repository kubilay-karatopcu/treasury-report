"""Süreç Düzenlileştirme W1 — process documentation store testleri."""
from __future__ import annotations

import pytest

from presentations.process.store import (
    LocalProcessStore,
    ProcessStoreError,
    normalize_overlay,
    overlay_key,
)


class TestNormalizeOverlay:
    def test_cleans_and_defaults(self):
        ov = normalize_overlay({
            "process_id": "mevduat.maliyet",
            "documentation": {"purpose": "  x  ", "business_context": "",
                              "bogus": "atılır"},
            "blocks_documentation": {
                "camon_wf": {"purpose": "y"},
                "empty_block": {"purpose": "   "},   # tümü boş → düşer
            },
        })
        assert ov["process_id"] == "mevduat.maliyet"
        assert ov["documentation"]["purpose"] == "x"
        assert ov["documentation"]["business_context"] is None
        assert "bogus" not in ov["documentation"]
        assert "camon_wf" in ov["blocks_documentation"]
        assert "empty_block" not in ov["blocks_documentation"]

    def test_invalid_pid_rejected(self):
        with pytest.raises(ProcessStoreError):
            normalize_overlay({"process_id": "../etc/passwd"})
        with pytest.raises(ProcessStoreError):
            normalize_overlay({"process_id": "Mevduat.Maliyet"})  # büyük harf yok

    def test_overlay_key_shape(self):
        assert overlay_key("mevduat.maliyet", 3) == "processes/mevduat.maliyet/v0003.yaml"


class TestLocalProcessStore:
    def test_save_load_roundtrip(self, tmp_path):
        store = LocalProcessStore(base_dir=tmp_path)
        assert store.load_latest("mevduat.maliyet") is None
        saved = store.save_new_version({
            "process_id": "mevduat.maliyet",
            "updated_by": "A16438",
            "documentation": {"purpose": "ilk amaç"},
        })
        assert saved["version"] == 1
        loaded = store.load_latest("mevduat.maliyet")
        assert loaded["documentation"]["purpose"] == "ilk amaç"
        assert loaded["updated_by"] == "A16438"

    def test_version_bump(self, tmp_path):
        store = LocalProcessStore(base_dir=tmp_path)
        store.save_new_version({"process_id": "mevduat.maliyet",
                                "documentation": {"purpose": "v1"}})
        v2 = store.save_new_version({"process_id": "mevduat.maliyet",
                                     "documentation": {"purpose": "v2"}})
        assert v2["version"] == 2
        assert store.list_versions("mevduat.maliyet") == [1, 2]
        assert store.load_latest("mevduat.maliyet")["documentation"]["purpose"] == "v2"

    def test_block_docs_persisted(self, tmp_path):
        store = LocalProcessStore(base_dir=tmp_path)
        store.save_new_version({
            "process_id": "mevduat.donusler",
            "documentation": {},
            "blocks_documentation": {"wr_rollovers": {"purpose": "tablo amacı"}},
        })
        loaded = store.load_latest("mevduat.donusler")
        assert loaded["blocks_documentation"]["wr_rollovers"]["purpose"] == "tablo amacı"

    def test_isolated_pids(self, tmp_path):
        store = LocalProcessStore(base_dir=tmp_path)
        store.save_new_version({"process_id": "a.b", "documentation": {"purpose": "x"}})
        assert store.load_latest("a.c") is None

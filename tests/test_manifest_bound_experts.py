"""Phase 10B — bound_experts manifest field tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from presentations.manifest import validate_manifest
from presentations.migration import ensure_bound_experts, ensure_nested


FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples" / "phase_10" / "manifest_with_bound_experts.json"
)


# ── Migration ───────────────────────────────────────────────────────────────

class TestEnsureBoundExperts:
    def test_adds_empty_list_when_missing(self):
        out = ensure_bound_experts({"id": "p1", "blocks": []})
        assert out["bound_experts"] == []

    def test_keeps_existing_list_intact(self):
        out = ensure_bound_experts({"id": "p1", "bound_experts": ["liq", "dep"]})
        assert out["bound_experts"] == ["liq", "dep"]

    def test_replaces_none(self):
        out = ensure_bound_experts({"id": "p1", "bound_experts": None})
        assert out["bound_experts"] == []

    def test_idempotent(self):
        first = ensure_bound_experts({"id": "p1", "blocks": []})
        second = ensure_bound_experts(first)
        assert first == second

    def test_does_not_mutate_original(self):
        original = {"id": "p1", "blocks": []}
        ensure_bound_experts(original)
        assert "bound_experts" not in original  # original untouched

    def test_ensure_nested_includes_bound_experts(self):
        # ensure_nested chains both migrations.
        out = ensure_nested({"id": "p1", "blocks": []})
        assert out["bound_experts"] == []


# ── Validator ───────────────────────────────────────────────────────────────

class TestValidatorBoundExperts:
    def _base(self, **extra):
        return {
            "meta": {"title": "t", "eyebrow": "", "date": "", "author_label": ""},
            "blocks": [],
            **extra,
        }

    def test_field_absent_is_ok(self):
        assert validate_manifest(self._base()) == []

    def test_empty_list_is_ok(self):
        assert validate_manifest(self._base(bound_experts=[])) == []

    def test_must_be_list(self):
        errs = validate_manifest(self._base(bound_experts="liq"))
        assert any("must be a list" in e for e in errs)

    def test_each_item_must_be_string(self):
        errs = validate_manifest(self._base(bound_experts=[123]))
        assert any("must be a string" in e for e in errs)

    def test_rejects_unknown_id_when_store_available(self, flask_app):
        # Inside a Flask request/app context, the validator looks up
        # EXPERT_STORE and rejects unknown ids.
        with flask_app.app_context():
            errs = validate_manifest(self._base(bound_experts=["nonexistent_xyz"]))
            assert any("unknown expert id" in e for e in errs)

    def test_accepts_known_id_when_store_available(self, flask_app):
        with flask_app.app_context():
            errs = validate_manifest(self._base(bound_experts=["liq", "dep"]))
            assert errs == []


# ── Fixture file ────────────────────────────────────────────────────────────

class TestFixtureManifest:
    def test_fixture_loads_and_validates(self, flask_app):
        manifest = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assert manifest["bound_experts"] == ["liq", "dep"]
        with flask_app.app_context():
            errs = validate_manifest(manifest)
            assert errs == [], f"fixture manifest failed validation: {errs}"


# ── Snapshot save preserves bound_experts ───────────────────────────────────

class TestSnapshotPreservesBoundExperts:
    def test_snapshot_meta_includes_bound_experts(self, auth_client, flask_app):
        # Warm the demo manifest so the session has one to snapshot.
        auth_client.get("/presentations/p_demo")
        # Patch the manifest to attach experts via direct_patch.
        patch_rv = auth_client.post(
            "/presentations/p_demo/patch",
            json={"patches": [{"op": "add", "path": "/bound_experts", "value": ["liq", "dep"]}]},
        )
        # If /patch rejects new top-level paths in this codebase, fall back
        # to writing directly via the session registry.
        if patch_rv.status_code != 200:
            registry = flask_app.config["SESSION_REGISTRY"]
            session = registry.get_or_create("A00000", "p_demo")
            m = session.get_manifest() or {}
            m["bound_experts"] = ["liq", "dep"]
            session.set_manifest(m)

        # Now snapshot.
        rv = auth_client.post("/presentations/p_demo/snapshot")
        assert rv.status_code == 200
        meta = rv.get_json()
        assert meta.get("bound_experts") == ["liq", "dep"], meta

        # And the loaded snapshot's frozen manifest also carries the field.
        store = flask_app.config["SNAPSHOT_STORE"]
        loaded = store.load(meta["snapshot_id"])
        assert loaded["manifest"].get("bound_experts") == ["liq", "dep"]
        assert loaded["meta"].get("bound_experts") == ["liq", "dep"]

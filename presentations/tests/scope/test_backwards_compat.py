"""Backwards compatibility (spec §1.4, §10.a): dashboards without a scope_ref
must behave exactly as before. Uses the existing manifest fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from presentations.manifest import validate_manifest
from presentations.nodes.validate_patch import _check_scope_contract

REPO_ROOT = Path(__file__).resolve().parents[3]
LEGACY_MANIFESTS = [
    REPO_ROOT / "examples" / "sample_manifest.json",
    REPO_ROOT / "examples" / "sessions" / "A16438" / "p_demo" / "manifest.json",
]


@pytest.fixture(params=LEGACY_MANIFESTS, ids=lambda p: p.name)
def legacy_manifest(request) -> dict:
    return json.loads(request.param.read_text(encoding="utf-8"))


def test_legacy_manifest_has_no_scope_ref(legacy_manifest):
    assert legacy_manifest.get("scope_ref") is None


def test_scope_ref_validation_adds_no_error_when_absent(legacy_manifest):
    # The new optional scope_ref block must not contribute any error to a
    # manifest that has none (pre-existing, unrelated errors are out of scope).
    errors = validate_manifest(legacy_manifest)
    assert not any("scope_ref" in e for e in errors)


def test_patch_enforcement_inert_without_contract(legacy_manifest):
    # Any patch passes the scope check when there is no scope contract.
    patches = [
        {"op": "replace", "path": "/blocks/0/title", "value": "X"},
        {"op": "replace", "path": "/filters/pinned/pf_anything/from", "value": "y"},
    ]
    assert _check_scope_contract(legacy_manifest, patches, None) == []


def test_adding_scope_ref_is_accepted(legacy_manifest):
    enriched = dict(legacy_manifest)
    enriched["scope_ref"] = {"presentation_id": "p_abc123", "scope_version": 4}
    errors = validate_manifest(enriched)
    assert not any("scope_ref" in e for e in errors)

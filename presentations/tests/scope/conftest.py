"""Shared fixtures for the Phase 8.a scope-contract test suite."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from presentations.scope._yaml import load_yaml
from presentations.scope.catalog import DictCatalog
from presentations.scope.schema import ScopeContract, load_scope_from_dict, load_scope_yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE8_DIR = REPO_ROOT / "examples" / "phase_8"


@pytest.fixture(scope="session")
def phase8_dir() -> Path:
    return PHASE8_DIR


@pytest.fixture(scope="session")
def sample_scope_text() -> str:
    return (PHASE8_DIR / "sample_scope.yaml").read_text(encoding="utf-8")


@pytest.fixture
def sample_scope(sample_scope_text) -> ScopeContract:
    return load_scope_yaml(sample_scope_text)


@pytest.fixture(scope="session")
def catalog() -> DictCatalog:
    raw = load_yaml((PHASE8_DIR / "sample_table_catalog_excerpt.yaml").read_text(encoding="utf-8"))
    return DictCatalog.from_excerpt(raw)


@pytest.fixture(scope="session")
def validator_cases() -> list[dict[str, Any]]:
    raw = load_yaml((PHASE8_DIR / "expected_validator_outputs.yaml").read_text(encoding="utf-8"))
    return raw["cases"]


@pytest.fixture
def scope_from_excerpt():
    """Build a full :class:`ScopeContract` from a partial ``scope_excerpt`` by
    filling in the required top-level fields with deterministic defaults."""
    base = dict(
        presentation_id="p_test",
        version=1,
        created_by="A16438",
        created_at=datetime(2026, 6, 15, tzinfo=timezone.utc).isoformat(),
    )

    def _build(excerpt: dict[str, Any]) -> ScopeContract:
        raw = dict(base)
        raw.update(excerpt)
        return load_scope_from_dict(raw)

    return _build

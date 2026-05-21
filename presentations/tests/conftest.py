"""Shared pytest fixtures for the Phase 6.5.a test suite."""
from __future__ import annotations

from pathlib import Path
from datetime import date

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "examples" / "phase_6_5"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def sample_block_dict() -> dict:
    return yaml.safe_load((FIXTURES_DIR / "sample_block.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def sample_block_2_dict() -> dict:
    return yaml.safe_load((FIXTURES_DIR / "sample_block_2.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def expected_resolved_sql() -> str:
    return (FIXTURES_DIR / "expected_resolved_query.sql").read_text(encoding="utf-8")


@pytest.fixture
def fixed_today() -> date:
    """Deterministic ``today`` for date-expression tests. Matches the
    'currentDate' note in CLAUDE memory."""
    return date(2026, 5, 21)

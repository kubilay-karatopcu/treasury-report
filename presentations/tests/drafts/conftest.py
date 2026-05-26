"""Shared fixtures for Phase 9.a draft-manager tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from presentations.drafts.manager import DraftManager
from presentations.session import SessionRegistry


class _FakeDC:
    """Same minimal filesystem stub used by the catalog tests."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = self.root / key.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def list_prefix(self, prefix: str) -> list[str]:
        base = self.root / prefix.lstrip("/")
        if not base.exists():
            return []
        out = []
        for f in base.rglob("*"):
            if f.is_file():
                out.append(f.relative_to(self.root).as_posix())
        return out

    def read_json(self, key: str):
        p = self._path(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return json.loads(p.read_text(encoding="utf-8"))

    def _upload_bytes(self, key: str, body: bytes, content_type: str | None = None):
        self._path(key).write_bytes(body)

    def delete_file(self, key: str):
        p = self._path(key)
        if p.exists():
            p.unlink()


@pytest.fixture
def dc(tmp_path) -> _FakeDC:
    return _FakeDC(root=tmp_path / "fake_s3")


@pytest.fixture
def session_registry(dc, tmp_path) -> SessionRegistry:
    return SessionRegistry(
        dc=dc,
        duck_base_dir=str(tmp_path / "sessions"),
        idle_timeout=1800,
    )


@pytest.fixture
def manager(session_registry, dc) -> DraftManager:
    return DraftManager(session_registry=session_registry, data_client=dc, gc_days=7)

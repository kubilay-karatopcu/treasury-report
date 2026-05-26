"""Top-level pytest fixtures.

Boots the real `app.py` with DEV_MODE=1 so:
- Oracle / S3 / LLM calls are replaced by fakes (`fake_db`, `LocalSnapshotStore`,
  `FakeLLM`).
- `before_request _inject_dev_user` auto-logs in every request as a stub user
  with `.sicil = "A00000"`, mirroring the real `User` interface.

Tests then use the standard Flask test client.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Force DEV_MODE before app import — app.py reads it at module load.
os.environ.setdefault("DEV_MODE", "1")

# Make project root importable from any cwd pytest runs in.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def flask_app():
    # Import here, after DEV_MODE is set in the environment.
    import app as app_module
    flask_app = app_module.app
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(flask_app):
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client):
    """DEV_MODE auto-logs in via `_inject_dev_user` before_request hook,
    so the test client is already authenticated. This alias keeps the
    test prompts readable."""
    return client

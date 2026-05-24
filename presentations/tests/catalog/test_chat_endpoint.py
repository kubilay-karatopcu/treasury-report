"""Phase 9.c — /atolye/kesif/chat HTTP endpoint smoke tests.

The discovery client itself is covered in test_discovery.py. These tests
exercise the route plumbing: auth, validation, FakeLLM round-trip,
history persistence on the draft manifest.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def chat_app(flask_app):
    """The catalog conftest already provides a Flask app with FakeLLM
    bound for many tests, but the chat route specifically requires
    LLM_CLIENT in app.config. Override it with a FakeLLM here so the
    propose_tables fast-path fires."""
    from presentations.llm import FakeLLM
    flask_app.config["LLM_CLIENT"] = FakeLLM()
    return flask_app


@pytest.fixture
def chat_client(chat_app):
    return chat_app.test_client()


def test_chat_rejects_empty_message(chat_client):
    r = chat_client.post("/presentations/atolye/kesif/chat", json={"message": "   "})
    assert r.status_code == 400


def test_chat_rejects_oversized_message(chat_client):
    r = chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "x" * 5000},
    )
    assert r.status_code == 400


def test_chat_returns_proposals_and_history(chat_client):
    r = chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "mevduat tabloları lazım"},
    )
    assert r.status_code == 200
    data = r.get_json()
    # User turn echoed.
    assert data["user_message"]["text"] == "mevduat tabloları lazım"
    # Assistant turn has a text response (FakeLLM provides canned text).
    assert isinstance(data["assistant_message"]["text"], str)
    # FakeLLM keyword-matched against catalog → at least one proposal
    # for DEPOSITS-related tables.
    proposals = data["assistant_message"]["proposals"]
    names = {p["name"] for p in proposals}
    assert names & {"DEPOSITS_DAILY", "DEPOSITS_BY_BRANCH"}
    # History contains both turns.
    assert len(data["history"]) >= 2


def test_chat_persists_across_calls(chat_client):
    """Second call's history must include the first turn — proves the
    draft manifest is being updated between requests."""
    r1 = chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "ilk mesaj"},
    )
    assert r1.status_code == 200

    r2 = chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "ikinci mesaj"},
    )
    data = r2.get_json()
    user_texts = [t["text"] for t in data["history"] if t["role"] == "user"]
    assert "ilk mesaj" in user_texts
    assert "ikinci mesaj" in user_texts


def test_chat_clear_wipes_history(chat_client):
    chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "doldur"},
    )
    r = chat_client.delete("/presentations/atolye/kesif/chat")
    assert r.status_code == 200
    assert r.get_json()["history"] == []


def test_chat_graceful_when_llm_unset(flask_app):
    """If the operator hasn't configured an LLM, chat should still
    respond (with a clear error message) instead of crashing."""
    flask_app.config["LLM_CLIENT"] = None
    c = flask_app.test_client()
    r = c.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "test"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["assistant_message"]["status"] == "error"


def test_bootstrap_payload_includes_chat_history(chat_client):
    """The Keşif page should embed any prior chat history in the
    bootstrap JSON so the drawer hydrates without a separate fetch."""
    import json
    import re
    # First send a message so history isn't empty.
    chat_client.post(
        "/presentations/atolye/kesif/chat",
        json={"message": "hydrate test"},
    )
    r = chat_client.get("/presentations/atolye/kesif")
    assert r.status_code == 200
    m = re.search(rb'<script id="kesif-data"[^>]*>(.*?)</script>', r.data, re.DOTALL)
    assert m
    payload = json.loads(m.group(1).decode("utf-8"))
    history = payload["chat"]["history"]
    assert any(t.get("text") == "hydrate test" for t in history)

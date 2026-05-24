"""Phase 9.c — discovery LLM client + prompt builder tests."""
from __future__ import annotations

import json

import pytest

from presentations.catalog.models import TableEntry
from presentations.discovery import (
    DiscoveryError,
    build_catalog_summary,
    build_user_message,
    propose_tables,
)


# ── Fixture entries ───────────────────────────────────────────────────────


def _make_entries():
    return [
        TableEntry(
            schema="EDW", name="DEPOSITS_DAILY",
            source="corporate", department="treasury",
            description="Günlük mevduat bakiye snapshot'ı.",
            concepts_bound=["branch", "as_of_time", "segment"],
        ),
        TableEntry(
            schema="EDW", name="NII_MONTHLY",
            source="corporate", department="treasury",
            description="Aylık NII — gerçekleşen ve tahmin.",
            concepts_bound=["as_of_time"],
        ),
        TableEntry(
            schema="ODS_RISK", name="CREDIT_RISK_EXPOSURE",
            source="corporate", department="risk",
            description="Kredi riski exposure metrikleri.",
            concepts_bound=["counterparty", "currency"],
        ),
    ]


# ── Prompt builder ────────────────────────────────────────────────────────


class TestCatalogSummary:
    def test_renders_every_entry_under_budget(self):
        out = build_catalog_summary(_make_entries(), token_budget=8000)
        assert "EDW.DEPOSITS_DAILY" in out
        assert "EDW.NII_MONTHLY" in out
        assert "ODS_RISK.CREDIT_RISK_EXPOSURE" in out
        # Each entry's concepts surface as a hint to the LLM.
        assert "branch" in out
        assert "counterparty" in out

    def test_truncates_when_over_budget(self):
        # 50-token budget = 200 chars — fits header + maybe one entry.
        out = build_catalog_summary(_make_entries(), token_budget=50)
        # Truncation notice must surface so the LLM knows the list is partial.
        assert "Token bütçesi" in out or "listelendi" in out

    def test_same_department_bias(self):
        entries = _make_entries()
        out = build_catalog_summary(entries, user_department="treasury", token_budget=8000)
        # Treasury tables should appear before the Risk one.
        treasury_idx = min(out.find("DEPOSITS_DAILY"), out.find("NII_MONTHLY"))
        risk_idx = out.find("CREDIT_RISK_EXPOSURE")
        assert treasury_idx < risk_idx


class TestUserMessage:
    def test_includes_user_request(self):
        msg = build_user_message(
            "şube performansı",
            catalog_summary="(catalog placeholder)",
        )
        assert "şube performansı" in msg

    def test_includes_department_and_basket(self):
        msg = build_user_message(
            "test",
            catalog_summary="(catalog)",
            user_department="treasury",
            current_basket=[{"table": "EDW.DEPOSITS_DAILY"}],
        )
        assert "treasury" in msg.lower()
        assert "EDW.DEPOSITS_DAILY" in msg

    def test_caps_history_to_last_10_turns(self):
        history = [
            {"role": "user", "text": f"msg{i}"} for i in range(15)
        ]
        msg = build_user_message(
            "current",
            catalog_summary="(catalog)",
            chat_history=history,
        )
        # The earliest 5 messages must be excluded.
        assert "msg0" not in msg
        assert "msg4" not in msg
        # The most recent 10 must appear.
        assert "msg14" in msg
        assert "msg5" in msg


# ── propose_tables — fast path (native client method) ─────────────────────


class _FakeLLMNative:
    """Stub that implements ``propose_tables`` natively (FakeLLM style)."""

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def propose_tables(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class TestProposeTablesNative:
    def test_passes_kwargs_through(self):
        client = _FakeLLMNative({
            "explanation": "ok",
            "proposals": [],
            "highlight_graph_node_ids": [],
        })
        propose_tables(
            client,
            user_request="hi",
            catalog_entries=_make_entries(),
            user_department="treasury",
        )
        assert client.calls[0]["user_request"] == "hi"
        assert client.calls[0]["user_department"] == "treasury"

    def test_drops_proposals_not_in_catalog(self):
        client = _FakeLLMNative({
            "explanation": "see below",
            "proposals": [
                {"schema": "EDW", "name": "DEPOSITS_DAILY", "match_score": 0.9, "rationale": "a"},
                {"schema": "EDW", "name": "DOES_NOT_EXIST", "match_score": 0.7, "rationale": "b"},
            ],
            "highlight_graph_node_ids": ["EDW.DEPOSITS_DAILY", "EDW.DOES_NOT_EXIST"],
        })
        result = propose_tables(client, user_request="x", catalog_entries=_make_entries())
        kept = [p.name for p in result.proposals]
        assert kept == ["DEPOSITS_DAILY"]
        assert result.dropped_proposals == [
            {"schema": "EDW", "name": "DOES_NOT_EXIST", "reason": "not_in_catalog"}
        ]
        # Highlights are filtered to known tables too.
        assert result.highlight_graph_node_ids == ["EDW.DEPOSITS_DAILY"]

    def test_score_clamped_to_unit_range(self):
        client = _FakeLLMNative({
            "explanation": "",
            "proposals": [
                {"schema": "EDW", "name": "DEPOSITS_DAILY", "match_score": 2.5, "rationale": ""},
                {"schema": "EDW", "name": "NII_MONTHLY", "match_score": -1, "rationale": ""},
            ],
            "highlight_graph_node_ids": [],
        })
        result = propose_tables(client, user_request="x", catalog_entries=_make_entries())
        scores = [p.match_score for p in result.proposals]
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_highlights_default_to_kept_proposals(self):
        """When the LLM forgot to populate highlight_graph_node_ids,
        derive it from the kept proposals."""
        client = _FakeLLMNative({
            "explanation": "...",
            "proposals": [
                {"schema": "EDW", "name": "DEPOSITS_DAILY", "match_score": 0.8, "rationale": "x"},
            ],
            # missing field entirely
        })
        result = propose_tables(client, user_request="x", catalog_entries=_make_entries())
        assert result.highlight_graph_node_ids == ["EDW.DEPOSITS_DAILY"]


# ── propose_tables — slow path (text completion + JSON parse) ─────────────


class _FakeLLMCompletion:
    """Stub that only has ``complete()`` — no native propose_tables.
    Returns whatever text the test pre-loaded, so we can exercise the
    JSON parser + retry path."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, **kwargs):
        self.calls.append((system, user))
        if not self.responses:
            raise RuntimeError("no more canned responses")
        return self.responses.pop(0)


class TestProposeTablesSlowPath:
    def test_parses_clean_json_response(self):
        canned = json.dumps({
            "explanation": "Tek tablo öneriyorum.",
            "proposals": [
                {"schema": "EDW", "name": "DEPOSITS_DAILY", "match_score": 0.9, "rationale": "x"},
            ],
            "highlight_graph_node_ids": ["EDW.DEPOSITS_DAILY"],
        })
        client = _FakeLLMCompletion([canned])
        result = propose_tables(client, user_request="hi", catalog_entries=_make_entries())
        assert len(result.proposals) == 1
        assert result.explanation.startswith("Tek tablo")
        assert len(client.calls) == 1  # no retry needed

    def test_retries_once_on_invalid_json(self):
        canned_bad = "merhaba — bu JSON değil"
        canned_ok = json.dumps({
            "explanation": "tamam",
            "proposals": [],
            "highlight_graph_node_ids": [],
        })
        client = _FakeLLMCompletion([canned_bad, canned_ok])
        result = propose_tables(client, user_request="hi", catalog_entries=_make_entries())
        assert len(client.calls) == 2
        assert result.proposals == []

    def test_strips_markdown_code_fence(self):
        canned = "```json\n" + json.dumps({
            "explanation": "fenced",
            "proposals": [],
            "highlight_graph_node_ids": [],
        }) + "\n```"
        client = _FakeLLMCompletion([canned])
        result = propose_tables(client, user_request="x", catalog_entries=_make_entries())
        assert result.explanation == "fenced"

    def test_raises_after_two_failures(self):
        client = _FakeLLMCompletion(["nope1", "nope2"])
        with pytest.raises(DiscoveryError):
            propose_tables(client, user_request="x", catalog_entries=_make_entries())
        assert len(client.calls) == 2

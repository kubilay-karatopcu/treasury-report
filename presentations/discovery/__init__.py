"""Phase 9.c — Keşif LLM chat (Discovery).

The Stage 1 LLM has a deliberately narrow job: propose tables that match
the user's request. It does NOT write SQL, touch scope, propose joins,
or add anything to the basket — every change still goes through user
clicks. See spec §5.

This package wraps the existing :mod:`presentations.llm` clients
(QwenClient / FakeLLM) with the discovery-specific prompt + the §5.3
JSON contract validator.
"""
from presentations.discovery.client import (
    DiscoveryError,
    DiscoveryResult,
    DiscoveryProposal,
    propose_tables,
)
from presentations.discovery.prompt import (
    DEFAULT_TOKEN_BUDGET,
    build_catalog_summary,
    build_system_prompt,
    build_user_message,
)

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "DiscoveryError",
    "DiscoveryProposal",
    "DiscoveryResult",
    "build_catalog_summary",
    "build_system_prompt",
    "build_user_message",
    "propose_tables",
]

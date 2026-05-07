"""
Decide what flavour of fetch the upcoming generate_patch step needs.

Three modes (from CLAUDE.md):
- "render"  : no data movement; manifest mutation is enough
- "requery" : DuckDB SQL re-run, basket already has the right tables
- "refetch" : basket changed; Oracle round-trip needed

Phase 4 keeps this conservative — we always refetch when the in-memory DuckDB
state doesn't match the manifest's basket signature; otherwise render. The
"requery" path becomes meaningful once the LLM emits SQL (planned for Phase 5+).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def plan_fetch(state):
    """Decide the fetch mode and stash it on state.fetch_mode."""
    session = state.session
    basket = state.manifest.get("basket", [])

    if not basket:
        state.fetch_mode = "render"
        return state

    if session is not None and session.needs_refetch(basket):
        state.fetch_mode = "refetch"
        log.info("plan_fetch: refetch (basket changed or DuckDB cold)")
    else:
        state.fetch_mode = "render"

    return state

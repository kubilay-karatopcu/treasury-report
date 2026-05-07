"""Generate patches by calling the LLM with the appropriate system prompt."""
from __future__ import annotations

import logging

from flask import current_app

from presentations.llm import load_prompt
from presentations.duck import summarize_views

log = logging.getLogger(__name__)


def generate_patch(state):
    """
    Read state.user_message + state.manifest + state.selected_block_id,
    plus an optional DuckDB data summary (Phase 5.5), call the LLM,
    set state.pending_patches and state.explanation.
    """
    llm = current_app.config["LLM_CLIENT"]
    system = load_prompt("edit")

    if state.validation_errors:
        feedback = (
            "\n\n[Önceki deneme reddedildi. Lütfen aşağıdaki hataları düzelt:]\n- "
            + "\n- ".join(state.validation_errors)
        )
        user_message = state.user_message + feedback
    else:
        user_message = state.user_message

    data_summary = _build_data_summary(state)

    patches, explanation = llm.generate_patches(
        system=system,
        user_message=user_message,
        manifest=state.manifest,
        selected_block_id=state.selected_block_id,
        data_summary=data_summary,
    )

    state.pending_patches = patches
    state.explanation = explanation
    state.validation_errors = []
    return state


def _build_data_summary(state) -> dict | None:
    """If the session has loaded views, extract a compact schema+sample+stats
    snapshot for the LLM. Returns None when there's no DuckDB to peek at."""
    if state.session is None:
        return None
    try:
        views = state.session.loaded_views()
    except Exception as exc:
        log.warning("generate_patch: loaded_views() failed: %s", exc)
        return None
    if not views:
        return None
    try:
        return summarize_views(state.session.get_duck_conn(), views)
    except Exception as exc:
        log.warning("generate_patch: summarize_views failed: %s", exc)
        return None

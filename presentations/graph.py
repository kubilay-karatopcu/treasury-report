"""
Pipeline runner: plan_fetch → (fetch_data) → generate → validate → (retry) → apply.

Phase 4 plugs in plan_fetch + fetch_data so the LLM sees a fresh DuckDB session
when the basket changes. LangGraph can be swapped in later without changing the
public surface (run_pipeline yielding SSE events).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

from presentations.nodes.plan_fetch import plan_fetch
from presentations.nodes.fetch_data import fetch_data
from presentations.nodes.generate_patch import generate_patch
from presentations.nodes.validate_patch import validate_patch
from presentations.nodes.apply_patch import apply_patch


@dataclass
class GraphState:
    presentation_id: str
    manifest: dict
    user_message: str
    selected_block_id: Optional[str] = None
    session: object = None              # PresentationSession or None

    # Routing decision set by plan_fetch.
    fetch_mode: str = "render"          # "render" | "requery" | "refetch"
    loaded_views: dict = field(default_factory=dict)

    pending_patches: list = field(default_factory=list)
    validation_errors: list = field(default_factory=list)
    explanation: str = ""
    retries_left: int = 1
    new_manifest: Optional[dict] = None


def run_pipeline(state: GraphState) -> Iterator[dict]:
    """Yields SSE events: status / patch / error / done."""
    state = plan_fetch(state)

    if state.fetch_mode == "refetch":
        yield {"event": "status", "data": {"phase": "fetching"}}
        try:
            state = fetch_data(state)
        except Exception as exc:
            yield {"event": "error", "data": {"message": f"Veri çekilemedi: {exc}"}}
            return

    yield {"event": "status", "data": {"phase": "thinking"}}

    while True:
        state = generate_patch(state)
        state = validate_patch(state)

        if not state.validation_errors:
            break

        if state.retries_left <= 0:
            yield {
                "event": "error",
                "data": {
                    "message": "Geçerli patch üretilemedi: " + "; ".join(state.validation_errors),
                },
            }
            return

        state.retries_left -= 1
        yield {"event": "status", "data": {"phase": "retrying"}}

    if state.pending_patches:
        yield {"event": "status", "data": {"phase": "applying"}}
        state = apply_patch(state)
        yield {
            "event": "patch",
            "data": {
                "patches": state.pending_patches,
                "explanation": state.explanation,
            },
        }
    else:
        yield {
            "event": "status",
            "data": {"phase": "noop", "explanation": state.explanation},
        }

    final_version = (
        state.new_manifest.get("version") if state.new_manifest else state.manifest.get("version", 0)
    )
    yield {"event": "done", "data": {"manifest_version": final_version}}

"""
Pipeline runner: plan_fetch → (fetch_data) → generate → validate → execute_sqls → apply.

Phase 4 plugged in plan_fetch + fetch_data for the legacy basket path.
Adım 2 inserts execute_block_sqls between validate and apply so LLM-produced
Oracle SQL is actually run, results are registered in DuckDB, and provenance
(sql + sample + meta) is persisted on each block.

Retry semantics:
- validate_patch errors → retry (schema fixes)
- execute_block_sqls errors → retry (SQL fixes: gate rejections, Oracle errors)
Both error kinds share state.validation_errors so the existing retry loop
already handles them with one extra cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Optional

from presentations.nodes.plan_fetch import plan_fetch
from presentations.nodes.fetch_data import fetch_data
from presentations.nodes.generate_patch import generate_patch
from presentations.nodes.validate_patch import validate_patch
from presentations.nodes.execute_block_sqls import execute_block_sqls
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
    retries_left: int = 2               # Bumped from 1: SQL errors deserve an
                                        # extra retry vs pure schema errors.
    new_manifest: Optional[dict] = None
    # F.5: Library suggestions — LLM kütüphaneden uygun blok bulduysa burada
    # döner. patches=[] ise graph apply'ı atlar, sadece suggestion event'i emit
    # eder. User accept ederse frontend addLibraryBlockToSection çağırır.
    suggestions: list = field(default_factory=list)


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

        # Schema OK → run any embedded SQL. Adım 2 addition.
        # We skip SQL execution if validate already failed — the LLM needs to
        # fix the structure first.
        if not state.validation_errors and state.pending_patches:
            yield {"event": "status", "data": {"phase": "querying"}}
            state = execute_block_sqls(state)

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
        try:
            state = apply_patch(state)
        except Exception as exc:
            # Validate dry-run geçti ama execute_block_sqls patches'a ekleme
            # yaptıysa son hâl invalid olabilir — kullanıcıya friendly mesaj
            import logging
            logging.getLogger(__name__).exception("apply_patch failed: %s", exc)
            yield {
                "event": "error",
                "data": {
                    "message": (
                        f"Patch uygulanamadı: {exc!r}. "
                        "LLM çıktısı tutarsız olabilir, biraz farklı bir şekilde yeniden dene "
                        "(örn. 'sadece TEB ve QNB bankalarını göster' yerine 'WHERE BANK_NAME IN (...)' SQL'inde değişiklik iste)."
                    ),
                },
            }
            return
        yield {
            "event": "patch",
            "data": {
                "patches": state.pending_patches,
                "explanation": state.explanation,
            },
        }
    elif state.suggestions:
        # Library suggestion: kullanıcının onayını bekle, manifest değişmedi.
        yield {
            "event": "suggestion",
            "data": {
                "suggestions": state.suggestions,
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
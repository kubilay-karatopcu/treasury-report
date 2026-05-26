"""Phase 9.c — prompt builders for the discovery LLM.

Two callables:

- :func:`build_catalog_summary` — renders the catalog as a compact list of
  ``schema.table`` entries with descriptions + concepts bound. The result
  is truncated to ``token_budget`` (default 8k tokens) using a rough
  4-char-per-token approximation. Tables are prioritised by:
    1. same department as the user (catalog-affinity bias);
    2. concept-binding count (heavier tables surfaced first).
  The budget is generous in dev (6 tables, ~600 tokens), but the same
  logic survives the 10k-table prod world.

- :func:`build_user_message` — composes the per-turn user payload: the
  current basket, recent chat history (last 10 turns), the user's
  request, and the catalog summary.

The system prompt is loaded from ``presentations/prompts/discover.txt``
(via :func:`presentations.llm.load_prompt`).
"""
from __future__ import annotations

from typing import Iterable

from presentations.catalog.models import TableEntry
from presentations.llm import load_prompt


# Rough char-to-token ratio. Same heuristic as ``presentations.llm``.
_CHARS_PER_TOKEN = 4
DEFAULT_TOKEN_BUDGET = 8000

# Max chat history turns we feed back to the LLM. Spec §5.2.
HISTORY_TURN_CAP = 10


def build_system_prompt() -> str:
    """Load and return the discovery system prompt verbatim."""
    return load_prompt("discover")


def _entry_priority(entry: TableEntry, user_dept: str | None) -> tuple[int, int]:
    """Lower tuple sorts earlier. We rank by (dept-affinity, binding count)
    descending — so same-dept + heavily-bound tables come first when we
    have to truncate."""
    same_dept = 0 if (user_dept and entry.department == user_dept) else 1
    # Negative count → descending in lexicographic sort.
    return (same_dept, -len(entry.concepts_bound or []))


def build_catalog_summary(
    entries: Iterable[TableEntry],
    *,
    user_department: str | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Render the catalog list for the LLM, capped at ``token_budget``.

    Each row is one line: ``<schema>.<table>  [dept] — <description>  · concepts: a, b, c``.
    The line is kept short on purpose; long descriptions are truncated
    at ~140 chars so a single tableYAML doesn't dominate the budget.
    """
    sorted_entries = sorted(list(entries), key=lambda e: _entry_priority(e, user_department))

    char_budget = token_budget * _CHARS_PER_TOKEN
    lines: list[str] = []
    header = (
        "## Katalogdaki tablolar\n"
        "Aşağıdaki tabloları kullanarak öneride bulun. ``schema.table`` "
        "ikilisi burada olmayan bir tabloyu önerme.\n\n"
    )
    used = len(header)
    truncated = False

    for entry in sorted_entries:
        line = _format_entry_line(entry)
        # +1 for the trailing newline we add.
        cost = len(line) + 1
        if used + cost > char_budget:
            truncated = True
            break
        lines.append(line)
        used += cost

    body = header + "\n".join(lines)
    if truncated:
        body += (
            f"\n\n*(Token bütçesi nedeniyle {len(lines)} / {len(sorted_entries)} "
            "tablo listelendi. Öneri yalnızca yukarıdaki listeden olmalı.)*"
        )
    return body


def _format_entry_line(entry: TableEntry) -> str:
    table_id = f"{entry.schema_name}.{entry.name}"
    desc = (entry.description or "").strip().split("\n")[0]
    if len(desc) > 140:
        desc = desc[:137] + "…"
    bits = [f"- **{table_id}**"]
    if entry.department:
        bits.append(f"[{entry.department}]")
    if desc:
        bits.append(f"— {desc}")
    if entry.concepts_bound:
        bits.append(f"· concepts: {', '.join(entry.concepts_bound)}")
    if entry.source == "user_upload":
        bits.append("· (yükleme)")
    return " ".join(bits)


def build_user_message(
    user_request: str,
    *,
    catalog_summary: str,
    current_basket: list[dict] | None = None,
    chat_history: list[dict] | None = None,
    user_department: str | None = None,
) -> str:
    """Compose the per-turn user-side payload for the discovery LLM.

    Sections (in order, all optional except the request itself):
      1. user_department — single line tag, sets dept-affinity bias.
      2. current_basket — table ids already added; don't re-propose.
      3. catalog_summary — the budget-truncated catalog list.
      4. chat_history — last HISTORY_TURN_CAP turns.
      5. user_request — the latest message.
    """
    parts: list[str] = []
    if user_department:
        parts.append(f"## Kullanıcı bilgisi\nDepartman: {user_department}")

    basket = current_basket or []
    if basket:
        listed = "\n".join(f"- {b.get('table', '?')}" for b in basket if b.get("table"))
        parts.append("## Sepette mevcut tablolar (tekrar önerme)\n" + listed)

    parts.append(catalog_summary)

    history = chat_history or []
    if history:
        recent = history[-HISTORY_TURN_CAP:]
        history_lines = []
        for turn in recent:
            role = turn.get("role", "user")
            content = (turn.get("text") or turn.get("content") or "").strip()
            if not content:
                continue
            # Cap each turn to keep budget predictable.
            history_lines.append(f"- **{role}:** {content[:400]}")
        if history_lines:
            parts.append("## Önceki mesajlar\n" + "\n".join(history_lines))

    parts.append("# Yeni talep\n" + user_request.strip())
    return "\n\n".join(parts)

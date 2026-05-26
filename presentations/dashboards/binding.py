"""Dashboard filter ↔ block variable binding resolution (Phase 6.5.c).

This is the seam left open in :mod:`presentations.variables.resolver` —
it wires a dashboard's ``filters`` + a block's ``variable_bindings`` into
the ``BindingResolver`` callback that ``resolve_variables()`` already
consults.

Two main entry points:

- :func:`build_binding_resolver` — given the dashboard filter state and a
  block's variable_bindings, returns a callback usable with
  ``resolve_variables(block, binding_resolver=cb)``.
- :func:`propose_auto_bindings` — Phase 6.5.c §3.5 auto-binding by
  semantic_tag. Surfaces suggestions; the user confirms via the UI.

The "Filter eklemek ister misiniz?" prompt is just the UI side of
:func:`unbound_variables` — every block variable whose semantic_tag has no
matching filter is a candidate for that banner.
"""
from __future__ import annotations

from typing import Any, Iterable

from presentations.blocks.schema import Block, Variable
from presentations.dashboards.schema import DashboardFilter, VariableBinding
from presentations.variables.resolver import BindingValue, parse_date_expr


# ── Auto-binding ──────────────────────────────────────────────────────────

def propose_auto_bindings(
    block_variables: Iterable[Variable],
    dashboard_filters: Iterable[DashboardFilter],
) -> dict[str, VariableBinding]:
    """Return ``{variable_name: VariableBinding}`` suggestions.

    Rules (spec §3.5):

    - For each block variable ``v``: find dashboard filters with matching
      ``semantic_tag``.
    - If exactly one match: propose ``from_filter: <id>``.
        - For ``date`` variables bound to a ``date_range`` filter, pick the
          accessor heuristically from the variable name: ``*_from / *_since
          / *_start`` → ``from``, ``*_to / *_until / *_end`` → ``to``,
          otherwise leave the accessor None and let the UI ask.
    - If multiple matches: leave unbound (caller's UI surfaces the choice).
    - If zero matches: leave unbound (caller's UI fires the "filter ekle?"
      prompt).
    """
    by_tag: dict[str, list[DashboardFilter]] = {}
    for f in dashboard_filters:
        by_tag.setdefault(f.semantic_tag, []).append(f)

    out: dict[str, VariableBinding] = {}
    for var in block_variables:
        candidates = by_tag.get(var.semantic_tag, [])
        if len(candidates) != 1:
            continue
        f = candidates[0]
        if var.type == "date" and f.type == "date_range":
            accessor = _accessor_from_name(var.name)
            if accessor is None:
                # Ambiguous — UI must ask the user.
                continue
            out[var.name] = VariableBinding(from_filter=f.id, accessor=accessor)
        elif var.type == f.type:
            out[var.name] = VariableBinding(from_filter=f.id)
        # else: type mismatch — UI should warn (e.g. enum_single ↔ enum_multi).
    return out


def _accessor_from_name(name: str) -> str | None:
    lower = name.lower()
    for suffix in ("_from", "_since", "_start"):
        if lower.endswith(suffix):
            return "from"
    for suffix in ("_to", "_until", "_end"):
        if lower.endswith(suffix):
            return "to"
    return None


def unbound_variables(
    block_variables: Iterable[Variable],
    variable_bindings: dict[str, VariableBinding],
    dashboard_filters: Iterable[DashboardFilter],
) -> list[Variable]:
    """Variables that have neither a binding nor a semantic_tag match.

    These are the candidates the "Filter eklemek ister misiniz?" banner
    targets. A variable with a ``constant`` binding is not unbound. A
    variable whose semantic_tag matches an existing filter but lacks an
    explicit binding is *partially* unbound — we still surface it so the
    user can either accept the auto-binding or skip.
    """
    tags = {f.semantic_tag for f in dashboard_filters}
    out = []
    for var in block_variables:
        if var.name in variable_bindings:
            continue
        if var.semantic_tag in tags:
            # Semantic tag match exists but no explicit binding yet.
            out.append(var)
            continue
        # No tag match at all — definitely unbound.
        out.append(var)
    return out


# ── Resolver callback ─────────────────────────────────────────────────────

def build_binding_resolver(
    variable_bindings: dict[str, VariableBinding],
    dashboard_filter_state: dict[str, Any],
):
    """Build the ``BindingResolver`` callback for ``resolve_variables``.

    Args:
        variable_bindings: ``{variable_name: VariableBinding}`` from the
            block's manifest entry.
        dashboard_filter_state: ``{filter_id: <current_value>}`` — the
            dashboard's live filter state (defaults + any user mutations).

    Returns:
        Callable that ``resolve_variables`` will call once per declared
        block variable; returns ``BindingValue`` or None.
    """
    def _cb(var: Variable):
        binding = variable_bindings.get(var.name)
        if binding is None:
            return None
        if binding.constant is not None:
            # Constants are expression strings for date types, literals
            # otherwise. resolve_variables will run parse_date_expr on
            # date / date_range overrides automatically (via is_expression).
            return BindingValue(
                value=binding.constant,
                is_expression=(var.type in ("date", "date_range")),
            )

        # from_filter path.
        filter_id = binding.from_filter
        if filter_id is None:
            return None
        value = dashboard_filter_state.get(filter_id)
        if value is None:
            return None
        if binding.accessor is not None:
            if isinstance(value, dict) and binding.accessor in value:
                value = value[binding.accessor]
            else:
                # The dashboard filter type doesn't carry the accessor —
                # likely a mis-bind. Return None so the resolver falls
                # back to the block's default.
                return None
        return BindingValue(value=value, is_expression=False)

    return _cb


# ── Filter state initialization ───────────────────────────────────────────

def initial_filter_state(filters: Iterable[DashboardFilter]) -> dict[str, Any]:
    """Materialise the initial filter state from declared defaults.

    Date expressions in ``default`` are parsed here (so ``today - 30d``
    becomes a real ``date`` object); enum defaults pass through unchanged.
    """
    state: dict[str, Any] = {}
    for f in filters:
        if f.default is None:
            continue
        if f.type == "date_range":
            d = f.default
            state[f.id] = {
                "from": parse_date_expr(d["from"]),
                "to":   parse_date_expr(d["to"]),
            }
        elif f.type in ("enum_single", "enum_multi", "number_range"):
            state[f.id] = f.default
    return state

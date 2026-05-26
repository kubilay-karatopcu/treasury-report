"""Scope-aware variable resolution + routing-aware execution (spec §4.2).

Two seams that Sunum's block-execution layer gains when a dashboard carries a
``scope_ref``:

1. **Variable resolution.** A block variable bound to a *pinned* scope filter
   resolves to the pinned value, ignoring any dashboard widget state (§4.2
   step 2). A variable bound to an *interactive* scope filter takes the live
   widget value, exactly like a Phase 6.5 ``from_filter`` binding. Everything
   else (``from_filter``, ``constant``, unbound) falls through to the existing
   Phase 6.5 resolver path. :func:`build_scope_binding_resolver` produces the
   ``BindingResolver`` callback that ``resolve_variables`` already consults.

2. **Routing.** Before executing a block, :func:`check_block_routing` looks at
   which basket aliases the block references. For cached aliases the existing
   DuckDB view (materialised by ``fetch_cached_tables``) is used as-is. For
   ``lazy`` aliases the block-execution path pulls the table on demand via
   :func:`ensure_lazy_alias_loaded` (8.d): a parameterised Oracle SELECT —
   shrunk by the same pinned-filter pushdown that cached tables get — runs and
   the result is registered as a DuckDB view for the rest of the session.

The lazy fetch is memoised per ``(scope_version, alias)`` on the DuckDB
connection so a single Sunum render that references the same lazy alias from
multiple blocks pulls Oracle exactly once.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from presentations.scope.schema import (
    InteractiveFilter,
    PinnedFilter,
    ScopeContract,
)
from presentations.variables.resolver import BindingValue


# ── Binding-shape access (works for both VariableBinding objects and dicts) ──

def _attr(binding: Any, name: str) -> Any:
    if binding is None:
        return None
    if isinstance(binding, dict):
        return binding.get(name)
    return getattr(binding, name, None)


# ── Pinned / interactive value extraction ──────────────────────────────────

def _pinned_binding_value(pf: PinnedFilter, var, accessor: str | None) -> BindingValue | None:
    """The immutable value a pinned filter supplies to a block variable."""
    t = getattr(var, "type", None)
    if t == "date":
        val = pf.to if accessor == "to" else pf.from_
        return BindingValue(value=val, is_expression=True)
    if t == "date_range":
        return BindingValue(value={"from": pf.from_, "to": pf.to}, is_expression=True)
    if t == "number_range":
        return BindingValue(value={"min": pf.from_, "max": pf.to}, is_expression=False)
    if t == "enum_multi":
        if pf.values is not None:
            vals = list(pf.values)
        elif pf.value is not None:
            vals = [pf.value]
        else:
            vals = []
        return BindingValue(value=vals, is_expression=False)
    if t == "enum_single":
        if pf.values:
            return BindingValue(value=pf.values[0], is_expression=False)
        return BindingValue(value=pf.value, is_expression=False)
    return None


def _interactive_binding_value(
    inter: InteractiveFilter, var, state: dict[str, Any], accessor: str | None,
) -> BindingValue | None:
    """The live widget value an interactive scope filter supplies."""
    val = state.get(inter.id) if state else None
    if val is None:
        val = inter.default_values
        if getattr(var, "type", None) == "enum_single" and isinstance(val, list):
            val = val[0] if val else None
    if accessor and isinstance(val, dict) and accessor in val:
        val = val[accessor]
    return BindingValue(
        value=val,
        is_expression=getattr(var, "type", None) in ("date", "date_range"),
    )


# ── Resolver callback ───────────────────────────────────────────────────────

def build_scope_binding_resolver(
    scope: ScopeContract,
    variable_bindings: dict[str, Any],
    dashboard_filter_state: dict[str, Any] | None = None,
    *,
    inner: Callable[[Any], BindingValue | None] | None = None,
):
    """Build the scope-aware ``BindingResolver`` for ``resolve_variables``.

    Args:
        scope: the active scope contract.
        variable_bindings: ``{variable_name: VariableBinding | dict}`` for the
            block (from the dashboard manifest entry).
        dashboard_filter_state: ``{filter_id: value}`` live widget state.
        inner: the Phase 6.5 dashboard resolver to fall through to for
            ``from_filter`` / ``constant`` bindings (optional).
    """
    state = dashboard_filter_state or {}

    def _cb(var):
        binding = variable_bindings.get(var.name)
        scope_fid = _attr(binding, "from_scope_filter")
        if scope_fid:
            accessor = _attr(binding, "accessor")
            pinned = scope.find_pinned(scope_fid)
            if pinned is not None:
                # Pinned wins absolutely — widget state is never consulted.
                return _pinned_binding_value(pinned, var, accessor)
            inter = scope.find_interactive(scope_fid)
            if inter is not None:
                return _interactive_binding_value(inter, var, state, accessor)
            # Dangling reference — let the resolver fall back to the default.
            return None
        return inner(var) if inner is not None else None

    return _cb


def is_pinned_bound(scope: ScopeContract, binding: Any) -> bool:
    """True if ``binding`` resolves to a *pinned* scope filter. Used by the
    patch validator to reject mutations of pinned-bound variables (§4.1)."""
    scope_fid = _attr(binding, "from_scope_filter")
    if not scope_fid:
        return False
    return scope.find_pinned(scope_fid) is not None


# ── Routing-aware execution guard (the lazy path lands in 8.d) ──────────────

_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)


def referenced_aliases(sql: str) -> set[str]:
    """Lower-cased basket aliases a block SQL references via FROM / JOIN."""
    if not sql:
        return set()
    return {m.group(1).lower() for m in _FROM_JOIN_RE.finditer(sql)}


def routing_for_alias(scope: ScopeContract, alias: str) -> str | None:
    """``"lazy"`` / ``"cached"`` for an alias, from scope status (preferred)
    or the basket routing decision. ``None`` if the alias is unknown."""
    if alias in scope.status.lazy_tables:
        return "lazy"
    if alias in scope.status.cached_tables:
        return "cached"
    item = scope.basket_item(alias)
    return item.routing.decision if item else None


def check_block_routing(scope: ScopeContract, aliases) -> None:
    """Guard before executing a block.

    Lazy aliases must be materialised first via :func:`ensure_lazy_alias_loaded`.
    This helper raises only when the block references a lazy alias that has
    not yet been loaded into the connection — i.e. the caller forgot to call
    ``ensure_lazy_alias_loaded`` before executing the block.

    Cached aliases (and lazy aliases that have already been loaded) pass
    silently. Aliases unknown to the scope pass through (might be a DuckDB
    block view that doesn't correspond to a scope alias at all).
    """
    for alias in aliases:
        if scope.is_lazy_alias(alias):
            raise NotImplementedError(
                f"Lazy alias {alias!r} not materialised. Call "
                f"ensure_lazy_alias_loaded(scope, conn, dc, alias=...) first."
            )


# Per-connection cache of materialised lazy aliases. Keyed by ``id(conn)``
# because the DuckDB connection rejects arbitrary attributes. The
# SessionRegistry owns connection lifetime, so leaks are bounded by session
# count and a stale id collision only affects an already-evicted session.
_LAZY_CACHE: dict[int, set[str]] = {}


def _lazy_cache_for(conn) -> set[str]:
    key = id(conn)
    cache = _LAZY_CACHE.get(key)
    if cache is None:
        cache = set()
        _LAZY_CACHE[key] = cache
    return cache


def ensure_lazy_alias_loaded(
    scope: ScopeContract,
    conn,
    dc,
    alias: str,
    *,
    catalog=None,
    concept_registry=None,
    binding_catalog=None,
) -> bool:
    """Materialise a lazy basket alias into a DuckDB view for this connection
    (spec §3.3, 8.d).

    The fetch is identical to the cached path (same pinned filter pushdown via
    the Phase 7 compiler, same projection) — the only difference is *when* it
    runs. Cached fetch happens at scope-build time; lazy fetch happens at
    block-execution time, on demand, the first time any block references the
    alias.

    Once loaded, the alias is recorded in :data:`_LAZY_CACHE` (keyed by the
    connection's ``id()``) so subsequent block runs in the same session reuse
    the DuckDB view. The session lifetime owns this cache;
    :func:`invalidate_lazy_cache` trims it when the scope version changes.

    Returns ``True`` when a fetch happened, ``False`` when the alias was
    already loaded (or isn't actually lazy / doesn't exist).
    """
    # Bail fast for non-lazy or unknown aliases — keep the call site cheap.
    item = scope.basket_item(alias)
    if item is None or item.table_ref is None or item.routing.decision != "lazy":
        return False

    cache = _lazy_cache_for(conn)
    if alias in cache:
        return False

    # Re-use the cached-fetch SQL composer so the pushdown logic stays in one
    # place. Lazy block-level interactive filters get layered in at block-SQL
    # rewrite time (existing variable-resolver path); the lazy fetch SQL only
    # carries the pinned filters.
    from presentations.scope.fetch import compose_cached_sql
    from presentations.duck import register_dataframe
    import pandas as pd

    sql, binds = compose_cached_sql(
        scope, item, catalog,
        concept_registry=concept_registry,
        binding_catalog=binding_catalog,
    )
    df = dc.get_data(
        base_prefix=None,
        dataset=f"scope-lazy::{scope.presentation_id}/{alias}",
        query=sql, query_params=binds,
    )
    if df is None:
        df = pd.DataFrame()
    if len(df.columns) > 0:
        register_dataframe(conn, alias, df)
    cache.add(alias)
    return True


def invalidate_lazy_cache(conn, aliases=None) -> None:
    """Drop one or more lazy alias views from the connection cache so the next
    block reference re-fetches from Oracle. ``aliases=None`` drops them all
    (used when a new scope version supersedes the current one)."""
    cache = _LAZY_CACHE.get(id(conn))
    if not cache:
        return
    if aliases is None:
        targets = list(cache)
    else:
        targets = [a for a in aliases if a in cache]
    for a in targets:
        # DuckDB views registered from a DataFrame are TABLES of the catalog;
        # `register_dataframe` uses `conn.register(name, df)`. Unregister first
        # (clears the binding) then drop the view for safety.
        try:
            conn.unregister(a)
        except Exception:
            pass
        try:
            conn.execute(f"DROP VIEW IF EXISTS {a}")
        except Exception:
            pass
        cache.discard(a)

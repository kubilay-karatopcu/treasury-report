"""Bind variable expansion — Phase 6.5.

Rewrites a block's raw SQL with placeholders bound to resolved variable
values, ready for execution against Oracle via the existing ``DataClient``.

Critical contract (spec §4.2): **values are NEVER concatenated into the SQL
string.** Scalars stay as bind parameters; ``enum_multi`` is expanded to a
positional list of bind names — for example ``IN (:currency_list)`` with
``currency_list = ["TRY", "USD"]`` becomes
``IN (:currency_list_0, :currency_list_1)`` with binds
``{currency_list_0: "TRY", currency_list_1: "USD"}``.

Date values become :class:`datetime.date` objects in the bind dict, never
strings, so ``oracledb`` and DuckDB bind them as native DATE.

This module is consumed by :mod:`presentations.routes_blocks` for the "run
block" endpoint and by Phase 6.5.c's filter dispatch. It is intentionally
free of any database dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from presentations.blocks.schema import Block, Variable


# ── Public dataclass ──────────────────────────────────────────────────────

@dataclass
class BoundQuery:
    """Result of :func:`expand_binds`.

    Attributes:
        sql: Rewritten SQL with ``enum_multi`` placeholders expanded.
        params: Bind dict suitable for ``oracledb`` / DuckDB execution.
    """

    sql: str
    params: dict[str, Any]


class EmptySelectionError(ValueError):
    """An ``enum_multi`` variable resolved to an empty list.

    Raised by :func:`expand_binds` so the application layer can short-circuit
    the query and render an empty chart instead of crashing on a SQL syntax
    error (Oracle and DuckDB both reject ``IN ()``).

    Attribute ``variable_name`` carries which variable was empty so the caller
    can attribute the empty state in the UI.
    """

    def __init__(self, variable_name: str):
        super().__init__(
            f"variable :{variable_name} (enum_multi) resolved to an empty "
            "selection. Caller should treat the result as zero rows."
        )
        self.variable_name = variable_name


# ── Internals ─────────────────────────────────────────────────────────────

# Match :ident exactly — must not be preceded by another ':' (Postgres cast).
# Anchored at word boundaries on both ends so foo_bar isn't matched as bar.
_BIND_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b")


def _index_variables(block: Block) -> dict[str, Variable]:
    return {v.name: v for v in block.variables}


_RANGE_ACCESSOR_SUFFIXES: dict[str, tuple[str, ...]] = {
    "date_range":   ("from", "to"),
    "number_range": ("min", "max"),
}


def _split_range_accessor(
    name: str, var_index: dict[str, Variable],
) -> tuple[str, str] | None:
    """If ``name`` looks like ``<range_var>_<accessor>``, return ``(base, accessor)``.

    Otherwise return None. Used by the binder to accept ``:period_from`` /
    ``:amount_max`` even though ``period`` / ``amount`` are the declared
    range variables — the SQL references the accessor, not the parent.
    """
    for var in var_index.values():
        for suffix in _RANGE_ACCESSOR_SUFFIXES.get(var.type, ()):
            if name == f"{var.name}_{suffix}":
                return var.name, suffix
    return None


def expand_binds(
    block: Block,
    resolved: dict[str, Any],
) -> BoundQuery:
    """Rewrite ``block.query`` and produce the bind dict.

    Args:
        block: The validated block.
        resolved: Output of :func:`resolve_variables`.

    Returns:
        BoundQuery with the rewritten SQL and the bind dict.

    Raises:
        ValueError: if a bind variable in the query is not declared on the
            block, or if a required-yet-unresolved variable is referenced.

    Notes:
        - ``enum_multi`` placeholders are replaced positionally (suffix ``_0``,
          ``_1``, ...). An empty list is invalid and raised here — the
          resolver should have caught it first.
        - ``date_range`` is **not** referenced by raw ``:name`` in the SQL —
          users bind two separate ``date`` variables (e.g. ``:as_of_from``
          and ``:as_of_to``). The dashboard layer can derive both from a
          single ``date_range`` filter via accessors (Phase 6.5.c).
    """
    var_index = _index_variables(block)
    params: dict[str, Any] = {}

    # Track which list-typed variables we've already expanded, so the same
    # placeholder referenced twice in the same query rewrites consistently.
    expansion_cache: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in var_index:
            # Accessor for a date_range / number_range variable?
            accessor = _split_range_accessor(name, var_index)
            if accessor is not None:
                base, suffix = accessor
                value = resolved.get(base, {})
                if not isinstance(value, dict) or suffix not in value:
                    raise ValueError(
                        f"range accessor :{name} but parent variable :{base} "
                        f"did not resolve to a {suffix} value."
                    )
                params[name] = value[suffix]
                return f":{name}"
            raise ValueError(
                f"SQL references undeclared bind variable :{name}. "
                "Run validate_sql() before expand_binds()."
            )
        if name not in resolved:
            raise ValueError(
                f"variable :{name} has no resolved value; "
                "resolve_variables() must run first."
            )

        var = var_index[name]
        value = resolved[name]

        if var.type == "enum_multi":
            if name in expansion_cache:
                return expansion_cache[name]
            if not isinstance(value, list):
                raise ValueError(
                    f"variable :{name} (enum_multi) has invalid resolved value: {value!r}"
                )
            if not value:
                # Empty selection — caller short-circuits to empty result.
                raise EmptySelectionError(name)
            placeholder_names = []
            for i, item in enumerate(value):
                pname = f"{name}_{i}"
                params[pname] = item
                placeholder_names.append(f":{pname}")
            replacement = ", ".join(placeholder_names)
            expansion_cache[name] = replacement
            return replacement

        if var.type == "enum_single":
            params[name] = value
            return f":{name}"

        if var.type == "date":
            if not isinstance(value, date):
                raise ValueError(
                    f"variable :{name} (date) must resolve to a date object; "
                    f"got {type(value).__name__}"
                )
            params[name] = value
            return f":{name}"

        if var.type == "number_range":
            # Not directly bindable by single name; SQL must reference the
            # ``_min`` / ``_max`` accessors (e.g. :amount_min, :amount_max).
            # If the user wrote a bare ``:amount``, surface a clear error.
            raise ValueError(
                f"number_range variable :{name} cannot be referenced directly; "
                f"use :{name}_min and :{name}_max in the SQL."
            )

        if var.type == "date_range":
            raise ValueError(
                f"date_range variable :{name} cannot be referenced directly; "
                f"declare two date variables (e.g. :{name}_from, :{name}_to) "
                "and bind them to the range's 'from'/'to' accessors."
            )

        raise ValueError(f"unsupported variable type for :{name}: {var.type!r}")

    rewritten = _BIND_RE.sub(_replace, block.query)
    return BoundQuery(sql=rewritten, params=params)

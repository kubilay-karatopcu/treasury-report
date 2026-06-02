"""Variable resolver — Phase 6.5.

Resolves each block variable to a concrete Python value using:
1. Dashboard-level variable bindings (constant or from_filter) — when running
   inside a dashboard. Phase 6.5.a does not wire dashboards yet, so the
   ``binding_resolver`` argument is the seam left for 6.5.c.
2. The block's own ``default`` expression, parsed by :func:`parse_date_expr`.

Output types:
- ``date``         → :class:`datetime.date`
- ``date_range``   → ``{"from": date, "to": date}`` (both ``date`` objects)
- ``enum_single``  → the scalar literal (e.g. ``"TRY"``, ``3``)
- ``enum_multi``   → ``list`` of literals (preserves order)
- ``number_range`` → ``{"min": float, "max": float}``

Validation failures abort resolution and raise :class:`ResolutionError`,
which carries a structured ``errors`` list for surfacing in the UI.

Relative-date grammar (spec §3.3):
    today
    today - <N>d   today - <N>w   today - <N>m   today - <N>y
    start_of_month   start_of_year   start_of_quarter
    <ISO date literal>           e.g. 2026-01-01
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

from presentations.blocks.schema import Block, Variable


# ── Errors ────────────────────────────────────────────────────────────────

class ResolutionError(ValueError):
    """One or more variables failed to resolve."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


# ── Relative-date parser ──────────────────────────────────────────────────

_REL_RE = re.compile(
    r"""
    ^\s*
    today
    (?:\s*-\s*(?P<n>\d+)\s*(?P<unit>[dwmy])?)?   # unit optional → defaults to days
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)
_ISO_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_ANCHOR_RE = re.compile(r"^\s*start_of_(month|year|quarter)\s*$", re.IGNORECASE)


def _shift_months(d: date, months: int) -> date:
    """Shift ``d`` by an integer number of months, clamping the day-of-month."""
    total = d.month - 1 + months
    new_year = d.year + total // 12
    new_month = total % 12 + 1
    # Clamp day to last day of new month.
    import calendar
    last_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day)
    return date(new_year, new_month, new_day)


def _start_of_quarter(d: date) -> date:
    q_start_month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, q_start_month, 1)


def parse_date_expr(expr: Any, today: date | None = None) -> date:
    """Parse a date expression to a :class:`datetime.date`.

    Supports the spec §3.3 grammar:
        today, today - Nd / Nw / Nm / Ny, start_of_month/year/quarter, ISO.

    Args:
        expr: The expression. ``date`` / ``datetime`` objects pass through.
        today: Override "today" for deterministic tests.

    Raises:
        ValueError: on any unparseable input.
    """
    if today is None:
        today = date.today()

    # Already a date / datetime — accept and normalize.
    if isinstance(expr, datetime):
        return expr.date()
    if isinstance(expr, date):
        return expr

    if not isinstance(expr, str):
        raise ValueError(f"date expression must be a string; got {type(expr).__name__}: {expr!r}")

    s = expr.strip()
    if not s:
        raise ValueError("date expression is empty")

    # Try ISO literal first — unambiguous shape.
    m = _ISO_RE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except ValueError as exc:
            raise ValueError(f"invalid ISO date {expr!r}: {exc}") from exc

    # today / today - Nu
    m = _REL_RE.match(s)
    if m:
        if m.group("n") is None:
            return today
        n = int(m.group("n"))
        unit = (m.group("unit") or "d").lower()   # bare "today - 7" → 7 days
        if unit == "d":
            return today - timedelta(days=n)
        if unit == "w":
            return today - timedelta(weeks=n)
        if unit == "m":
            return _shift_months(today, -n)
        if unit == "y":
            try:
                return today.replace(year=today.year - n)
            except ValueError:
                # Feb 29 fallback — bump to Feb 28 in target year.
                return today.replace(year=today.year - n, day=28)
        raise ValueError(f"unsupported unit in date expression: {unit!r}")

    # start_of_*
    m = _ANCHOR_RE.match(s)
    if m:
        anchor = m.group(1).lower()
        if anchor == "month":
            return today.replace(day=1)
        if anchor == "year":
            return today.replace(month=1, day=1)
        if anchor == "quarter":
            return _start_of_quarter(today)

    raise ValueError(
        f"unrecognised date expression {expr!r}. Allowed: today, today - Nd/Nw/Nm/Ny, "
        "start_of_month / start_of_year / start_of_quarter, ISO date (YYYY-MM-DD)."
    )


# ── Binding callback signature ────────────────────────────────────────────

@dataclass
class BindingValue:
    """Result returned by ``binding_resolver``: either a constant expression
    that needs to be parsed, or a fully resolved Python value.

    Phase 6.5.c will wire dashboard filters through this. The resolver itself
    stays type-aware and ignorant of where values come from.
    """

    value: Any
    is_expression: bool = False  # True → run through parse_date_expr first.


BindingResolver = Callable[[Variable], BindingValue | None]


# ── Per-type resolution helpers ───────────────────────────────────────────

def _resolve_date(var: Variable, raw: Any, today: date | None) -> date:
    return parse_date_expr(raw, today=today)


def _resolve_date_range(
    var: Variable, raw: Any, today: date | None
) -> dict[str, date]:
    if not isinstance(raw, dict) or "from" not in raw or "to" not in raw:
        raise ResolutionError([
            f"variable {var.name!r}: date_range value must be "
            "{'from': <expr>, 'to': <expr>}"
        ])
    return {
        "from": parse_date_expr(raw["from"], today=today),
        "to":   parse_date_expr(raw["to"],   today=today),
    }


def _resolve_enum_single(var: Variable, raw: Any) -> Any:
    if var.allowed_values is None:
        raise ResolutionError([
            f"variable {var.name!r}: enum_single missing allowed_values"
        ])
    if raw not in var.allowed_values:
        raise ResolutionError([
            f"variable {var.name!r}: value {raw!r} not in allowed_values "
            f"{var.allowed_values!r}"
        ])
    return raw


def _resolve_enum_multi(var: Variable, raw: Any) -> list[Any]:
    if var.allowed_values is None:
        raise ResolutionError([
            f"variable {var.name!r}: enum_multi missing allowed_values"
        ])
    if not isinstance(raw, list):
        raise ResolutionError([
            f"variable {var.name!r}: enum_multi value must be a list (got {type(raw).__name__})"
        ])
    allowed = set(var.allowed_values)
    bad = [v for v in raw if v not in allowed]
    if bad:
        raise ResolutionError([
            f"variable {var.name!r}: values {bad!r} not in allowed_values "
            f"{var.allowed_values!r}"
        ])
    # Empty list is allowed at resolution time: it means "user selected
    # nothing" — the binder propagates this as EmptySelectionError so the
    # application layer can short-circuit the query and render an empty chart
    # instead of crashing on `IN ()`.
    return list(raw)


def _resolve_number_range(var: Variable, raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict) or "min" not in raw or "max" not in raw:
        raise ResolutionError([
            f"variable {var.name!r}: number_range value must be "
            "{'min': <num>, 'max': <num>}"
        ])
    a, b = raw["min"], raw["max"]
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)):
        raise ResolutionError([
            f"variable {var.name!r}: number_range min/max must be numeric"
        ])
    if a > b:
        raise ResolutionError([
            f"variable {var.name!r}: number_range min ({a}) > max ({b})"
        ])
    return {"min": float(a), "max": float(b)}


# ── Main entry point ──────────────────────────────────────────────────────

def resolve_variables(
    block: Block,
    overrides: dict[str, Any] | None = None,
    *,
    binding_resolver: BindingResolver | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Resolve every variable on ``block`` to a concrete Python value.

    Resolution order per variable:
    1. If ``overrides`` carries the variable name, use that value (validated
       per type; date/date_range values may be raw strings, in which case
       they are run through :func:`parse_date_expr`).
    2. Else if ``binding_resolver`` returns a non-None :class:`BindingValue`,
       use that.
    3. Else use ``var.default`` (must be present if ``var.required`` is
       True).

    Raises:
        ResolutionError: with a list of per-variable error messages.
    """
    overrides = overrides or {}
    resolved: dict[str, Any] = {}
    errors: list[str] = []

    for var in block.variables:
        try:
            raw, is_expression = _select_raw(var, overrides, binding_resolver)
        except ResolutionError as exc:
            errors.extend(exc.errors)
            continue

        if raw is None:
            if var.required:
                errors.append(
                    f"variable {var.name!r}: required, no value supplied and no default set"
                )
            else:
                resolved[var.name] = None
            continue

        try:
            value = _coerce(var, raw, is_expression, today=today)
        except ResolutionError as exc:
            errors.extend(exc.errors)
            continue
        except ValueError as exc:
            errors.append(f"variable {var.name!r}: {exc}")
            continue

        resolved[var.name] = value

    if errors:
        raise ResolutionError(errors)

    return resolved


def _select_raw(
    var: Variable,
    overrides: dict[str, Any],
    binding_resolver: BindingResolver | None,
) -> tuple[Any, bool]:
    """Return (raw_value, is_expression_string) for a variable.

    - Overrides win first; treat their string values as expressions for
      date types (so the UI / API can pass "today - 7d" verbatim).
    - Otherwise consult the dashboard binding resolver.
    - Falls back to the block default; defaults are expressions for date
      types and literals otherwise.
    """
    if var.name in overrides:
        raw = overrides[var.name]
        is_expr = var.type in ("date", "date_range") and isinstance(raw, str)
        return raw, is_expr

    if binding_resolver is not None:
        binding = binding_resolver(var)
        if binding is not None:
            return binding.value, binding.is_expression

    if var.default is not None:
        return var.default, True  # defaults always run through the parser for dates.

    # Phase 6.5.c UX: enum types without an explicit default fall back to
    # ``allowed_values`` (multi → all, single → first). Most blocks want
    # "select everything by default" — making the user repeat that list as a
    # default value is busywork. Power users can still override by setting
    # default explicitly.
    if var.type == "enum_multi" and var.allowed_values:
        return list(var.allowed_values), False
    if var.type == "enum_single" and var.allowed_values:
        return var.allowed_values[0], False

    return None, False


def _coerce(var: Variable, raw: Any, is_expression: bool, today: date | None) -> Any:
    """Per-type coercion to the canonical Python representation."""
    t = var.type
    if t == "date":
        return _resolve_date(var, raw, today)
    if t == "date_range":
        return _resolve_date_range(var, raw, today)
    if t == "enum_single":
        return _resolve_enum_single(var, raw)
    if t == "enum_multi":
        return _resolve_enum_multi(var, raw)
    if t == "number_range":
        return _resolve_number_range(var, raw)
    raise ResolutionError([f"variable {var.name!r}: unsupported type {t!r}"])


# ── Helpers used by store / binder ────────────────────────────────────────

def normalize_for_cache_key(resolved: dict[str, Any]) -> dict[str, Any]:
    """Normalise a resolved-vars dict for stable hashing (spec §4.3).

    - Dates → ISO strings.
    - enum_multi lists → sorted copies.
    - Numeric ranges / date ranges → dicts with sorted keys.
    """
    out: dict[str, Any] = {}
    for k, v in resolved.items():
        out[k] = _normalize_one(v)
    return out


def _normalize_one(v: Any) -> Any:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, list):
        # Hashing requires deterministic order; we preserve original-list
        # values, only sort for the cache key.
        try:
            return sorted(v)
        except TypeError:
            return list(v)
    if isinstance(v, dict):
        return {k: _normalize_one(vv) for k, vv in sorted(v.items())}
    return v

"""SQL whitelist validator — Phase 6.5.

Implements spec §4.1 rules:

1. The query parses with sqlparse.
2. The top-level statement is ``SELECT`` or ``WITH`` (CTE chained into a
   single ``SELECT``).
3. Banned DDL keywords (``CREATE``, ``DROP``, ``ALTER``, ``TRUNCATE``,
   ``RENAME``, ``GRANT``, ``REVOKE``, ``COMMENT``) reject the query.
4. Banned DML write keywords (``INSERT``, ``UPDATE``, ``DELETE``, ``MERGE``,
   ``UPSERT``) reject the query.
5. Procedural blocks (``BEGIN``, ``DECLARE``, ``EXECUTE IMMEDIATE``,
   ``CALL``) reject the query.
6. Multiple semicolon-separated statements reject.
7. Every ``:bind_var`` reference must be a declared block variable.
8. Declared variables not referenced in the query produce a *warning* (not an
   error) — the block still saves.

The validator is purely defensive: it does **not** execute the SQL and does
not need a database connection. Identifier classification uses sqlparse plus
a regex sweep for words that sqlparse fails to tokenise (notably the
``WITH``-followed CTE definition, where sqlparse 0.5 tags the outer keyword
as ``CTE`` rather than ``DML``).

Returns :class:`ValidationResult` with structured ``errors`` / ``warnings``
so the editor UI can surface them inline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import sqlparse
from sqlparse import tokens as T


# ── ValidationResult ──────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Outcome of a single SQL+variables validation pass."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(
            ok=self.ok and other.ok,
            errors=[*self.errors, *other.errors],
            warnings=[*self.warnings, *other.warnings],
        )

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ── Forbidden keyword sets ────────────────────────────────────────────────

# Spec §4.1: rules 3, 4, 5.
_DDL_KEYWORDS = frozenset({
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "GRANT", "REVOKE", "COMMENT",
})
_DML_WRITE_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT",
})
_PROCEDURAL_KEYWORDS = frozenset({
    "BEGIN", "DECLARE", "CALL",
})
# Two-word procedural pattern handled by regex sweep below.
_PROCEDURAL_PHRASES = (re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE),)

FORBIDDEN_SINGLE = _DDL_KEYWORDS | _DML_WRITE_KEYWORDS | _PROCEDURAL_KEYWORDS

# Cheap pre-screen by raw text — catches obvious cases before parsing and is
# the source of the "anywhere in the query" check called for by spec.
_FORBIDDEN_RE = {
    kw: re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in FORBIDDEN_SINGLE
}


# ── Bind variable extraction ──────────────────────────────────────────────

# Match :ident, but not Postgres-style ::cast or "x :: y".
# Skip when preceded by another ':' (PG ::cast), and skip inside string literals
# (handled separately by stripping literals first).
_BIND_VAR_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)")

# Strip single-quoted strings (Oracle escapes via doubled '').
_STRING_LIT_RE = re.compile(r"'(?:[^']|'')*'")
# Strip block + line comments before bind extraction.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_noise(sql: str) -> str:
    """Remove comments and string literals so they don't poison keyword/bind
    detection. Replaces with single space to preserve token boundaries."""
    s = _BLOCK_COMMENT_RE.sub(" ", sql)
    s = _LINE_COMMENT_RE.sub(" ", s)
    s = _STRING_LIT_RE.sub("''", s)
    return s


def extract_bind_vars(sql: str) -> list[str]:
    """Return the ordered list of distinct ``:name`` placeholders in *sql*.

    Skips placeholders inside string literals and comments. Order matches
    first-occurrence in the source.
    """
    cleaned = _strip_noise(sql)
    seen: list[str] = []
    seen_set: set[str] = set()
    for m in _BIND_VAR_RE.finditer(cleaned):
        name = m.group(1)
        if name not in seen_set:
            seen_set.add(name)
            seen.append(name)
    return seen


# ── Top-level statement classifier ────────────────────────────────────────

def _top_level_keyword(stmt: sqlparse.sql.Statement) -> str | None:
    """Return the uppercased first DML/keyword token of a parsed statement.

    Walks past whitespace, comments, and parentheses. ``WITH`` (CTE) and
    ``SELECT`` are the only acceptable values for Phase 6.5.
    """
    for tok in stmt.flatten():
        if tok.ttype in (T.Whitespace, T.Newline, T.Comment, T.Comment.Single,
                         T.Comment.Multiline):
            continue
        if tok.ttype in (T.Punctuation,):
            # Allow leading parens, e.g. "(SELECT ...)" — keep scanning.
            if tok.value.strip() in ("(",):
                continue
            return None
        if tok.ttype is None:
            continue
        v = tok.value.strip().upper()
        if not v:
            continue
        return v
    return None


# ── Main entry points ─────────────────────────────────────────────────────

def _split_top_level_statements(sql: str) -> list[sqlparse.sql.Statement]:
    """Split into non-empty statements at top-level ``;``."""
    parsed = sqlparse.parse(sql)
    return [s for s in parsed if str(s).strip()]


def validate_sql(
    sql: str,
    declared_variables: Iterable[str] | None = None,
    *,
    range_variables: Iterable[str] | None = None,
) -> ValidationResult:
    """Validate ``sql`` against the Phase 6.5 whitelist.

    Args:
        sql: The raw block query string.
        declared_variables: Names of the variables declared on the block. If
            None, bind-variable cross-checks are skipped (used by lower-level
            callers that only care about the keyword whitelist).
        range_variables: Optional set of declared ``date_range`` /
            ``number_range`` variable names. References to ``<name>_from`` /
            ``<name>_to`` / ``<name>_min`` / ``<name>_max`` are accepted as
            implicit accessors and do not count as undeclared.

    Returns:
        ValidationResult with structured errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(sql, str) or not sql.strip():
        return ValidationResult(ok=False, errors=["SQL is empty"])

    # ── Rule 1: parseable ─────────────────────────────────────────────
    try:
        statements = _split_top_level_statements(sql)
    except Exception as exc:  # sqlparse rarely raises, but be defensive.
        return ValidationResult(ok=False, errors=[f"SQL parse error: {exc}"])

    if not statements:
        return ValidationResult(ok=False, errors=["SQL is empty after parse"])

    # ── Rule 6: single statement ──────────────────────────────────────
    # A trailing semicolon yields one effective statement; multiple
    # non-empty statements is a rejection.
    non_empty = statements
    if len(non_empty) > 1:
        errors.append(
            f"Multiple statements detected ({len(non_empty)}). "
            "Block queries must be a single SELECT or WITH statement."
        )
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    stmt = non_empty[0]

    # ── Rule 2: top-level SELECT or WITH ──────────────────────────────
    top = _top_level_keyword(stmt)
    if top not in ("SELECT", "WITH"):
        errors.append(
            f"Top-level statement must be SELECT or WITH (got {top!r}). "
            "Other statement types are not allowed."
        )
        # Continue — surfacing forbidden-keyword errors too is useful for UX.

    # ── Rules 3, 4, 5: forbidden keywords anywhere ────────────────────
    cleaned = _strip_noise(sql)

    for kw in sorted(FORBIDDEN_SINGLE):
        if _FORBIDDEN_RE[kw].search(cleaned):
            kind = _classify_kw(kw)
            errors.append(
                f"Forbidden {kind} keyword detected: {kw!r}. "
                "Only SELECT and WITH are allowed."
            )

    for pat in _PROCEDURAL_PHRASES:
        m = pat.search(cleaned)
        if m:
            errors.append(
                f"Forbidden procedural keyword detected: {m.group(0)!r}."
            )

    # ── Rule 7 & 8: bind vs declared variables ────────────────────────
    if declared_variables is not None:
        declared = set(declared_variables)
        range_set = set(range_variables or ())
        binds = extract_bind_vars(sql)
        bind_set = set(binds)

        undeclared: list[str] = []
        # An accessor of a range variable counts as a reference to its parent.
        referenced_via_accessor: set[str] = set()
        for b in binds:
            if b in declared:
                continue
            parent = _match_range_accessor(b, range_set)
            if parent is not None:
                referenced_via_accessor.add(parent)
                continue
            undeclared.append(b)

        if undeclared:
            errors.append(
                f"SQL references undeclared bind variables: {undeclared!r}. "
                "Every :placeholder must be declared in block.variables."
            )

        effective_refs = bind_set | referenced_via_accessor
        unused = sorted(declared - effective_refs)
        for name in unused:
            warnings.append(
                f"Declared variable {name!r} is not referenced in the query."
            )

    ok = not errors
    return ValidationResult(ok=ok, errors=errors, warnings=warnings)


def _match_range_accessor(name: str, range_vars: set[str]) -> str | None:
    """Return the parent range variable if ``name`` is one of its accessors."""
    for parent in range_vars:
        for suffix in ("_from", "_to", "_min", "_max"):
            if name == f"{parent}{suffix}":
                return parent
    return None


def _classify_kw(kw: str) -> str:
    if kw in _DDL_KEYWORDS:
        return "DDL"
    if kw in _DML_WRITE_KEYWORDS:
        return "DML write"
    if kw in _PROCEDURAL_KEYWORDS:
        return "procedural"
    return "forbidden"

"""Tests for presentations.sql.binder — bind expansion and integration."""
from __future__ import annotations

import re
from datetime import date

import pytest

from presentations.blocks.schema import Block, load_block_from_dict
from presentations.sql.binder import expand_binds, BoundQuery
from presentations.variables.resolver import resolve_variables


def _whitespace_normalise(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _extract_expected_select(text: str) -> str:
    """Pull the SELECT block out of expected_resolved_query.sql, ignoring
    surrounding comments. Strips the trailing ';'."""
    lines: list[str] = []
    in_select = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_select:
            if stripped.upper().startswith("SELECT"):
                in_select = True
        if in_select:
            if stripped.startswith("--"):
                # comment after the query — stop here.
                if stripped.upper().startswith("-- EXPECTED"):
                    break
                continue
            lines.append(line)
    sql = "\n".join(lines).strip().rstrip(";")
    return sql


def test_expand_binds_matches_fixture(sample_block_dict, fixed_today, expected_resolved_sql):
    """Acceptance: rewritten SQL matches examples/phase_6_5/expected_resolved_query.sql."""
    block = load_block_from_dict(sample_block_dict)
    resolved = resolve_variables(block, today=fixed_today)
    bound = expand_binds(block, resolved)

    expected = _extract_expected_select(expected_resolved_sql)
    assert _whitespace_normalise(bound.sql) == _whitespace_normalise(expected)


def test_expand_binds_param_dict_shape(sample_block_dict, fixed_today):
    block = load_block_from_dict(sample_block_dict)
    resolved = resolve_variables(block, today=fixed_today)
    bound = expand_binds(block, resolved)

    # Date params are real date objects (per spec §4.2).
    assert isinstance(bound.params["as_of_from"], date)
    assert isinstance(bound.params["as_of_to"], date)
    assert bound.params["as_of_from"] == date(2026, 4, 21)
    assert bound.params["as_of_to"] == date(2026, 5, 21)

    # enum_multi expanded with positional suffix.
    assert bound.params["currency_list_0"] == "TRY"
    assert bound.params["currency_list_1"] == "USD"
    assert bound.params["currency_list_2"] == "EUR"
    assert "currency_list" not in bound.params  # original key removed.

    assert bound.params["maturity_list_0"] == "1M"
    assert bound.params["maturity_list_1"] == "3M"
    assert bound.params["maturity_list_2"] == "6M"


def test_expand_binds_repeated_placeholder():
    """If a query references the same enum_multi placeholder twice (e.g.
    in two different WHERE clauses), both rewrites must produce the same
    positional list — not double-suffix it."""
    block = Block(
        id="dup_block", version=1, title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query=(
            "SELECT * FROM t WHERE a IN (:vals) UNION ALL "
            "SELECT * FROM s WHERE b IN (:vals)"
        ),
        visualization={"type": "kpi", "config": {}},
        variables=[{
            "name": "vals", "semantic_tag": "currency", "type": "enum_multi",
            "required": True, "allowed_values": ["A", "B", "C"], "default": ["A", "B"],
        }],
    )
    resolved = resolve_variables(block, today=date(2026, 5, 21))
    bound = expand_binds(block, resolved)
    occurrences = bound.sql.count(":vals_0")
    assert occurrences == 2, f"expected both INs to reference :vals_0; got\n{bound.sql}"


def test_expand_binds_rejects_undeclared(sample_block_dict, fixed_today):
    """The binder cross-checks references at expand time; an unresolved name
    becomes a clear ValueError (validator should catch it earlier)."""
    block = load_block_from_dict(sample_block_dict)
    block = block.model_copy(update={
        "query": block.query + "\n-- bogus: :unknown_var",
    })
    resolved = resolve_variables(block, today=fixed_today)
    # Comment-stripping in the binder means :unknown_var inside a -- line is
    # ignored. Use a real reference instead.
    block = block.model_copy(update={
        "query": "SELECT 1 FROM dual WHERE x = :unknown_var",
    })
    with pytest.raises(ValueError):
        expand_binds(block, resolved)


def test_expand_binds_date_must_be_date_object():
    block = Block(
        id="block_a", version=1, title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query="SELECT * FROM t WHERE d = :d_var",
        visualization={"type": "kpi", "config": {}},
        variables=[{
            "name": "d_var", "semantic_tag": "as_of_time", "type": "date",
            "required": True, "default": "today",
        }],
    )
    # Pass an explicit string instead of going through the resolver.
    with pytest.raises(ValueError):
        expand_binds(block, {"d_var": "2026-01-01"})


def test_expand_binds_empty_enum_multi_raises_empty_selection():
    """Phase 6.5.c: an empty enum_multi raises EmptySelectionError so the
    caller can short-circuit to an empty result instead of building an
    invalid `IN ()` clause."""
    from presentations.sql.binder import EmptySelectionError

    block = Block(
        id="block_a", version=1, title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query="SELECT * FROM t WHERE c IN (:c_var)",
        visualization={"type": "kpi", "config": {}},
        variables=[{
            "name": "c_var", "semantic_tag": "currency", "type": "enum_multi",
            "required": False, "allowed_values": ["A", "B"],
        }],
    )
    with pytest.raises(EmptySelectionError) as exc:
        expand_binds(block, {"c_var": []})
    assert exc.value.variable_name == "c_var"
    # EmptySelectionError IS-A ValueError for backwards compat with broad
    # exception handlers.
    assert isinstance(exc.value, ValueError)


def test_date_range_via_accessors():
    """date_range variable bound through ``_from`` / ``_to`` accessor names."""
    block = Block(
        id="block_a", version=1, title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query="SELECT * FROM t WHERE d BETWEEN :rng_from AND :rng_to",
        visualization={"type": "kpi", "config": {}},
        variables=[{
            "name": "rng", "semantic_tag": "as_of_time", "type": "date_range",
            "required": True,
            "default": {"from": "today - 7d", "to": "today"},
        }],
    )
    resolved = resolve_variables(block, today=date(2026, 5, 21))
    bound = expand_binds(block, resolved)
    assert bound.params["rng_from"] == date(2026, 5, 14)
    assert bound.params["rng_to"] == date(2026, 5, 21)


def test_expand_binds_leaves_literals_and_comments_untouched():
    """Bug #9: a ``:name`` inside a string literal or a comment must NOT be
    treated as a bind. Only references in code expand; the executed SQL then
    matches what validate_sql sees after stripping noise."""
    block = Block(
        id="blk_bug9", version=1, title="x", team="treasury", owner="x",
        created_at="2026-05-21T10:00:00Z",
        query=(
            "SELECT 'filter: :currencies' AS label,  -- note :currencies\n"
            "       /* keep :currencies */ x "
            "FROM t WHERE ccy IN (:currencies)"
        ),
        visualization={"type": "kpi", "config": {}},
        variables=[{
            "name": "currencies", "semantic_tag": "currency", "type": "enum_multi",
            "required": True, "allowed_values": ["TRY", "USD", "EUR"],
            "default": ["TRY", "USD"],
        }],
    )
    resolved = resolve_variables(block, today=date(2026, 5, 21))
    bound = expand_binds(block, resolved)

    # String literal preserved verbatim — NOT expanded.
    assert "'filter: :currencies'" in bound.sql
    # Line comment and block comment preserved verbatim.
    assert "-- note :currencies" in bound.sql
    assert "/* keep :currencies */" in bound.sql
    # Only the real WHERE reference expands positionally.
    assert "IN (:currencies_0, :currencies_1)" in bound.sql
    assert bound.params == {"currencies_0": "TRY", "currencies_1": "USD"}

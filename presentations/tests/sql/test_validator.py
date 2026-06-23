"""Tests for presentations.sql.validator — one per spec §4.1 rule."""
from __future__ import annotations

import re

import pytest

from presentations.sql.validator import extract_bind_vars, validate_sql


# ── Rule 1: parseable ─────────────────────────────────────────────────────

def test_empty_sql_rejected():
    r = validate_sql("")
    assert not r.ok
    assert any("empty" in e.lower() for e in r.errors)


def test_whitespace_only_rejected():
    r = validate_sql("   \n\t   ")
    assert not r.ok


# ── Rule 2: SELECT / WITH only ───────────────────────────────────────────

def test_simple_select_accepted():
    r = validate_sql("SELECT 1 AS x FROM dual", declared_variables=set())
    assert r.ok, r.errors


def test_with_cte_accepted():
    sql = "WITH t AS (SELECT 1 AS x) SELECT * FROM t"
    r = validate_sql(sql, declared_variables=set())
    assert r.ok, r.errors


def test_explain_plan_rejected():
    r = validate_sql("EXPLAIN PLAN FOR SELECT 1")
    assert not r.ok
    assert any("SELECT or WITH" in e for e in r.errors)


# ── Rule 3: DDL banned ───────────────────────────────────────────────────

@pytest.mark.parametrize("kw", ["CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "GRANT", "REVOKE", "COMMENT"])
def test_ddl_keyword_rejected(kw):
    r = validate_sql(f"{kw} TABLE foo")
    assert not r.ok
    assert any(kw in e for e in r.errors)


def test_ddl_inside_string_literal_is_safe():
    # 'DROP' is inside a string literal; should not trigger.
    sql = "SELECT 'DROP TABLE x' AS msg FROM dual"
    r = validate_sql(sql, declared_variables=set())
    assert r.ok, r.errors


def test_ddl_inside_comment_is_safe():
    sql = "SELECT 1 FROM dual  -- DROP TABLE foo"
    r = validate_sql(sql, declared_variables=set())
    assert r.ok, r.errors


# ── Rule 4: DML write banned ─────────────────────────────────────────────

@pytest.mark.parametrize("kw", ["INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT"])
def test_dml_write_keyword_rejected(kw):
    r = validate_sql(f"{kw} INTO foo VALUES (1)")
    assert not r.ok


def test_select_with_update_substring_in_alias_safe():
    # 'UPDATED_AT' contains UPDATE as substring but should not match.
    sql = "SELECT UPDATED_AT FROM foo"
    r = validate_sql(sql, declared_variables=set())
    assert r.ok, r.errors


# ── Rule 5: procedural banned ────────────────────────────────────────────

@pytest.mark.parametrize("snippet", [
    "BEGIN SELECT 1 INTO x FROM dual; END;",
    "DECLARE x NUMBER; BEGIN x := 1; END;",
    "CALL foo()",
])
def test_procedural_rejected(snippet):
    r = validate_sql(snippet)
    assert not r.ok


def test_execute_immediate_rejected():
    r = validate_sql("BEGIN EXECUTE IMMEDIATE 'SELECT 1'; END;")
    assert not r.ok
    assert any("EXECUTE IMMEDIATE" in e.upper() for e in r.errors)


# ── Rule 6: multi-statement ──────────────────────────────────────────────

def test_two_statements_rejected():
    r = validate_sql("SELECT 1; SELECT 2")
    assert not r.ok
    assert any("Multiple statements" in e for e in r.errors)


def test_trailing_semicolon_allowed():
    r = validate_sql("SELECT 1 FROM dual;", declared_variables=set())
    assert r.ok, r.errors


# ── Rule 7: undeclared binds ─────────────────────────────────────────────

def test_undeclared_bind_rejected():
    r = validate_sql(
        "SELECT * FROM t WHERE x = :foo",
        declared_variables={"bar"},
    )
    assert not r.ok
    assert any("foo" in e for e in r.errors)


def test_multiple_binds_partial_undeclared():
    r = validate_sql(
        "SELECT * FROM t WHERE x = :foo AND y = :bar",
        declared_variables={"foo"},
    )
    assert not r.ok
    assert any("bar" in e for e in r.errors)


# ── Rule 8: unused declared → warning ────────────────────────────────────

def test_unused_declared_is_warning():
    r = validate_sql("SELECT 1 FROM dual", declared_variables={"foo"})
    assert r.ok
    assert any("foo" in w for w in r.warnings)


# ── Bind extraction edge cases ───────────────────────────────────────────

def test_extract_binds_order_preserved():
    sql = "SELECT * FROM t WHERE a = :foo AND b = :bar AND c = :foo"
    assert extract_bind_vars(sql) == ["foo", "bar"]


def test_extract_binds_ignores_postgres_cast():
    # Postgres '::' cast should not be matched.
    sql = "SELECT '2026-01-01'::date AS d FROM dual"
    assert extract_bind_vars(sql) == []


def test_extract_binds_ignores_inside_strings():
    sql = "SELECT 'foo :bar baz' AS s FROM dual"
    assert extract_bind_vars(sql) == []


# ── Regression: keyword-named identifiers must not be false-rejected ──────

@pytest.mark.parametrize("sql", [
    "SELECT comment FROM tickets",
    'SELECT "DELETE" FROM t',
    "SELECT BEGIN FROM t",
])
def test_keyword_named_identifier_not_rejected(sql):
    # A column or quoted identifier named like a banned keyword is legitimate
    # — only a *statement-leading* or real DML/DDL keyword should reject.
    r = validate_sql(sql, declared_variables=set())
    assert r.ok, r.errors


@pytest.mark.parametrize("sql", [
    "DROP TABLE x",
    "INSERT INTO foo VALUES(1)",
    "DELETE FROM t",
    "SELECT 1; DROP TABLE x",
])
def test_leading_or_multi_statement_banned_still_rejected(sql):
    assert not validate_sql(sql, declared_variables=set()).ok


@pytest.mark.parametrize("sql", [
    "SELECT * FROM (DELETE FROM t)",
    "WITH x AS (DELETE FROM t) SELECT * FROM x",
])
def test_nested_dml_keyword_rejected(sql):
    # Defense-in-depth: a real DML keyword (Keyword.DML) nested in a subquery or
    # CTE must still reject even though the statement leads with SELECT/WITH.
    # sqlparse tags these as Keyword.DML (distinct from a keyword-named column),
    # so leading-token-only would wrongly accept them.
    assert not validate_sql(sql, declared_variables=set()).ok


def test_sub_outside_noise_preserves_literals_and_comments():
    from presentations.sql.validator import sub_outside_noise
    pat = re.compile(r":(\w+)")
    sql = "SELECT ':x' AS a /* :y */ , b -- :z\nFROM t WHERE c = :w"
    out = sub_outside_noise(pat, "BIND", sql)
    assert "':x'" in out          # literal untouched
    assert "/* :y */" in out      # block comment untouched
    assert "-- :z" in out         # line comment untouched
    assert "c = BIND" in out      # only the code bind rewritten

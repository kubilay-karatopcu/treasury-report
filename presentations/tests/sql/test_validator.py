"""Tests for presentations.sql.validator — one per spec §4.1 rule."""
from __future__ import annotations

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

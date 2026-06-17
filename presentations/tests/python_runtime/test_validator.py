"""Faz P — Python transform AST whitelist validator testleri."""
from __future__ import annotations

import pytest

from presentations.python_runtime.validator import (
    ALLOWED_IMPORT_ROOTS,
    OUTPUT_DF_NAME,
    validate_python,
)


def test_minimal_valid_script():
    r = validate_python("output_node_df = input_node_df.head(10)")
    assert r.ok
    assert r.errors == []


def test_allowlisted_imports_pass():
    code = (
        "import pandas as pd\n"
        "import numpy as np\n"
        "from datetime import datetime\n"
        "output_node_df = pd.DataFrame({'x': np.arange(3)})\n"
    )
    r = validate_python(code)
    assert r.ok, r.errors


@pytest.mark.parametrize("mod", ["os", "sys", "subprocess", "socket", "pathlib", "importlib"])
def test_forbidden_imports_rejected(mod):
    r = validate_python(f"import {mod}\noutput_node_df = input_node_df")
    assert not r.ok
    assert any("import yasak" in e for e in r.errors)


def test_star_import_rejected():
    r = validate_python("from pandas import *\noutput_node_df = input_node_df")
    assert not r.ok
    assert any("import *" in e for e in r.errors)


def test_relative_import_rejected():
    r = validate_python("from . import x\noutput_node_df = input_node_df")
    assert not r.ok


@pytest.mark.parametrize(
    "expr",
    [
        "eval('1')",
        "exec('x=1')",
        "compile('1', '<s>', 'eval')",
        "open('/etc/passwd')",
        "__import__('os')",
        "getattr(input_node_df, 'x')",
        "globals()",
        "locals()",
    ],
)
def test_forbidden_names_rejected(expr):
    r = validate_python(f"{expr}\noutput_node_df = input_node_df")
    assert not r.ok
    assert any("yasak isim" in e or "dunder" in e for e in r.errors)


@pytest.mark.parametrize(
    "code",
    [
        "output_node_df = ().__class__.__bases__[0].__subclasses__()",
        "x = input_node_df.__class__\noutput_node_df = input_node_df",
        "y = __builtins__\noutput_node_df = input_node_df",
    ],
)
def test_dunder_escape_rejected(code):
    r = validate_python(code)
    assert not r.ok
    assert any("dunder" in e for e in r.errors)


def test_missing_output_rejected():
    r = validate_python("result = input_node_df.head()")
    assert not r.ok
    assert any(OUTPUT_DF_NAME in e for e in r.errors)


def test_syntax_error_rejected():
    r = validate_python("output_node_df = input_node_df(")
    assert not r.ok
    assert any("dizimi" in e.lower() or "syntax" in e.lower() for e in r.errors)


def test_empty_rejected():
    assert not validate_python("").ok
    assert not validate_python("   \n  ").ok


def test_allowlist_is_frozen_and_excludes_dangerous():
    # Güvenlik regresyonu: tehlikeli modüller asla allowlist'e sızmamalı.
    for danger in ("os", "sys", "subprocess", "socket", "importlib", "ctypes"):
        assert danger not in ALLOWED_IMPORT_ROOTS

"""Faz P — Python transform subprocess sandbox testleri.

Bu testler gerçek bir alt-process başlatır (POSIX). Hızlı senaryolar için düşük
CPU/wall limitleri kullanılır.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from presentations.python_runtime.executor import run_python_transform


@pytest.fixture
def df():
    return pd.DataFrame({"a": [1, 2, 3], "b": [10, 20, 30]})


def test_success_returns_dataframe(df):
    r = run_python_transform(
        "output_node_df = input_node_df.assign(c=input_node_df['a'] + input_node_df['b'])",
        df,
    )
    assert r.ok, r.error
    assert r.df is not None
    assert list(r.df.columns) == ["a", "b", "c"]
    assert r.row_count == 3
    assert r.columns == ["a", "b", "c"]
    assert r.df["c"].tolist() == [11, 22, 33]


def test_input_isolation_only_input_node_df(df):
    # Script yalnız input_node_df + pd/np görür; başka bir node adı NameError verir.
    r = run_python_transform(
        "output_node_df = some_other_node.head()", df
    )
    assert not r.ok
    assert "NameError" in (r.error or "")


def test_non_dataframe_output_rejected(df):
    r = run_python_transform("output_node_df = 42", df)
    assert not r.ok
    assert "DataFrame" in (r.error or "")


def test_missing_output_rejected_by_validator(df):
    # Statik validator bunu yakalar (çalıştırmaya gerek yok).
    r = run_python_transform("zzz = input_node_df.head()", df)
    assert not r.ok


def test_runtime_error_surfaced(df):
    r = run_python_transform(
        "output_node_df = input_node_df.drop(columns=['nope'])", df
    )
    assert not r.ok
    assert "KeyError" in (r.error or "")


def test_pandas_and_numpy_available(df):
    code = (
        "import numpy as np\n"
        "output_node_df = pd.DataFrame({'s': [np.sqrt(input_node_df['a'].sum())]})\n"
    )
    r = run_python_transform(code, df)
    assert r.ok, r.error
    assert r.columns == ["s"]


def test_forbidden_import_blocked(df):
    r = run_python_transform("import os\noutput_node_df = input_node_df", df)
    assert not r.ok
    assert "import yasak" in (r.error or "")


def test_print_captured(df):
    r = run_python_transform(
        "print('hello from script')\noutput_node_df = input_node_df", df
    )
    assert r.ok, r.error
    assert "hello from script" in r.stdout


@pytest.mark.skipif(os.name != "posix", reason="rlimit yalnız POSIX")
def test_cpu_limit_kills_infinite_loop(df):
    r = run_python_transform(
        "while True:\n    pass\noutput_node_df = input_node_df",
        df, cpu_seconds=2, wall_timeout=15,
    )
    assert not r.ok
    assert "CPU" in (r.error or "") or "zaman aşımı" in (r.error or "")


def test_wall_timeout(df):
    # CPU limitini yüksek tut ki duvar-saati timeout'u devreye girsin (uyku
    # CPU yakmaz). datetime allowlist'te; busy-sleep yerine time.sleep yasak
    # olduğundan numpy ile küçük bir meşgul döngü + düşük wall timeout.
    code = (
        "x = 0\n"
        "while x < 10**12:\n"
        "    x += 1\n"
        "output_node_df = input_node_df\n"
    )
    r = run_python_transform(code, df, cpu_seconds=60, wall_timeout=2)
    assert not r.ok
    assert "zaman aşımı" in (r.error or "") or "CPU" in (r.error or "")

"""Faz P — Python transform subprocess sandbox testleri.

Bu testler gerçek bir alt-process başlatır (POSIX). Hızlı senaryolar için düşük
CPU/wall limitleri kullanılır.
"""
from __future__ import annotations

import os

import numpy as np
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


def test_dtype_preservation_no_parquet_engine(df):
    # Transfer JSON Table Schema (orient='table') — parquet engine GEREKTİRMEZ
    # (ofis bug'ı: alt-process'te pyarrow yoktu). int/float/datetime/bool korunur.
    rich = pd.DataFrame({
        "i": [1, 2, 3],
        "f": [1.5, 2.0, 3.25],
        "s": ["a", "b", "c"],
        "d": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
        "b": [True, False, True],
    })
    r = run_python_transform("output_node_df = input_node_df", rich)
    assert r.ok, r.error
    dts = {c: str(t) for c, t in r.df.dtypes.items()}
    assert dts["i"] == "int64"
    assert dts["f"] == "float64"
    assert "datetime64" in dts["d"]
    assert dts["b"] == "bool"
    assert r.df["f"].tolist() == [1.5, 2.0, 3.25]


def test_out_of_bounds_datetime(df):
    # Finansta "max/sonsuz" tarih sentinel'leri (2400, 9999-12-31) ns sınırını
    # (~2262) aşar; ns-bağlı dönüşüm "Out of bounds nanosecond timestamp" verirdi.
    rich = pd.DataFrame({
        "ID": [1, 2, 3],
        "START_DT": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "END_DT": np.array(["2030-01-01", "2400-01-01", "9999-12-31"], dtype="datetime64[us]"),
    })
    r = run_python_transform("output_node_df = input_node_df[input_node_df['ID'] >= 2]", rich)
    assert r.ok, r.error
    assert "datetime64" in str(r.df["END_DT"].dtype)
    assert str(r.df["END_DT"].iloc[1].date()) == "9999-12-31"


def test_groupby_with_index_preserved(df):
    # Anlamlı index (groupby keys) kolona çevrilir → kaybolmaz.
    code = (
        "g = input_node_df.groupby('a', as_index=True)['b'].sum().to_frame()\n"
        "output_node_df = g\n"
    )
    r = run_python_transform(code, df)
    assert r.ok, r.error
    assert "a" in r.df.columns and "b" in r.df.columns


def test_wall_timeout(df):
    # datetime allowlist'te ama time.sleep yasak; CPU limitini yüksek tutup
    # küçük bir meşgul döngü + düşük wall timeout ile duvar-saatini tetikle.
    code = (
        "x = 0\n"
        "while x < 10**12:\n"
        "    x += 1\n"
        "output_node_df = input_node_df\n"
    )
    r = run_python_transform(code, df, cpu_seconds=60, wall_timeout=2)
    assert not r.ok
    assert "zaman aşımı" in (r.error or "") or "CPU" in (r.error or "")

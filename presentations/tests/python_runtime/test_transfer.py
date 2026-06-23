"""Faz P — sandbox veri aktarımı (_transfer) dtype koruma testleri.

Odak: Oracle NUMBER → ``Decimal`` kolonları transfer turunda float64'e çevrilir
(yoksa JSON Table Schema'da Decimal tipi olmadığı için ``str`` dtype'a ezilir →
sandbox'ta ``.round()`` sessiz no-op, ``np.average`` TypeError). Gerçek string
kolonlar (kod/ID, baştaki sıfırlar) string KALIR.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pandas as pd
import pytest

from presentations.python_runtime._transfer import read_table, write_table


def _roundtrip(df):
    fd, path = tempfile.mkstemp(suffix=".json", prefix="xfer_")
    os.close(fd)
    try:
        write_table(df, path)
        return read_table(path)
    finally:
        os.remove(path)


def test_decimal_column_becomes_float():
    df = pd.DataFrame({"NII": [Decimal("1.234567891"), Decimal("2.345678912")]})
    out = _roundtrip(df)
    assert str(out["NII"].dtype) == "float64"
    # .round(3) artık etki eder (eskiden str dtype → sessiz no-op).
    assert out["NII"].round(3).tolist() == pytest.approx([1.235, 2.346])


def test_decimal_with_nulls_coerces():
    df = pd.DataFrame({"NII": [Decimal("1.5"), None, Decimal("2.5")]})
    out = _roundtrip(df)
    assert str(out["NII"].dtype) == "float64"
    vals = out["NII"].tolist()
    assert vals[0] == pytest.approx(1.5) and vals[2] == pytest.approx(2.5)
    assert pd.isna(vals[1])


def test_string_code_column_stays_string():
    # Sayısal GÖRÜNEN ama gerçek string olan kod kolonu numerify EDİLMEZ —
    # baştaki sıfırlar korunmalı ("her kolon olduğu gibi kalsın").
    df = pd.DataFrame({"BRANCH_CODE": ["00123", "00456", "07890"]})
    out = _roundtrip(df)
    assert out["BRANCH_CODE"].tolist() == ["00123", "00456", "07890"]


def test_float_int_bool_string_preserved():
    df = pd.DataFrame({
        "f": [1.5, 2.5], "i": [1, 2], "b": [True, False], "s": ["x", "y"],
    })
    out = _roundtrip(df)
    assert str(out["f"].dtype) == "float64"
    assert str(out["i"].dtype).startswith("int")
    assert str(out["b"].dtype) == "bool"
    assert out["s"].tolist() == ["x", "y"]


def test_datetime_preserved():
    df = pd.DataFrame({"d": pd.to_datetime(["2025-05-01", "2025-06-01"])})
    out = _roundtrip(df)
    assert pd.api.types.is_datetime64_any_dtype(out["d"])
    # tz-naive davranışı AYNEN korunur (tz eklenmez).
    assert not isinstance(out["d"].dtype, pd.DatetimeTZDtype)


def test_mixed_decimal_int_column_becomes_numeric():
    """BUG 1 (genişletme): TAMAMI Decimal değil, İÇİNDE bir Decimal bile olan
    object kolon numerik'e çevrilmeli. fillna(skaler)/skaler-atama Oracle NUMBER
    (Decimal) kolona int/float karıştırır → infer_dtype 'mixed-integer'/'mixed'
    "decimal" ile eşleşmez, kolon str'ye ezilir, .round() sessiz no-op /
    np.average TypeError."""
    df = pd.DataFrame({"NII": [Decimal("1.5"), 2, Decimal("3.5")]})
    out = _roundtrip(df)
    assert str(out["NII"].dtype) == "float64"
    # .round() artık etki eder (eskiden str dtype → TypeError/no-op).
    assert out["NII"].round(1).tolist() == pytest.approx([1.5, 2.0, 3.5])


def test_mixed_decimal_float_column_becomes_numeric():
    df = pd.DataFrame({"NII": [Decimal("1.5"), 2.25, Decimal("3.0")]})
    out = _roundtrip(df)
    assert str(out["NII"].dtype) == "float64"
    assert out["NII"].tolist() == pytest.approx([1.5, 2.25, 3.0])


def test_mixed_decimal_does_not_break_string_code_column():
    """Decimal numerify'i gerçek string kod kolonlarını ("00123") BOZMAMALI —
    onlarda hiç Decimal eleman yok → dokunulmaz, baştaki sıfırlar korunur."""
    df = pd.DataFrame({
        "NII": [Decimal("1.5"), 2, Decimal("3.5")],
        "BRANCH_CODE": ["00123", "00456", "07890"],
    })
    out = _roundtrip(df)
    assert str(out["NII"].dtype) == "float64"
    assert out["BRANCH_CODE"].tolist() == ["00123", "00456", "07890"]


def test_duplicate_column_labels_roundtrip_no_crash():
    """BUG 2: tekrarlı kolon etiketinde write_table'ın per-kolon prob döngüsü
    d[c].dtype yapardı; d[c] DataFrame döner → AttributeError, çıktı kaybolurdu.
    Pozisyonla dolaşıp serileştirmeden önce benzersizleştirince çökmeden
    round-trip olur ve veri kaybolmaz (orient='table' aynı-adlı anahtarı ezerdi,
    "a":1,"a":2 → 2 + okuma kolonları patlatırdı)."""
    df = pd.DataFrame([[1, 2, 3], [4, 5, 6]], columns=["a", "a", "b"])
    out = _roundtrip(df)
    assert list(out.columns) == ["a", "a", "b"]
    assert out.values.tolist() == [[1, 2, 3], [4, 5, 6]]


def test_tz_aware_column_preserves_instant_and_tz():
    """BUG 3: tz-farkında kolon (datetime64[us, TZ]) eskiden to_numpy('us') ile
    tz düşürülüp UTC wall-clock NAIVE dönerdi (Europe/Istanbul +03 → saat 3
    kayar, gün sınırı geçebilir). Artık tz kaydedilir, UTC ISO yazılır, okuyunca
    yeniden localize edilir → instant + tz korunur, sessiz kayma yok."""
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2025-01-15 23:30:00"]).tz_localize("Europe/Istanbul")
    })
    out = _roundtrip(df)
    assert isinstance(out["ts"].dtype, pd.DatetimeTZDtype)
    assert str(out["ts"].dtype.tz) == "Europe/Istanbul"
    # Aynı instant + aynı wall-clock (kayma yok).
    assert out["ts"].iloc[0] == df["ts"].iloc[0]
    assert str(out["ts"].iloc[0]) == "2025-01-15 23:30:00+03:00"


def test_tz_aware_america_new_york_roundtrip():
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2025-03-01 09:30:00"]).tz_localize("America/New_York")
    })
    out = _roundtrip(df)
    assert str(out["ts"].dtype.tz) == "America/New_York"
    assert out["ts"].iloc[0] == df["ts"].iloc[0]


def test_tz_aware_with_nat_preserved():
    df = pd.DataFrame({
        "ts": pd.Series(
            pd.to_datetime(["2025-01-15 01:00:00", None]).tz_localize("Europe/Istanbul")
        )
    })
    out = _roundtrip(df)
    assert str(out["ts"].dtype.tz) == "Europe/Istanbul"
    assert out["ts"].iloc[0] == df["ts"].iloc[0]
    assert pd.isna(out["ts"].iloc[1])


def test_integer_column_labels_roundtrip():
    """C-fix: pandas 3.0'da int kolon ETİKETİ + int değer → eskiden
    IntCastingNaNError (read_table çöker, sonuç atılır); int etiket + float değer
    → sessizce NaN'a düşerdi. Etiketler artık serileştirmeden önce str'lendiği
    için round-trip kayıpsız (executor zaten downstream'de str'liyor)."""
    out = _roundtrip(pd.DataFrame({0: [1, 2], 1: [3, 4]}))
    assert list(out.columns) == ["0", "1"]
    assert out["0"].tolist() == [1, 2]
    assert out["1"].tolist() == [3, 4]
    # float değerli int-adlı kolon: değerler korunur (eski sessiz NaN yok).
    outf = _roundtrip(pd.DataFrame({0: [1.0, 2.0]}))
    assert outf["0"].tolist() == [1.0, 2.0]


def test_value_counts_transpose_runs_end_to_end():
    """C-fix: `value_counts().to_frame().T` integer kolon adları üretir — çok
    yaygın transform; eskiden int-etiket/str-anahtar uyuşmazlığı yüzünden
    'Çıktı okunamadı' (ok=False) ile sessizce atılıyordu."""
    from presentations.python_runtime.executor import run_python_transform

    df = pd.DataFrame({"v": [10, 20, 30, 20, 10, 10]})
    code = "output_node_df = input_node_df['v'].value_counts().to_frame().T\n"
    r = run_python_transform(code, df)
    assert r.ok, r.error
    assert len(r.df) >= 1


def test_user_round_code_rounds_decimal_source_end_to_end():
    """Kullanıcının ORİJİNAL kodu — kaynak Decimal (Oracle NUMBER) olsa bile
    artık yuvarlar (transfer Decimal→float64 olduğu için)."""
    from presentations.python_runtime.executor import run_python_transform

    df = pd.DataFrame({
        "NII_FORECAST": [Decimal("1.234567891"), Decimal("2.345678912")],
        "NII_ACTUAL": [Decimal("3.456789123"), Decimal("4.567891234")],
    })
    code = (
        "output_node_df = input_node_df.copy()\n"
        "output_node_df['NII_FORECAST'] = output_node_df['NII_FORECAST'].round(3)\n"
        "output_node_df['NII_ACTUAL'] = output_node_df['NII_ACTUAL'].round(3)\n"
    )
    r = run_python_transform(code, df)
    assert r.ok, r.error
    assert r.df["NII_FORECAST"].tolist() == pytest.approx([1.235, 2.346])
    assert r.df["NII_ACTUAL"].tolist() == pytest.approx([3.457, 4.568])

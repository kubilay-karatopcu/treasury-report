"""reservation_data ETL portu — semantik regresyon testleri.

Legacy ``DataOperations.process_data`` + ``load_competitor_data`` sözleşmesini
doğrular: ``load_dataframe`` monkeypatch'lenir (Oracle/dev.db gerekmez),
sentetik girdi → port edilen mantık vs beklenen çıktı.

Koşum: repo kökünden `python -m pytest mevduat_panel/tests/test_reservation_data.py -q`
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mevduat_panel.engine import reservation_data as rd  # noqa: E402


# ── Sentetik girdi fabrikaları ───────────────────────────────────────────────

def _myu_frame() -> pd.DataFrame:
    """MYU-stili frame: aynı grupta iki revizyon + filtrelenecek küçük tutar."""
    return pd.DataFrame({
        "CUST_ID": [1, 1, 2],
        "CREATE_DT": ["2026-07-24", "2026-07-24", "2026-07-24"],
        "CREATE_TM": ["093000", "094500", "100000"],
        "VADE_BASLANGIC": ["32-35", "32-35", "32-35"],
        "CUST_TP": ["G", "G", "T"],
        "CCY_CODE": ["TRY", "TRY", "TRY"],
        "TALEP_REVIZE_NO": [1, 2, 1],
        "RESERVATION_AMT": [60000, 60000, 40000],  # 3. satır < 50k → elenmeli
        "MARKET_MAX_RT": [0.50, 0.50, 0.50],
        "OFFERED_RATE": [0.45, 0.46, 0.45],
        "DEMANDED_RATE": [0.48, 0.49, 0.48],
        "COMPETITOR_BANK_RTS": [0.47, 0.47, 0.47],
    })


def _empty_core_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(_myu_frame().columns))


def _treasury_frame() -> pd.DataFrame:
    """TREASURY ham frame (rename ÖNCESİ kolon adları)."""
    return pd.DataFrame({
        "CUST_ID": [9],
        "RSRVTN_DT": ["2026-07-24"],
        "CREATE_TM": ["20260724093000"],
        "MTRTY_STRT": ["32-35"],
        "MTRTY_END": ["45"],
        "CURRENCY_CD": ["TRY"],
        "PRCNG_CNT": [1],
        "RQSTD_INTRST_RT": [0.48],
        "RECMMND_INTRST_RT": [0.44],
        "APPRVD_INTRST_RT": [0.40],
        "RESERVATION_AMT": [70000],
        "MARKET_MAX_RT": [0.50],
        "COMPETITOR_BANK_RTS": [0.47],
    })


def _fake_loader(myu=None, core=None, treasury=None):
    myu = myu if myu is not None else _myu_frame()
    core = core if core is not None else _empty_core_frame()
    treasury = treasury if treasury is not None else _treasury_frame()

    def _loader(name, params=None):
        if name in ("myu", "myu_T_CUST"):
            return myu.copy()
        if name in ("core_comparison", "core_comparison_T_CUST"):
            return core.copy()
        if name in ("treasury", "treasury_T_CUST"):
            return treasury.copy()
        raise KeyError(name)
    return _loader


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader())
    rd.reset_caches()
    yield
    rd.reset_caches()


# ── current_df portu ─────────────────────────────────────────────────────────

def test_reservation_amount_filter(patched):
    """RESERVATION_AMT < 50000 satırlar elenir."""
    df = rd.build_reservation_df()
    assert (df["RESERVATION_AMT"] >= 50000).all()
    # 40000'lik MYU satırı gitmiş olmalı → kalan MYU=2, TREASURY=1
    assert len(df) == 3


def test_market_max_rate_filter(monkeypatch):
    """OFFERED_RATE > MARKET_MAX_RT*1.02 satırlar elenir."""
    myu = _myu_frame()
    myu.loc[0, "OFFERED_RATE"] = 0.90  # 0.90 > 0.50*1.02=0.51 → elenmeli
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader(myu=myu))
    rd.reset_caches()
    df = rd.build_reservation_df()
    assert (df["OFFERED_RATE"] <= df["MARKET_MAX_RT"] * 1.02).all()


def test_is_max_revize_flag(patched):
    """Aynı [DATA_SRC,CUST_ID,CREATE_DT,VADE_BASLANGIC] grubunda yalnız en yüksek
    TALEP_REVIZE_NO satırı IS_MAX_REVIZE=True."""
    df = rd.build_reservation_df()
    grp = df[(df["DATA_SRC"] == "MYU") & (df["CUST_ID"] == 1)]
    flagged = grp[grp["IS_MAX_REVIZE"]]
    assert len(flagged) == 1
    assert flagged.iloc[0]["TALEP_REVIZE_NO"] == 2


def test_json_ready_columns(patched):
    """DATE_STR_CLEAN + DATE_TIME_STR türetilir; DATE_TIME NaT satır yok."""
    df = rd.build_reservation_df()
    assert "DATE_STR_CLEAN" in df.columns
    assert "DATE_TIME_STR" in df.columns
    assert df["DATE_TIME"].notna().all()
    assert (df["DATE_STR_CLEAN"] == "2026-07-24").all()


def test_treasury_rename(patched):
    """TREASURY ham kolonları legacy adlarına map edilir."""
    df = rd.build_reservation_df()
    tre = df[df["DATA_SRC"] == "TREASURY"]
    assert len(tre) == 1
    assert tre.iloc[0]["OFFERED_RATE"] == 0.40      # APPRVD_INTRST_RT
    assert tre.iloc[0]["VADE_BASLANGIC"] == "32-35"  # MTRTY_STRT


def test_cache_and_reset(patched):
    a = rd.load_reservation_df()
    b = rd.load_reservation_df()
    assert a is b               # aynı cache referansı
    rd.reset_caches()
    c = rd.load_reservation_df()
    assert c is not a           # reset sonrası yeniden kuruldu


# ── competitor_df portu ──────────────────────────────────────────────────────

def test_parse_range():
    assert rd._parse_range("32-45 Gün") == (32, 45)
    assert rd._parse_range("500 Bin") == (500, 500)
    assert rd._parse_range(None) == (0, 0)


def test_competitor_build(monkeypatch):
    comp = pd.DataFrame({
        "TARIH": ["2026-07-24", "2026-07-23", None],
        "VADE": ["32-45 Gün", "1-3 Ay", "32-45"],
        "TUTAR": ["0-500 Bin", "500 Bin+", "0"],
        "FAIZ": [0.45, 0.44, None],
        "DOVIZ_CINSI": ["TRY", "TRY", "TRY"],
        "KAYNAK": ["web", "web", "web"],
        "URUN": ["MEVDUAT", "MEVDUAT", "MEVDUAT"],
        "BANKA_ADI": ["A", "B", "C"],
    })
    monkeypatch.setattr(rd, "load_dataframe", lambda name, params=None: comp.copy())
    rd.reset_caches()
    out = rd.build_competitor_df()
    # 3. satır TARIH/FAIZ NaN → düşer
    assert len(out) == 2
    assert out.iloc[0]["VADE_MIN"] == 32 and out.iloc[0]["VADE_MAX"] == 45
    assert "DATE_STR" in out.columns
    assert out["FAIZ"].notna().all()


# ── 2026-07-29 veri-kaybı regresyonu: oracledb float64 dtype tuzağı ─────────
# Legacy veri DataClient'ın S3 parquet önbelleğinden gelirdi (int/str korunur);
# port taze oracledb çeker → NUMBER kolonlar float64. Eski parse bu yüzden
# 10:00 öncesi MYU'yu ve TÜM TREASURY'yi (Hazine) sessizce düşürüyordu.

def test_myu_float_create_tm_before_10am_survives(monkeypatch):
    """float 93015.0 (5 hane + .0) → 09:30:15; sabah satırları düşmemeli."""
    myu = _myu_frame()
    myu["CREATE_TM"] = [93000.0, 94500.0, 100000.0]   # oracledb float64
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader(myu=myu))
    rd.reset_caches()
    df = rd.build_reservation_df()
    myu_rows = df[df["DATA_SRC"] == "MYU"]
    hours = sorted(myu_rows["DATE_TIME"].dt.strftime("%H:%M:%S"))
    assert "09:30:00" in hours, "10:00 öncesi MYU satırı düştü (float dtype)"
    assert "09:45:00" in hours


def test_treasury_float_create_tm_survives(monkeypatch):
    """float 20260724093000.0 → '.0' kuyruğu 14 haneyi bozmamalı; Hazine yaşar."""
    tre = _treasury_frame()
    tre["CREATE_TM"] = [20260724093000.0]             # oracledb float64
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader(treasury=tre))
    rd.reset_caches()
    df = rd.build_reservation_df()
    tre_rows = df[df["DATA_SRC"] == "TREASURY"]
    assert len(tre_rows) == 1, "TREASURY (Hazine) satırları tamamen düştü"
    assert tre_rows["DATE_TIME"].dt.strftime("%H:%M:%S").iloc[0] == "09:30:00"


def test_treasury_oracle_date_create_tm_falls_back(monkeypatch):
    """CREATE_TM Oracle DATE dönerse (14-hane değil) satır yine yaşamalı."""
    tre = _treasury_frame()
    tre["CREATE_TM"] = pd.to_datetime(["2026-07-24 09:30:00"])
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader(treasury=tre))
    rd.reset_caches()
    df = rd.build_reservation_df()
    assert (df["DATA_SRC"] == "TREASURY").sum() == 1


def test_myu_datetime_create_dt_survives(monkeypatch):
    """CREATE_DT datetime64 dönerse ('2026-07-24 00:00:00' str'i) parse kırılmamalı."""
    myu = _myu_frame()
    myu["CREATE_DT"] = pd.to_datetime(myu["CREATE_DT"])
    monkeypatch.setattr(rd, "load_dataframe", _fake_loader(myu=myu))
    rd.reset_caches()
    df = rd.build_reservation_df()
    assert (df["DATA_SRC"] == "MYU").sum() == 2   # 3. satır tutar filtresi (<50k)

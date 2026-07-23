"""W5a — blok digest sağlayıcıları.

Sözleşme (docs/PROCESS_REGULARIZATION_PLAN.md §3.5 W5):
- registry 12 dökümante bloğun tamamını kapsar,
- SOĞUK cache'te her digest [] döner ve ASLA Oracle'a (load_dataframe) gitmez,
- sıcak sentetik cache'te satırlar {k, v} taşır ve ≤15 satırdır.

Sentetik df'ler gerçek şemanın küçük altkümesi — motorların gerektirdiği tüm
kolonlar dahil. Sayı doğruluğu ofiste gerçek veriyle gözle doğrulanır (spec:
digest'in iş anlamı kullanıcı onayına tabidir).
"""
from __future__ import annotations

import pandas as pd
import pytest

from mevduat_panel import block_digests as bd
from mevduat_panel.engine import np_agg, outstanding, weekly


EXPECTED_BLOCKS = {
    "camon_wf", "camon_bubble", "camon_ratehm",
    "bamon_bridge", "bamon_heatmap",
    "tamon_ladder", "tamon_curve",
    "wr_rollovers", "wr_dtm",
    "np_rvhm", "np_aumcombo",
    "sec_mix",
}


@pytest.fixture
def registry():
    return bd.build_digest_registry()


@pytest.fixture
def no_oracle(monkeypatch):
    """load_dataframe'e her dokunuş test hatası — digest SQL tetikleyemez."""
    def _boom(*a, **kw):
        raise AssertionError("digest Oracle'a gitti — sözleşme ihlali")
    monkeypatch.setattr("mevduat_panel.data_source.load_dataframe", _boom)
    return _boom


@pytest.fixture
def cold_caches(monkeypatch):
    monkeypatch.setattr(outstanding, "_DD_CACHE", {})
    monkeypatch.setattr(outstanding, "_DAILY_DD_CACHE", {})
    monkeypatch.setattr(outstanding, "_SWAP_CACHE", {})
    monkeypatch.setattr(np_agg, "_NP_CACHE", None)
    monkeypatch.setattr(weekly, "_WEEKLY_AGG_DF_CACHE", {})
    monkeypatch.setattr(weekly, "_WEEKLY_FULL_DF_CACHE", {})
    from mevduat_panel.engine import sector_data
    monkeypatch.setattr(sector_data, "_BANK_VADE_CACHE", None)
    monkeypatch.setattr(sector_data, "_VADE_CACHE", None)


def _dd_df():
    """İki aylık, iki ürünlü minimal TRY_DEPOSIT_DETAIL sentetiği."""
    rows = []
    for month, rate_a, rate_b in (("2026-05-31", 0.30, 0.40),
                                  ("2026-06-30", 0.31, 0.44)):
        for prod, seg, aum, rate, bal in (
            ("KASA_T", "Mass", "AUM_0_100K", rate_a, 8e9),
            ("BTH",    "Private", "AUM_5M_10M", rate_b, 4e9),
        ):
            rows.append({
                "MONTH": pd.Timestamp(month),
                "BALANCE": bal, "INTEREST_RATE": rate, "CUST_COUNT": 100,
                "DIM_PRODUCT": prod, "DIM_SUBPRODUCT": prod + "_S",
                "DIM_CUSTOMER": "Bireysel", "DIM_AUM": aum, "DIM_SEGMENT": seg,
                "DIM_BUCKET": "0-30", "DIM_BUCKET_DTM": "0-30",
                "TENOR_RATE": 32.0, "DTM_RATE": 20.0,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def warm_dd(monkeypatch):
    monkeypatch.setattr(outstanding, "_DD_CACHE", {
        "df": _dd_df(), "dates": ["2026-05-31", "2026-06-30"],
    })


@pytest.fixture
def warm_np(monkeypatch):
    rows = []
    for day in ("2026-07-20", "2026-07-21"):
        for ch, band, rate, vol in (("Şube", "0-1M", 42.0, 900.0),
                                    ("Dijital", "5M-10M", 45.5, 400.0)):
            rows.append({
                "DAT": pd.Timestamp(day), "CCY_CODE": "TRY", "CUST_TP": "G",
                "RELATED_PC": ch, "AUM_BAND": band, "TENOR_GRP": "1M",
                "SUB_SEGMENT": "Mass", "NP_HACIM": vol, "YENI_PARA": vol / 2,
                "OS_BAKIYE": vol * 10, "NP_FAIZ": rate, "TENOR_DAYS": 32.0,
            })
    monkeypatch.setattr(np_agg, "_NP_CACHE", pd.DataFrame(rows))


@pytest.fixture
def warm_weekly(monkeypatch):
    agg = pd.DataFrame([
        {"ROLL_DATE": pd.Timestamp("2026-07-20"), "CURRENCY": cur,
         "CCY_CODE": "TRY" if cur == "TRY" else "USD", "CUST_TP": tp,
         "AUM_BAND": band, "TRY_BAKIYE_TOPLAM": bal}
        for cur, tp, band, bal in (
            ("TRY", "G", "0-5M", 3e9), ("TRY", "T", "200M+", 9e9),
            ("FX",  "G", "0-5M", 1e9),
        )
    ])
    agg["AUM_BAND"] = pd.Categorical(
        agg["AUM_BAND"], categories=weekly.WeeklyRollingsEngine.BAND_ORDER)
    full = pd.DataFrame([
        {"ROLL_DATE": pd.Timestamp("2026-07-20"), "DTM": dtm,
         "TRY_BALANCE": bal, "CUST_ID": i, "SEGMENT": "Mass",
         "HAS_KAMPANYA": 0, "KAMPANYA_ADI": None, "CUST_TP": "G"}
        for i, (dtm, bal) in enumerate([(10, 2e9), (45, 5e9), (200, 1e9)])
    ])
    key = ("14/07/2026", "20/07/2026")
    monkeypatch.setattr(weekly, "_WEEKLY_AGG_DF_CACHE", {key: agg})
    monkeypatch.setattr(weekly, "_WEEKLY_FULL_DF_CACHE", {key: full})


# ── Registry kapsamı ────────────────────────────────────────────────────────

def test_registry_covers_all_documented_blocks(registry):
    assert set(registry) == EXPECTED_BLOCKS


def test_registry_matches_process_registry():
    """PROCESS_REGISTRY'deki custom bloklarla digest kaydı senkron kalmalı."""
    from prisma_home.processes import PROCESS_REGISTRY
    ids = {b["id"] for meta in PROCESS_REGISTRY.values()
           for b in meta.get("blocks") or []}
    assert EXPECTED_BLOCKS <= ids


# ── Soğuk cache: [] + Oracle yasak ──────────────────────────────────────────

def test_cold_caches_yield_empty_without_oracle(registry, cold_caches, no_oracle):
    for bid, fn in registry.items():
        assert fn() == [], f"{bid} soğuk cache'te boş dönmedi"


# ── Sıcak sentetik cache'ler ────────────────────────────────────────────────

def _check_shape(rows):
    assert isinstance(rows, list) and len(rows) <= 15
    for r in rows:
        assert r.get("k") and isinstance(r.get("v"), str)


def test_dd_digests_shape(registry, warm_dd, no_oracle, monkeypatch):
    monkeypatch.setattr(outstanding, "_SWAP_CACHE", {})  # hedge kolu kapalı
    for bid in ("camon_wf", "camon_bubble", "camon_ratehm",
                "bamon_bridge", "bamon_heatmap"):
        rows = registry[bid]()
        _check_shape(rows)
        assert rows, f"{bid} sıcak cache'te boş döndü"


def test_camon_wf_bennet_consistency(registry, warm_dd, no_oracle):
    rows = {r["k"]: r for r in registry["camon_wf"]()}
    assert "Başlangıç WAvg" in rows and "Bitiş WAvg" in rows
    assert "Mix etkisi" in rows and "Fiyat etkisi" in rows
    # Bennet: mix + fiyat = bitiş - başlangıç (bps, yuvarlama toleransı).
    def _num(s):
        return float(s.replace("bps", "").replace(",", "").replace("+", "").strip())
    delta = _num(rows["Bitiş WAvg"]["delta"])
    parts = _num(rows["Mix etkisi"]["v"]) + _num(rows["Fiyat etkisi"]["v"])
    assert abs(delta - parts) < 1.5


def test_tenor_digests_skip_when_swap_cold(registry, warm_dd, no_oracle,
                                           monkeypatch):
    monkeypatch.setattr(outstanding, "_SWAP_CACHE", {})
    assert registry["tamon_ladder"]() == []
    assert registry["tamon_curve"]() == []


def test_np_digests_shape(registry, warm_np, no_oracle):
    for bid in ("np_rvhm", "np_aumcombo"):
        rows = registry[bid]()
        _check_shape(rows)
        assert rows, f"{bid} sıcak cache'te boş döndü"


def test_np_rvhm_reports_last_day_cells(registry, warm_np, no_oracle):
    rows = registry["np_rvhm"]()
    keys = " | ".join(r["k"] for r in rows)
    assert "Son gün" in keys and "×" in keys


def test_weekly_digests_shape(registry, warm_weekly, no_oracle):
    for bid in ("wr_rollovers", "wr_dtm"):
        rows = registry[bid]()
        _check_shape(rows)
        assert rows, f"{bid} sıcak cache'te boş döndü"


def test_digest_never_raises_on_garbage_cache(registry, no_oracle, monkeypatch):
    """Bozuk cache içeriği bile [] üretir (savunma sözleşmesi)."""
    monkeypatch.setattr(outstanding, "_DD_CACHE",
                        {"df": pd.DataFrame({"X": [1]}), "dates": ["a", "b"]})
    monkeypatch.setattr(np_agg, "_NP_CACHE", pd.DataFrame({"X": [1]}))
    for bid in ("camon_wf", "camon_bubble", "camon_ratehm", "np_rvhm"):
        assert registry[bid]() == []

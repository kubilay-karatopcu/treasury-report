"""W5a — blok digest sağlayıcıları.

Sözleşme (docs/PROCESS_REGULARIZATION_PLAN.md §3.5 W5 + FB1):
- registry 12 dökümante bloğun tamamını kapsar,
- FB1: digest'ler yalnız arka plan piramidinde koşar → motor cache'i soğuksa
  loader'ı çağırıp ISITABİLİR; veri/bağlam yoksa (hata) zarifçe [] döner,
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
    # FB1: digest artık build_payload'ı default pencereyle (DD son gününden
    # türetilen) çağırır. _DD_CACHE pencereyi verir; build_payload mock'lanır.
    monkeypatch.setattr(outstanding, "_DD_CACHE",
                        {"df": _dd_df(), "dates": ["2026-05-31", "2026-06-30"]})
    BAND = weekly.WeeklyRollingsEngine.BAND_ORDER
    payload = {
        "table_1": {
            "columns": BAND,
            "rows": [{"label": "TRY", "date": "30.06.2026",
                      "values": [3000, 0, 0, 0, 0, 9000], "total": 12000,
                      "pct_of_total": 100}],
            "footer": [
                {"label": "TRY", "total": 12000, "values": [3000, 0, 0, 0, 0, 9000]},
                {"label": "FX", "total": 1000, "values": [1000, 0, 0, 0, 0, 0]},
                {"label": "Total", "total": 13000, "values": [4000, 0, 0, 0, 0, 9000]},
            ],
        },
        "dtm_histogram": [
            {"bucket": "≤14", "volume_m": 2000.0, "ticket_count": 1},
            {"bucket": "15-32", "volume_m": 5000.0, "ticket_count": 1},
        ],
    }
    monkeypatch.setattr(weekly.WeeklyRollingsEngine, "build_payload",
                        classmethod(lambda cls, ds, de: payload))


# ── Registry kapsamı ────────────────────────────────────────────────────────

def test_registry_covers_all_documented_blocks(registry):
    assert set(registry) == EXPECTED_BLOCKS


def test_registry_matches_process_registry():
    """PROCESS_REGISTRY'deki custom bloklarla digest kaydı senkron kalmalı."""
    from prisma_home.processes import PROCESS_REGISTRY
    ids = {b["id"] for meta in PROCESS_REGISTRY.values()
           for b in meta.get("blocks") or []}
    assert EXPECTED_BLOCKS <= ids


# ── Soğuk cache / bağlamsız: zarif boş (FB1) ────────────────────────────────

def test_cold_caches_yield_empty(registry, cold_caches):
    # App bağlamı yok → loader'lar current_app'te patlar → _safe zarifçe boşaltır.
    for bid, fn in registry.items():
        out = fn()
        assert out["rows"] == [] and out["view"] is None, \
            f"{bid} soğuk/bağlamsız boş dönmedi"


# ── Sıcak sentetik cache'ler ────────────────────────────────────────────────

def _check_shape(out):
    assert isinstance(out, dict) and isinstance(out["rows"], list)
    assert len(out["rows"]) <= 15
    for r in out["rows"]:
        assert r.get("k") and isinstance(r.get("v"), str)


def test_dd_digests_shape_and_view(registry, warm_dd, no_oracle, monkeypatch):
    monkeypatch.setattr(outstanding, "_SWAP_CACHE", {})  # hedge kolu kapalı
    for bid in ("camon_wf", "camon_bubble", "camon_ratehm",
                "bamon_bridge", "bamon_heatmap"):
        out = registry[bid]()
        _check_shape(out)
        assert out["rows"], f"{bid} sıcak cache'te boş döndü"
        # W6b — view-state paritesi: dd digest'leri tarih kontrollerini taşır.
        view = out["view"]
        assert view and view["label"] and view["controls"]
        ids = {c["id"] for c in view["controls"]}
        assert any(i.endswith("-date0") for i in ids)
        vals = {c["id"]: c["value"] for c in view["controls"]}
        date0 = next(v for k, v in vals.items() if k.endswith("-date0"))
        assert date0 == "2026-05-31"     # digest'in gerçekten kullandığı tarih


def test_camon_wf_bennet_consistency(registry, warm_dd, no_oracle):
    rows = {r["k"]: r for r in registry["camon_wf"]()["rows"]}
    assert "Başlangıç WAvg" in rows and "Bitiş WAvg" in rows
    assert "Mix etkisi" in rows and "Fiyat etkisi" in rows
    # Bennet: mix + fiyat = bitiş - başlangıç (bps, yuvarlama toleransı).
    def _num(s):
        return float(s.replace("bps", "").replace(",", "").replace("+", "").strip())
    delta = _num(rows["Bitiş WAvg"]["delta"])
    parts = _num(rows["Mix etkisi"]["v"]) + _num(rows["Fiyat etkisi"]["v"])
    assert abs(delta - parts) < 1.5


def test_tenor_empty_when_swap_unavailable(registry, warm_dd, monkeypatch):
    # FB1: swap ısıtılamaz + hedge overlay app bağlamı ister → build_snapshot
    # patlar → digest zarifçe boş (üretimde swap prewarm ile dolu).
    monkeypatch.setattr(outstanding, "_SWAP_CACHE", {})
    assert registry["tamon_ladder"]()["rows"] == []
    assert registry["tamon_curve"]()["rows"] == []


def test_np_digests_shape_and_view(registry, warm_np, no_oracle):
    for bid in ("np_rvhm", "np_aumcombo"):
        out = registry[bid]()
        _check_shape(out)
        assert out["rows"], f"{bid} sıcak cache'te boş döndü"
    # np_rvhm view: son iki günün penceresi + günlük frekans.
    vals = {c["id"]: c["value"] for c in registry["np_rvhm"]()["view"]["controls"]}
    assert vals == {"np-vp-date0": "2026-07-20",
                    "np-vp-date1": "2026-07-21",
                    "np-vp-freq": "D"}


def test_np_rvhm_reports_last_day_cells(registry, warm_np, no_oracle):
    rows = registry["np_rvhm"]()["rows"]
    keys = " | ".join(r["k"] for r in rows)
    assert "Son gün" in keys and "×" in keys


def test_weekly_digests_shape_and_view(registry, warm_weekly):
    for bid in ("wr_rollovers", "wr_dtm"):
        out = registry[bid]()
        _check_shape(out)
        assert out["rows"], f"{bid} boş döndü"
        # FB1: pencere DD son gününden türetilir (30.06 → [24.06, 30.06]).
        vals = {c["id"]: c["value"] for c in out["view"]["controls"]}
        assert vals == {"wr-date-start": "2026-06-24",
                        "wr-date-end": "2026-06-30"}


def test_digest_never_raises_on_garbage_cache(registry, no_oracle, monkeypatch):
    """Bozuk cache içeriği bile boş rows üretir (savunma sözleşmesi)."""
    monkeypatch.setattr(outstanding, "_DD_CACHE",
                        {"df": pd.DataFrame({"X": [1]}), "dates": ["a", "b"]})
    monkeypatch.setattr(np_agg, "_NP_CACHE", pd.DataFrame({"X": [1]}))
    for bid in ("camon_wf", "camon_bubble", "camon_ratehm", "np_rvhm"):
        assert registry[bid]()["rows"] == []

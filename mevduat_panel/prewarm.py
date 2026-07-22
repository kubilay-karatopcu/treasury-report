"""Arka plan cache ısıtma + data-refresh — kaynak `_prewarm_deposit_caches` portu.

Kaynak: NIM_calculation app.py 6486-6555. Kaynakta `python app.py`
senaryosunda `app.run()`'dan ÖNCE koşar; burada OpenShift/WSGI pod açılışını
bloklamamak için daemon thread'de koşar.

Varsayılan: **AÇIK** — pod açılışında tüm motorlar RAM'e ısıtılır (kullanıcı
kararı 2026-07-22: eski davranış = gün başı toplu ısıtma). `MEVDUAT_PANEL_PREWARM=0`
ile kapatılabilir (motorlar o zaman ilk istekte lazy yüklenir).

Data tazeleme: `refresh_all()` tüm cache'leri boşaltıp yeniden ısıtır. ODH
cronjob'u bunu `/mevduat-panel/admin/refresh` endpoint'i üzerinden vurur
(bkz. routes.py). Cache disiplini process-lifetime kalır; tazeleme = restart
yerine bu çağrı.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("mevduat_panel")


def _warm_steps():
    """(ad, yükleyici) listesi — açılış ve refresh aynı sırayı kullanır.

    Import'lar fonksiyon içinde: prewarm modülü uygulama import'unda yan-etki
    olarak engine'leri (dolayısıyla Oracle bağımlılıklarını) çekmesin."""
    from .engine.np_agg import load_np_data
    from .engine.outstanding import (
        DailyDepositEngine,
        DepositDetailEngine,
        SwapHedgeEngine,
    )
    from .engine.outstanding_daily import load_outstanding_daily
    from .engine.sector_data import warm_all as warm_sector_data

    return [
        ("TRY_DEPOSIT_DETAIL", DepositDetailEngine._load),
        ("daily_deposit", DailyDepositEngine._load),
        ("new_production", load_np_data),
        ("outstanding_daily", load_outstanding_daily),
        ("sector_data", warm_sector_data),
        ("swaps", SwapHedgeEngine._load),
    ]


def _reset_modules():
    """`reset_caches()` sunan tüm engine modülleri — refresh sırasıyla boşaltılır."""
    from .engine import np_agg, outstanding, outstanding_daily, sector_data, weekly

    return [np_agg, outstanding, outstanding_daily, sector_data, weekly]


def _warm(app) -> dict:
    """Warm adımlarını sırayla koşar; {ad: "ok"|"error"} döner.
    Hata olan adım lazy'ye düşer (log'lanır), diğerlerini bloklamaz."""
    result: dict = {}
    with app.app_context():
        for name, fn in _warm_steps():
            try:
                fn()
                result[name] = "ok"
                log.info("mevduat_panel prewarm: %s OK", name)
            except Exception:
                result[name] = "error"
                log.exception("mevduat_panel prewarm: %s BAŞARISIZ (lazy'ye düşülür)", name)
    return result


def reset_all(app) -> None:
    """Tüm engine cache'lerini boşaltır (sonraki yükleme SQL'i tazeler)."""
    with app.app_context():
        for mod in _reset_modules():
            try:
                mod.reset_caches()
            except Exception:
                log.exception("mevduat_panel refresh: %s.reset_caches BAŞARISIZ", mod.__name__)


def refresh_all(app) -> dict:
    """Cache'leri boşalt + yeniden ısıt. ODH cronjob'unun tetiklediği tazeleme.
    {"ok": bool, "steps": {...}, "elapsed_s": float} döner."""
    t0 = time.perf_counter()
    reset_all(app)
    steps = _warm(app)
    elapsed = round(time.perf_counter() - t0, 2)
    ok = all(v == "ok" for v in steps.values())
    log.info("mevduat_panel refresh tamam: %.2fs ok=%s %s", elapsed, ok, steps)
    return {"ok": ok, "steps": steps, "elapsed_s": elapsed}


def start_background_prewarm(app) -> None:
    """Daemon thread'de açılış ısıtması başlatır — çağıran hiç bloklanmaz."""
    t = threading.Thread(target=_warm, args=(app,), name="mevduat-panel-prewarm", daemon=True)
    t.start()

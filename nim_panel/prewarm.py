"""Arka plan cache ısıtma — kaynak `_prewarm_deposit_caches` portu.

Kaynak: NIM_calculation app.py 6486-6555. Kaynakta `python app.py`
senaryosunda `app.run()`'dan ÖNCE koşar; burada OpenShift/WSGI pod açılışını
bloklamamak için daemon thread'de koşar ve `NIM_PANEL_PREWARM=1` ortam
değişkeniyle açılır (varsayılan: kapalı — motorlar ilk istekte lazy yüklenir,
kaynak cache disiplini aynen geçerlidir: process-lifetime, restart'ta sıfır).
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("nim_panel")


def _prewarm(app) -> None:
    from .engine.np_agg import load_np_data
    from .engine.outstanding import (
        DailyDepositEngine,
        DepositDetailEngine,
        SwapHedgeEngine,
    )
    from .engine.outstanding_daily import load_outstanding_daily
    from .engine.sector_data import warm_all as warm_sector_data

    steps = [
        ("TRY_DEPOSIT_DETAIL", DepositDetailEngine._load),
        ("daily_deposit", DailyDepositEngine._load),
        ("new_production", load_np_data),
        ("outstanding_daily", load_outstanding_daily),
        ("sector_data", warm_sector_data),
        ("swaps", SwapHedgeEngine._load),
    ]
    with app.app_context():
        for name, fn in steps:
            try:
                fn()
                log.info("nim_panel prewarm: %s OK", name)
            except Exception:
                log.exception("nim_panel prewarm: %s BAŞARISIZ (lazy'ye düşülür)", name)


def start_background_prewarm(app) -> None:
    """Daemon thread'de prewarm başlatır — çağıran hiç bloklanmaz."""
    t = threading.Thread(target=_prewarm, args=(app,), name="nim-panel-prewarm", daemon=True)
    t.start()

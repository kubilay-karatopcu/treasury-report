"""Future Deposit Rollings endpoint'leri.

Kaynak: NIM_calculation (bs_evolution5 @ c569ae3) — satır referansları blok
başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları uyarlandı
(bkz. nim_panel/tools/extract_a4a5.py).
"""

from __future__ import annotations

import json
import logging
import threading as _threading
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .routes import nim_panel_bp

from .engine.weekly import WeeklyRollingsEngine, _mask_full_nm


log = logging.getLogger("nim_panel")


# Kaynak: app.py 3688-3689
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

# ── app.py 3674-3675 ──
WEEKLY_CACHE: Dict[Tuple[str, str], Dict] = {}
WEEKLY_SEGMENTS_CACHE: Dict[Tuple[str, str], Dict] = {}

# ── app.py 4896-5014 ──
@nim_panel_bp.route("/api/weekly_rollings", methods=["GET"])
@login_required
def api_weekly_rollings():
    """Mevduat Dönüşleri (Weekly Report) — three pivot tables.

    Query params:
        date_start  DD/MM/YYYY  inclusive lower bound on MTRTY_DT
        date_end    DD/MM/YYYY  inclusive upper bound

    Returns:
        {ok, date_start, date_end, row_count, table_1, table_2, table_3}
        — see WeeklyRollingsEngine.build_payload for table shape.

    Cache: process-lifetime, keyed by (date_start, date_end).
    """
    date_start = request.args.get("date_start", "").strip()
    date_end   = request.args.get("date_end",   "").strip()
    if not date_start or not date_end:
        return _json_response(
            {"ok": False, "error": "date_start and date_end are required (DD/MM/YYYY)."},
            status=400)
    try:
        # Format'ı sıkı parse et — malformed string cache'i zehirlemesin.
        ds_dt = pd.to_datetime(date_start, format="%d/%m/%Y")
        de_dt = pd.to_datetime(date_end,   format="%d/%m/%Y")
    except Exception as e:
        return _json_response(
            {"ok": False, "error": f"Invalid date (DD/MM/YYYY expected): {e}"},
            status=400)
    if ds_dt > de_dt:
        return _json_response(
            {"ok": False, "error": "date_start cannot be after date_end."}, status=400)

    key = (date_start, date_end)
    if key in WEEKLY_CACHE:
        return _json_response({"ok": True, **WEEKLY_CACHE[key]})
    try:
        payload = WeeklyRollingsEngine.build_payload(date_start, date_end)
        WEEKLY_CACHE[key] = payload
        return _json_response({"ok": True, **payload})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.info("WEEKLY_ROLLINGS ERROR:\n" + tb)
        return _json_response({"ok": False, "error": str(e), "traceback": tb}, status=500)


def _weekly_parse_dates():
    """Shared DD/MM/YYYY validation for weekly_* endpoints.
    Returns (date_start, date_end, None) on success, or (None, None, error_response)."""
    date_start = request.args.get("date_start", "").strip()
    date_end   = request.args.get("date_end",   "").strip()
    if not date_start or not date_end:
        return None, None, _json_response(
            {"ok": False, "error": "date_start and date_end are required (DD/MM/YYYY)."}, status=400)
    try:
        ds_dt = pd.to_datetime(date_start, format="%d/%m/%Y")
        de_dt = pd.to_datetime(date_end,   format="%d/%m/%Y")
    except Exception as e:
        return None, None, _json_response(
            {"ok": False, "error": f"Invalid date (DD/MM/YYYY expected): {e}"}, status=400)
    if ds_dt > de_dt:
        return None, None, _json_response(
            {"ok": False, "error": "date_start cannot be after date_end."}, status=400)
    return date_start, date_end, None


def _weekly_run(cache: Dict, builder, date_start: str, date_end: str, error_tag: str):
    key = (date_start, date_end)
    if key in cache:
        return _json_response({"ok": True, **cache[key]})
    try:
        payload = builder(date_start, date_end)
        cache[key] = payload
        return _json_response({"ok": True, **payload})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.info(f"{error_tag} ERROR:\n" + tb)
        return _json_response({"ok": False, "error": str(e), "traceback": tb}, status=500)


@nim_panel_bp.route("/api/weekly_segments", methods=["GET"])
@login_required
def api_weekly_segments():
    """Slide 2 — müşteri segmenti dağılımı + top 20 + HHI."""
    ds, de, err = _weekly_parse_dates()
    if err: return err
    return _weekly_run(WEEKLY_SEGMENTS_CACHE,
                       WeeklyRollingsEngine.build_segments_payload,
                       ds, de, "WEEKLY_SEGMENTS")


@nim_panel_bp.route("/api/weekly_drilldown", methods=["GET"])
@login_required
def api_weekly_drilldown():
    """Cell drill-down — tek bir hücre için müşteri listesi + mini analitikler."""
    ds, de, err = _weekly_parse_dates()
    if err: return err
    roll_date = request.args.get("roll_date", "").strip()
    aum_band  = request.args.get("aum_band",  "").strip()
    currency  = request.args.get("currency",  "").strip()
    cust_tp   = request.args.get("cust_tp",   "").strip()
    # Tüm filtreler opsiyonel: Total kolonu / pinned satır tıklamasında
    # tarih veya band boş gelebilir, hatta hiç filtre olmayabilir.
    if roll_date:
        try:
            pd.to_datetime(roll_date, format="%d/%m/%Y")
        except Exception as e:
            return _json_response(
                {"ok": False, "error": f"Invalid roll_date (DD/MM/YYYY): {e}"}, status=400)
    try:
        payload = WeeklyRollingsEngine.build_drilldown_payload(
            ds, de, roll_date, aum_band, currency, cust_tp)
        return _json_response({"ok": True, **payload})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.info("WEEKLY_DRILLDOWN ERROR:\n" + tb)
        return _json_response({"ok": False, "error": str(e), "traceback": tb}, status=500)




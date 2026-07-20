#!/usr/bin/env python3
"""Faz A4+A5 ekstraksiyonu: Weekly Rollings + New Business portu.

- engine/np_agg.py + engine/outstanding_daily.py: kaynak dosyalar birebir,
  yalniz `from engine.db_source import load_dataframe` -> `..data_source`.
- engine/weekly.py: WeeklyRollingsEngine + _mask_full_nm (app.py 3214-3686).
- routes_weekly.py: 3 endpoint (app.py 4896-5014).
- routes_np.py: NP yardimcilari + 10 endpoint + detail-master katmani
  (bsc_np_* endpoint'leri SECTOR bagimli -> Faz A6'da).
"""
import os
from pathlib import Path

SRCDIR = Path(os.environ.get(
    "NIM_SRC",
    "/tmp/claude-0/-home-user-treasury-report/352d97fa-5f6e-5022-ba03-cb7f8d08e7cc/scratchpad/NIM_calculation",
))
SRC = SRCDIR / "app.py"
MOD = Path("/home/user/treasury-report/nim_panel")

lines = SRC.read_text().splitlines(keepends=True)

def rng(a, b):
    return f"# ── app.py {a}-{b} ──\n" + "".join(lines[a - 1: b]) + "\n"

def logify(text):
    out = []
    for ln in text.splitlines(keepends=True):
        if "print(" in ln:
            ln = ln.replace('print(f"[WARN] ', 'log.warning(f"') \
                   .replace('print("[WARN] ', 'log.warning("') \
                   .replace('print(f"[startup] ', 'log.info(f"') \
                   .replace('print("[startup] ', 'log.info("')
            ln = ln.replace("print(", "log.info(")
        out.append(ln)
    return "".join(out)

HDR = ('"""%s\n\nKaynak: NIM_calculation (bs_evolution5 @ c569ae3) — satır referansları blok\n'
       'başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları uyarlandı\n'
       '(bkz. nim_panel/tools/extract_a4a5.py).\n"""\n')
LOG = 'import logging\n\nlog = logging.getLogger("nim_panel")\n\n'

# ── engine/np_agg.py — kaynak dosya birebir ────────────────────────────────
npagg = (SRCDIR / "engine" / "np_agg.py").read_text()
old = "        from engine.db_source import load_dataframe\n"
assert old in npagg
npagg = npagg.replace(old, "        from ..data_source import load_dataframe  # port: db_source yerine\n")
npagg = logify(npagg)
if "print(" in npagg and "log =" not in npagg:
    npagg = npagg.replace("import pandas as pd\n", "import pandas as pd\n\n" + LOG, 1)

# ── engine/outstanding_daily.py — kaynak dosya birebir ─────────────────────
osd = (SRCDIR / "engine" / "outstanding_daily.py").read_text()
old = "    from engine.db_source import load_dataframe\n"
assert old in osd
osd = osd.replace(old, "    from ..data_source import load_dataframe  # port: db_source yerine\n")
osd = logify(osd)
if "print(" in osd and "log =" not in osd:
    osd = osd.replace("import pandas as pd\n", "import pandas as pd\n\n" + LOG, 1)

# ── engine/weekly.py ───────────────────────────────────────────────────────
wk = HDR % "Future Deposit Rollings motoru — WeeklyRollingsEngine + KVKK maskesi."
wk += """
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_source import load_dataframe
from .common import _aum_numeric_key, _wavg

""" + LOG + """
# Port notu: kaynak config.ENV — _to_bind yalnız prod yolunu kullanır.
_ENV = "PRODUCTION_DB"

"""
wk += rng(3214, 3663)        # WeeklyRollingsEngine
wk += rng(3665, 3682)        # _mask_full_nm
wk = logify(wk)

# ── routes ortak govde ─────────────────────────────────────────────────────
def routes_hdr(title, extra_imports):
    return HDR % title + """
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
""" + extra_imports + """

log = logging.getLogger("nim_panel")


# Kaynak: app.py 3688-3689
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

"""

# routes_weekly.py
rw = routes_hdr("Future Deposit Rollings endpoint'leri.", """
from .engine.weekly import WeeklyRollingsEngine, _mask_full_nm
""")
rw += rng(3674, 3675)        # WEEKLY_CACHE + WEEKLY_SEGMENTS_CACHE
rw += rng(4896, 5014)        # weekly_rollings + weekly_segments + weekly_drilldown

# routes_np.py
rn = routes_hdr("New Business — Volume & Pricing endpoint'leri.", """
from .data_source import load_dataframe
from .engine.common import _build_bubble_charts, _wavg
from .engine.np_agg import (
    _AUM_LABELS as NP_AUM_LABELS,
    aggregate_distribution as np_aggregate_distribution,
    aggregate_timeseries as np_aggregate_timeseries,
    apply_filters as np_apply_filters,
    compound_to_simple_pct as np_compound_to_simple_pct,
    get_dimension_values as np_get_dimension_values,
    load_np_data,
    simple_to_compound_pct_series as np_simple_to_compound_pct_series,
)
from .engine.outstanding_daily import (
    COMMON_AUM_ORDER,
    COMMON_AUM_TO_NP_BANDS,
    COMMON_TENOR_ORDER,
    COMMON_TENOR_TO_NP_GRP,
    COMMON_TENOR_TO_OS_VADE,
    NP_AUM_TO_COMMON,
    NP_TENOR_TO_COMMON,
    _require_mapped as os_require_mapped,
    aggregate_outstanding,
    load_outstanding_daily,
)
from .engine.weekly import _mask_full_nm
""")
rn += rng(6556, 6635)        # _parse_list_param/_apply_tenor_buckets/_parse_np_filters/_freq_param/_np_records
rn += rng(6636, 6750)        # np/meta + np/aum_rate_chart
rn += "# Port notu: bsc_np_rate_series + bsc_np_monthly_table (kaynak 6751-6981)\n"
rn += "# sector_data'ya bağımlı — Faz A6 ile gelir.\n\n"
rn += rng(6982, 7263)        # segment_rate_bubble + rate_volume_bubble
rn += rng(7264, 7695)        # rate_volume_heatmap
rn += rng(7696, 7830)        # cell_timeseries
rn += rng(7831, 7896)        # detail master lock + enrich + load_np_detail*
rn += rng(7897, 7912)        # detail_prewarm
rn += rng(7913, 8137)        # cell_drilldown
rn += rng(8138, 8230)        # rate_volume_curve
rn += rng(8231, 8249)        # volume_pricing
for _ in (0,):
    rw = rw.replace('@app.route("', '@nim_panel_bp.route("')
    rn = rn.replace('@app.route("', '@nim_panel_bp.route("')
    rw = rw.replace('methods=["GET"])\ndef ', 'methods=["GET"])\n@login_required\ndef ')
    rn = rn.replace('methods=["GET"])\ndef ', 'methods=["GET"])\n@login_required\ndef ')
rw = logify(rw)
rn = rn.replace("app.logger.warning(", "log.warning(")
rn = rn.replace("        from engine.db_source import load_dataframe\n",
                "        from .data_source import load_dataframe  # port: db_source yerine\n")
rn = logify(rn)

outputs = {
    "engine/np_agg.py": npagg,
    "engine/outstanding_daily.py": osd,
    "engine/weekly.py": wk,
    "routes_weekly.py": rw,
    "routes_np.py": rn,
}
for f, txt in outputs.items():
    (MOD / f).write_text(txt)
    print(f"{f}: {len(txt.splitlines())} satir")

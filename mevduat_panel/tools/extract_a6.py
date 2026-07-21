#!/usr/bin/env python3
"""Faz A6 ekstraksiyonu: Sector Comparison + BSC endpoint'leri portu.

- engine/sector_data.py: kaynak dosya birebir (db_source -> data_source).
- routes_sector.py: 8 sektör endpoint'i (app.py 4488-4848) + 2 BSC NP
  endpoint'i (app.py 6751-6981).
- common.py'deki gecici _DEMAND_SUBPRODUCTS sabiti artık sector_data'dan
  import edilir (tek kaynak).
"""
import os
from pathlib import Path

SRCDIR = Path(os.environ.get(
    "NIM_SRC",
    "/tmp/claude-0/-home-user-treasury-report/352d97fa-5f6e-5022-ba03-cb7f8d08e7cc/scratchpad/NIM_calculation",
))
SRC = SRCDIR / "app.py"
MOD = Path("/home/user/treasury-report/mevduat_panel")

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
       '(bkz. mevduat_panel/tools/extract_a6.py).\n"""\n')
LOG = 'import logging\n\nlog = logging.getLogger("mevduat_panel")\n\n'

# ── engine/sector_data.py — kaynak dosya birebir ───────────────────────────
sd = (SRCDIR / "engine" / "sector_data.py").read_text()
old = "from engine.db_source import load_dataframe\n"
assert old in sd
sd = sd.replace(old, "from ..data_source import load_dataframe  # port: db_source yerine\n")
sd = sd.replace("    from engine.db_source import load_dataframe as _ld\n",
                "    from ..data_source import load_dataframe as _ld  # port: db_source yerine\n")
sd = sd.replace("    from engine.np_agg import",
                "    from .np_agg import")  # port: paket-ici lazy import
assert "from engine." not in sd
sd = logify(sd)
if "log.warning" in sd or "log.info" in sd:
    sd = sd.replace("import pandas as pd\n", "import pandas as pd\n\n" + LOG, 1)

# ── routes_sector.py ───────────────────────────────────────────────────────
rs = HDR % "Sector Comparison + BSC endpoint'leri."
rs += """
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .engine.common import _convert_rate_series, _wavg
from .engine.outstanding import DailyDepositEngine, DepositDetailEngine
from .engine.np_agg import (
    aggregate_timeseries as np_aggregate_timeseries,
    apply_filters as np_apply_filters,
    compound_to_simple_pct as np_compound_to_simple_pct,
    load_np_data,
    simple_to_compound_pct_series as np_simple_to_compound_pct_series,
)
from .engine.sector_data import (
    TCMB_BUCKET_META,
    _days_to_tcmb_key,
    bddk_mix_weight_summary,
    load_bddk_amt_kirilim,
    load_bddk_maliyet,
    load_bddk_vade,
    load_tcmb_deposit_rates,
    sector_blotter,
    sector_deposit_rate_series,
    sector_mix_attribution_monthly,
    sector_outstanding_monthly,
    sector_outstanding_series,
    sector_wavg_tenor_by_month,
    tcmb_rate_table,
    vade_mix_comparison,
    vade_mix_presentation,
)
from .routes import mevduat_panel_bp

log = logging.getLogger("mevduat_panel")


# Kaynak: app.py 3688-3689
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

"""
rs += rng(4488, 4620)        # sector_deposit_rates
rs += rng(4621, 4647)        # tcmb_rate_table
rs += rng(4648, 4678)        # sector_blotter
rs += rng(4679, 4727)        # sector_outstanding
rs += rng(4728, 4774)        # sector_outstanding_monthly
rs += rng(4775, 4805)        # sector_mix_attribution
rs += rng(4806, 4827)        # sector_vade_mix
rs += rng(4828, 4848)        # sector_vade_mix_pres
rs += rng(6751, 6886)        # bsc_np_rate_series
rs += rng(6887, 6981)        # bsc_np_monthly_table
rs = rs.replace('@app.route("', '@mevduat_panel_bp.route("')
rs = rs.replace('methods=["GET"])\ndef ', 'methods=["GET"])\n@login_required\ndef ')
rs = rs.replace("        from engine.sector_data import _DEMAND_SUBPRODUCTS\n",
                "        from .engine.sector_data import _DEMAND_SUBPRODUCTS  # port\n")
rs = rs.replace("        from engine.sector_data import load_bist_tlref, load_tcmb_deposit_rates\n",
                "        from .engine.sector_data import load_bist_tlref  # port (tcmb yukarida)\n")
rs = rs.replace("        from engine.sector_data import (load_tcmb_deposit_rates,\n",
                "        from .engine.sector_data import (  # port\n")
rs = logify(rs)

(MOD / "engine" / "sector_data.py").write_text(sd)
(MOD / "routes_sector.py").write_text(rs)
for f in ("engine/sector_data.py", "routes_sector.py"):
    print(f"{f}: {len((MOD / f).read_text().splitlines())} satir")

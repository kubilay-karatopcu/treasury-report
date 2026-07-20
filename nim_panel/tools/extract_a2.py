#!/usr/bin/env python3
"""Faz A2 ekstraksiyonu: kaynak app.py -> nim_panel Outstanding üçlüsü.

Cost + Balance + Tenor motorları ve endpoint'leri (heatmap/drill endpoint'leri
üç sayfanın ortak fabrikası olduğundan birlikte taşınır). Kod gövdeleri
BIREBIR taşınır (satır referanslı); yalnız modül bağları uyarlanır:
- @app.route -> @nim_panel_bp.route + @login_required
- print()   -> logging (repo standardı: commitli kodda print yok)
- engine.sector_data._DEMAND_SUBPRODUCTS -> common.py sabiti (A6'ya kadar)
- _ENV      -> sabit "PRODUCTION_DB" (yalnız log mesajlarında)
"""
import os
import re as _re
from pathlib import Path

SRC = Path(os.environ.get(
    "NIM_SRC",
    "/tmp/claude-0/-home-user-treasury-report/352d97fa-5f6e-5022-ba03-cb7f8d08e7cc/scratchpad/NIM_calculation",
)) / "app.py"
MOD = Path("/home/user/treasury-report/nim_panel")

lines = SRC.read_text().splitlines(keepends=True)

def rng(a, b):
    """1-tabanli [a, b] kapali aralik, '# ── app.py A-B ──' basligiyla."""
    return f"# ── app.py {a}-{b} ──\n" + "".join(lines[a - 1: b]) + "\n"

def logify(text):
    """print() -> log.warning/log.info (satir bazli, [WARN] sezgisiyle)."""
    out = []
    for ln in text.splitlines(keepends=True):
        if "print(" in ln:
            ln = ln.replace('print(f"[WARN] ', 'log.warning(f"') \
                   .replace('print("[WARN] ', 'log.warning("') \
                   .replace('print(f"[startup] ', 'log.info(f"') \
                   .replace('print("[startup] ', 'log.info("')
            ln = ln.replace("print(", "log.info(")  # kalan genel durum
        out.append(ln)
    return "".join(out)

HDR = ('"""%s\n\nKaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları\n'
       'blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları\n'
       'uyarlandı (bkz. nim_panel/tools/extract_a2.py).\n"""\n')

LOG = 'import logging\n\nlog = logging.getLogger("nim_panel")\n\n'

# ── engine/common.py — paletler, sayısal yardımcılar, bubble/heatmap ───────
common = HDR % "Ortak yardımcılar — paletler, oran dönüşümü, bubble/heatmap kurucuları."
common += """
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

""" + LOG + """
# Kaynak: engine/sector_data.py::_DEMAND_SUBPRODUCTS — sector_data portu
# (Faz A6) gelene kadar sabit burada yaşar; A6'da tek kaynağa bağlanır.
_DEMAND_SUBPRODUCTS = ("KGH", "BTH")

"""
common += rng(122, 123)      # _SCENARIO_PALETTE
common += rng(131, 137)      # _scenario_color
common += rng(145, 148)      # Y_* sabitleri
common += rng(227, 243)      # _wavg, _bps, _fmt_int
common += rng(244, 251)      # _pick_col
common += rng(252, 266)      # _auto_y_range
common += rng(267, 287)      # _date_str, _aum_numeric_key
common += rng(956, 1099)     # _build_bubble_charts
common += rng(1100, 1158)    # _RATE_CONV_MODES, _convert_rate_series, _apply_demand_deposit
common += rng(1159, 1218)    # _cost_bubble_source
common += rng(1219, 1320)    # _rate_heatmap_seg_aum
old = "    from engine.sector_data import _DEMAND_SUBPRODUCTS\n"
assert old in common
common = common.replace(old, "    # port: _DEMAND_SUBPRODUCTS modul sabiti (kaynak: engine/sector_data.py)\n")
common = logify(common)

# ── engine/chart_builder.py — NIMChartBuilder (waterfall figür fabrikası) ──
cb = HDR % "NIMChartBuilder — waterfall/driver Plotly figür kurucusu (cost motorlarının fabrikası)."
cb += """
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .common import (
    Y_MAX_PAD,
    Y_MIN_FLOOR,
    Y_MIN_SPAN,
    Y_PAD_RATIO,
    _auto_y_range,
    _bps,
    _fmt_int,
    _wavg,
)

"""
cb += rng(735, 804)          # NIMChartBuilder (_wf_ranges/_wf_data/_line_data)
cb += ("    # Port notu: build_all (kaynak 806-952) NII-ozel — NIMDecompositionEngine\n"
      "    # gerektirir; deposit motorlari yalniz _wf_data/_line_data kullanir. Faz B'de\n"
      "    # NII tasinirsa build_all o fazda geri gelir.\n")
cb = logify(cb)

# ── engine/outstanding.py — Cost + Tenor + Balance motorları (tek modül) ──
# Kaynakta hepsi app.py'de yaşıyordu; sınıflar birbirine çapraz referans verir
# (Balance→DepositDetail._load, Tenor→DailyDeposit, Cost→Tenor._bucket_lower).
# Ayrı dosyalara bölmek dairesel import üretir — kaynak sırasıyla tek modül.
out = HDR % "Outstanding üçlüsü motorları — Cost (DepositDetail/DailyDeposit), Tenor (+Swap), Balance."
out += """
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..data_source import load_dataframe
from .chart_builder import NIMChartBuilder
from .common import (
    _apply_demand_deposit,
    _aum_numeric_key,
    _bps,
    _build_bubble_charts,
    _convert_rate_series,
    _cost_bubble_source,
    _date_str,
    _fmt_int,
    _rate_heatmap_seg_aum,
    _wavg,
)

""" + LOG + """
# Port notu: kaynak config.ENV — yalnız log mesajlarında kullanılır.
_ENV = "PRODUCTION_DB"

"""
out += rng(953, 954)         # _DD_CACHE
out += rng(1321, 1724)       # DepositDetailEngine
out += rng(1725, 2106)       # _DAILY_DD_CACHE + DailyDepositEngine
out += rng(2107, 2348)       # TenorAnalysisEngine + DailyTenorEngine
out += rng(2349, 2373)       # _apply_tenor_mode
out += rng(2374, 2581)       # _build_tenor_payload
out += rng(2582, 2635)       # _build_tenor_daily_evolution
out += rng(2636, 2794)       # SwapHedgeEngine
out += rng(2795, 2869)       # BalanceAnalysisEngine
out += rng(2870, 2928)       # DailyBalanceEngine
out += rng(2929, 2958)       # _apply_balance_merges
out += rng(2959, 3213)       # _build_balance_payload
out = logify(out)

# ── request_params.py — dim filtre / merge parserları ──────────────────────
pp = HDR % "İstek parametresi parserları — filter_<DIM>, merges (Balance/Tenor/Cost ortak)."
pp += """
from __future__ import annotations

import json
from typing import Dict, List

from .engine.outstanding import BalanceAnalysisEngine, TenorAnalysisEngine

"""
pp += rng(4280, 4296)        # _parse_dim_filters (tenor/cost ortak)
pp += rng(4368, 4422)        # _parse_balance_dim_filters + _parse_balance_merges
pp += rng(4423, 4453)        # _parse_tenor_merges
pp = logify(pp)

# ── routes ortak gövde ─────────────────────────────────────────────────────
def routes_hdr(title):
    return HDR % title + """
from __future__ import annotations

import json
import logging
from typing import Dict, List

import numpy as np
import pandas as pd
from flask import Response, request
from flask_login import login_required
from plotly.utils import PlotlyJSONEncoder

from .request_params import (
    _parse_balance_dim_filters,
    _parse_balance_merges,
    _parse_dim_filters,
    _parse_tenor_merges,
)
from .routes import nim_panel_bp

log = logging.getLogger("nim_panel")


# Kaynak: app.py 3688-3689 — PlotlyJSONEncoder numpy/pandas/NaN'i güvenle
# serileştirir (jsonify NaN tuzağı yok).
def _json_response(payload: dict, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, cls=PlotlyJSONEncoder),
        status=status,
        mimetype="application/json",
    )

"""

# routes_cost.py
rc = routes_hdr("Outstanding Cost Analysis endpoint'leri.")
rc += """from .engine.common import (
    _RATE_CONV_MODES,
    _aum_numeric_key,
    _convert_rate_series,
    _cost_bubble_source,
    _rate_heatmap_seg_aum,
    _wavg,
)
from .engine.outstanding import (
    BalanceAnalysisEngine,
    DailyBalanceEngine,
    DailyDepositEngine,
    DepositDetailEngine,
    TenorAnalysisEngine,
    _apply_balance_merges,
)

"""
rc += rng(4199, 4279)        # deposit_detail_dates/waterfalls + daily ikilisi
rc += rng(5078, 5139)        # rate_drill
rc += rng(5140, 5205)        # cost_rate_heatmap
rc += rng(5206, 5399)        # hm_product_bar
rc += rng(5400, 5504)        # deposit_product_daily
rc += rng(5505, 5583)        # bubble_series
rc = logify(rc)

# routes_outstanding.py (balance + tenor)
ro = routes_hdr("Outstanding Balance + Tenor endpoint'leri.")
ro += """from .engine.common import _aum_numeric_key, _convert_rate_series, _wavg
from .engine.outstanding import (
    BalanceAnalysisEngine,
    DailyBalanceEngine,
    DailyDepositEngine,
    DailyTenorEngine,
    DepositDetailEngine,
    SwapHedgeEngine,
    TenorAnalysisEngine,
    _apply_balance_merges,
    _apply_tenor_mode,
    _build_balance_payload,
    _build_tenor_daily_evolution,
    _build_tenor_payload,
)

"""
ro += rng(4298, 4367)        # tenor_dates/monthly/daily
ro += rng(4454, 4487)        # balance_dates
ro += rng(4849, 4895)        # balance_monthly + balance_daily
ro += rng(5015, 5077)        # balance_drill
ro = logify(ro)

for r in (rc, ro):
    pass
rc = rc.replace('@app.route("', '@nim_panel_bp.route("')
ro = ro.replace('@app.route("', '@nim_panel_bp.route("')
rc = rc.replace('methods=["GET"])\ndef ', 'methods=["GET"])\n@login_required\ndef ')
ro = ro.replace('methods=["GET"])\ndef ', 'methods=["GET"])\n@login_required\ndef ')

# ── yaz ────────────────────────────────────────────────────────────────────
(MOD / "engine").mkdir(exist_ok=True)
(MOD / "engine" / "__init__.py").write_text(HDR % "nim_panel hesap motorları.")
outputs = {
    "engine/common.py": common,
    "engine/chart_builder.py": cb,
    "engine/outstanding.py": out,
    "request_params.py": pp,
    "routes_cost.py": rc,
    "routes_outstanding.py": ro,
}
for f, txt in outputs.items():
    (MOD / f).write_text(txt)
    print(f"{f}: {len(txt.splitlines())} satir")

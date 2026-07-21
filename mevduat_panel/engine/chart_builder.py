"""NIMChartBuilder — waterfall/driver Plotly figür kurucusu (cost motorlarının fabrikası).

Kaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları
blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları
uyarlandı (bkz. mevduat_panel/tools/extract_a2.py).
"""

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

# ── app.py 735-804 ──
class NIMChartBuilder:
    @staticmethod
    def _wf_ranges(x_labels, y_values, measures):
        """Convert Plotly-style waterfall arrays to [low, high] bar ranges."""
        bars, running = [], 0.0
        for label, val, measure in zip(x_labels, y_values, measures):
            val = float(val)
            if measure == "absolute":
                low, high = 0.0, val
                running = val
            elif measure == "relative":
                low, high = (running, running + val) if val >= 0 else (running + val, running)
                running += val
            else:  # "total"
                low, high = 0.0, running
            bars.append({"x": label, "low": round(low, 3), "high": round(high, 3),
                         "value": round(val, 3), "measure": measure})
        return bars

    @classmethod
    def _wf_data(cls, title, x, y, measures, y_min_floor, y_min_span, y_pad_ratio, y_max_pad=None, meta=None):
        bars = cls._wf_ranges(x, y, measures)
        # Exclude b.low == 0 (absolute/total bars anchored at zero) from range calc;
        # only floating bar bottoms reflect the actual NIM level.
        float_lows = [b["low"] for b in bars if b["low"] != 0]
        range_lows = float_lows if float_lows else [b["low"] for b in bars]
        all_vals = range_lows + [b["high"] for b in bars]
        yr = _auto_y_range(all_vals, pad_ratio=y_pad_ratio, min_span=y_min_span,
                           min_floor=y_min_floor, y_max_pad=y_max_pad)
        # Always anchor the Y-axis bottom 50 bps below the lowest NIM level so that
        # bars of different magnitudes remain visually distinguishable.  The min_floor
        # guard above can raise yr[0] above actual bar bottoms; override if needed.
        wf_min = float(np.nanmin([float(v) for v in all_vals]))
        yr[0] = min(yr[0], wf_min - 50.0)
        # Merge optional per-bar metadata (e.g. rate levels for tooltip)
        if meta:
            for bar, m in zip(bars, meta):
                if m:
                    bar.update(m)
        return {"type": "waterfall", "title": title, "yaxis_title": "bps",
                "y_range": [round(yr[0], 2), round(yr[1], 2)], "bars": bars}

    @staticmethod
    def _line_data(title, series_map, yaxis_title="bps", as_percent=False, dashed=None, colors=None, y_min=None, y_max=None):
        mult = 100.0 if as_percent else 10000.0
        dashed = set(dashed or [])
        colors = colors or {}
        series = []
        for name, df in series_map.items():
            if df is None or df.empty:
                continue
            points = []
            for _, row in df.iterrows():
                pt = {"x": pd.Timestamp(row["SIM_DATE_DT"]).strftime("%Y-%m-%d"),
                      "y": round(float(row["value"]) * mult, 4)}
                if "balance" in df.columns and pd.notna(row.get("balance")):
                    pt["balance"] = round(float(row["balance"]), 0)
                points.append(pt)
            entry = {"name": name, "data": points, "dash": name in dashed}
            if name in colors:
                entry["color"] = colors[name]
            series.append(entry)
        result = {"type": "line", "title": title, "yaxis_title": yaxis_title,
                  "as_percent": as_percent, "series": series}
        if y_min is not None:
            result["y_min"] = round(float(y_min), 4)
        if y_max is not None:
            result["y_max"] = round(float(y_max), 4)
        return result


    # Port notu: build_all (kaynak 806-952) NII-ozel — NIMDecompositionEngine
    # gerektirir; deposit motorlari yalniz _wf_data/_line_data kullanir. Faz B'de
    # NII tasinirsa build_all o fazda geri gelir.

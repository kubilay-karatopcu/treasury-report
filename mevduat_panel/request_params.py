"""İstek parametresi parserları — filter_<DIM>, merges (Balance/Tenor/Cost ortak).

Kaynak: NIM_calculation app.py (bs_evolution5 @ c569ae3) — satır referansları
blok başlarındadır. Hesap mantığına DOKUNULMAMIŞTIR; yalnız modül bağları
uyarlandı (bkz. mevduat_panel/tools/extract_a2.py).
"""

from __future__ import annotations

import json
from typing import Dict, List

from .engine.outstanding import BalanceAnalysisEngine, TenorAnalysisEngine

# ── app.py 4280-4296 ──
def _parse_dim_filters(request_args) -> Dict[str, List[str]]:
    """Read per-dimension filter selections from query params.

    Format: filter_<DIM>=val1|val2|val3  (URL-encoded, pipe-separated values)
    Missing or empty means "no constraint" for that dimension.
    Also reads filter_MATURITY_BUCKET for bucket-level filtering.
    """
    out: Dict[str, List[str]] = {}
    for d in list(TenorAnalysisEngine.DIMENSIONS) + ["MATURITY_BUCKET"]:
        raw = request_args.get(f"filter_{d}", "").strip()
        if not raw:
            continue
        vals = [v for v in raw.split("|") if v]
        if vals:
            out[d] = vals
    return out


# ── app.py 4368-4422 ──
def _parse_balance_dim_filters(request_args) -> Dict[str, List[str]]:
    """Read per-dimension filter selections for Balance Analysis (+ Cost heatmap).

    Same wire format as Tenor (`filter_<DIM>=v1|v2`). Base dimensions PLUS
    MATURITY_BUCKET (TENOR) — böylece Balance monthly/daily + Cost rate-heatmap
    vade kovası filtresine uyar (BalanceAnalysisEngine._filter_by_dims DIM_BUCKET'i
    ele alır).
    """
    out: Dict[str, List[str]] = {}
    for d in list(BalanceAnalysisEngine.DIMENSIONS) + ["MATURITY_BUCKET"]:
        raw = request_args.get(f"filter_{d}", "").strip()
        if not raw:
            continue
        vals = [v for v in raw.split("|") if v]
        if vals:
            out[d] = vals
    return out


def _parse_balance_merges(request_args) -> Dict[str, List[Dict]]:
    """Read the JSON-encoded `merges` arg for Balance Analysis.

    Format: {"DIM": [{"name": "AUM_0_500K", "members": ["AUM_0_100K","AUM_100K_500K"]}, ...]}
    Unknown / malformed input returns {} (silent — filters still apply).
    """
    raw = request_args.get("merges", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    _allowed = set(BalanceAnalysisEngine.DIMENSIONS) | {"MATURITY_BUCKET"}
    out: Dict[str, List[Dict]] = {}
    for dim, groups in data.items():
        if dim not in _allowed:
            continue
        if not isinstance(groups, list):
            continue
        clean = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            name = str(g.get("name", "")).strip()
            members = g.get("members") or []
            if not name or not isinstance(members, list) or not members:
                continue
            clean.append({"name": name, "members": [str(m) for m in members]})
        if clean:
            out[dim] = clean
    return out



# ── app.py 4423-4453 ──
def _parse_tenor_merges(request_args) -> Dict[str, List[Dict]]:
    """Tenor Analysis merges — Balance ile aynı format ama MATURITY_BUCKET dim'ini
    de kabul eder (vade bucket gruplaması). Bilinmeyen dim / bozuk giriş atlanır."""
    raw = request_args.get("merges", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    allowed = set(TenorAnalysisEngine.DIMENSIONS) | {"MATURITY_BUCKET"}
    out: Dict[str, List[Dict]] = {}
    for dim, groups in data.items():
        if dim not in allowed or not isinstance(groups, list):
            continue
        clean = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            name = str(g.get("name", "")).strip()
            members = g.get("members") or []
            if not name or not isinstance(members, list) or not members:
                continue
            clean.append({"name": name, "members": [str(m) for m in members]})
        if clean:
            out[dim] = clean
    return out




"""Column-name regex hints (Phase 7.c, spec §5.2).

Hard-coded patterns per concept, drawn from observed Treasury column naming.
A name match is a *hint* (confidence ``inferred_regex``), never enough to bind
in production on its own — the operator must approve it in the review UI.

The hint map is keyed by concept id so it stays aligned with the registry.
Concepts absent here simply get no name-based hint (sample matching or the LLM
fallback may still place them).
"""
from __future__ import annotations

import re


# concept_id → list of regex patterns matched against the UPPER-cased column name.
REGEX_HINTS: dict[str, list[str]] = {
    "currency":        [r"^CCY$", r"^CURRENCY$", r"^CUR_CODE$", r"^CURRENCY_CODE$", r".*_CCY$", r"^PARA_BIRIMI$"],
    "as_of_time":      [r"^AS_OF_DATE$", r"^ASOF_DATE$", r"^SNAPSHOT_DATE$", r"^REPORT_DATE$",
                        r"^POZISYON_TARIHI$", r"^DATE$", r"^TARIH$", r"^GUN_SONU.*", r"^EOD_DATE$"],
    "trade_time":      [r"^TRADE_DATE$", r"^DEAL_DATE$", r"^BOOKING_DATE$", r"^ISLEM_TARIHI$"],
    "value_time":      [r"^VALUE_DATE$", r"^VALOR$", r"^VALOR_DATE$", r"^VALOR_TARIHI$"],
    "settle_time":     [r"^SETTLE_DATE$", r"^SETTLEMENT_DATE$", r"^TAKAS_TARIHI$"],
    "maturity":        [r"^MATURITY_BUCKET$", r"^MATURITY_DAYS$", r"^MATURITY$", r"^VADE$", r"^VADE_GRUBU$", r"^KALAN_VADE.*"],
    "tenor_bucket":    [r"^TENOR$", r"^TENOR_BUCKET$", r"^VADE_DILIMI$"],
    "counterparty":    [r"^COUNTERPARTY_ID$", r"^CP_ID$", r"^COUNTERPARTY$", r"^KARSI_TARAF.*"],
    "branch":          [r"^BRANCH_ID$", r"^BRANCH_CODE$", r"^BR_ID$", r"^BRANCH$", r"^SUBE_KODU$", r"^SUBE_ID$"],
    "region":          [r"^REGION$", r"^REGION_CODE$", r"^BOLGE$", r"^BOLGE_KODU$"],
    "product_group":   [r"^PRODUCT_GROUP$", r"^PRODUCT$", r"^PROD_GRP$", r"^URUN_GRUBU$"],
    "segment":         [r"^SEGMENT$", r"^CUSTOMER_SEGMENT$", r"^MUSTERI_SEGMENTI$", r"^SEGMENT_CODE$"],
    "rating_bucket":   [r"^RATING$", r"^RATING_BUCKET$", r"^CREDIT_RATING$", r"^RISK_RATING$"],
    "user_id":         [r"^USER_ID$", r"^SICIL$", r"^SICIL_NO$", r"^KULLANICI.*"],
    "deal_id":         [r"^DEAL_ID$", r"^DEAL_NO$", r"^TRADE_ID$", r"^ISLEM_NO$"],
    "instrument_type": [r"^INSTRUMENT_TYPE$", r"^INSTR_TYPE$", r"^ENSTRUMAN_TIPI$", r"^PRODUCT_TYPE$"],
}

# Pre-compile for speed + determinism.
_COMPILED: dict[str, list[re.Pattern]] = {
    concept: [re.compile(p, re.IGNORECASE) for p in pats]
    for concept, pats in REGEX_HINTS.items()
}


def regex_candidates(column_name: str) -> list[str]:
    """Concept ids whose name patterns match ``column_name`` (deterministic order)."""
    name = (column_name or "").strip()
    out: list[str] = []
    for concept, patterns in _COMPILED.items():
        if any(p.match(name) for p in patterns):
            out.append(concept)
    return out

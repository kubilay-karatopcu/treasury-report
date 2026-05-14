"""
Deposit pricer — calls the real pricing API.

Contract:
    price_deposit(ps: PricingSession, revision_probability: float = 1.0) -> None

Mutates `ps.request` in place:
    - On success: sets price, increments pricing_no, updates previous_price
    - On failure / missing inputs: leaves state untouched and sets valid=False
"""

import os
import logging
from datetime import datetime

import requests
from urllib3.exceptions import InsecureRequestWarning

from .state import PricingSession
from .mock_data import get_customer_info

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

PRICER_HOST = os.getenv(
    "PRICER_HOST",
    "https://sdp-pricer-opensol-dev-qnbhazmevduatfiyatlama."
    "seip-vip-tst-ocpdev51.qnb.com.tr",
)
PRICER_TIMEOUT = float(os.getenv("PRICER_TIMEOUT", "30"))
MAX_REVISIONS  = 3  # hard cap; API is authoritative within this window


# --------------------------------------------------------------------------- #
# Tenor formatting
# --------------------------------------------------------------------------- #

def _tenor_range(tenor: int) -> str:
    """API expects a bucket-style string like '[32-60]'."""
    return f"[{int(tenor)}-{int(tenor)}]"


# --------------------------------------------------------------------------- #
# API call
# --------------------------------------------------------------------------- #

def _call_pricer_api(
    *,
    cust_id: int,
    amount: float,
    tenor: int,
    currency: str,
    aum: float,
    revise_number: int,
    requested_price,
    revision_probability: float = 1.0,
):
    # requested_price: float when user demanded a specific rate, "" otherwise
    payload = {"pricingParams": [{
        "asofdate":             datetime.now().strftime("%d-%m-%Y"),
        "custID":               int(cust_id),
        "amount":               float(amount),
        "tenor":                _tenor_range(tenor),
        "requestedPrice":       requested_price,
        "ccyCode":              currency,
        "aum":                  float(aum or 0),
        "revise_number":        int(revise_number),
        "revision_probability": round(revision_probability, 2),
    }]}

    log.info("pricer API call: %s", payload)
    try:
        r = requests.post(f"{PRICER_HOST}/price",
                          json=payload, verify=False, timeout=PRICER_TIMEOUT, auth=("admin", "pr1cer!87474_sdp_2025"))
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("pricer API failed: %s", e)
        return None

    try:
        price = float(data["prices"][0]["price"])
        log.info("pricer API returned: price=%s", price)
        return price
    except (KeyError, IndexError, TypeError, ValueError) as e:
        log.warning("pricer API unexpected response shape: %s (%s)", data, e)
        return None


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #

def price_deposit(ps: PricingSession, revision_probability: float = 1.0) -> None:
    req = ps.request

    # Not enough info to price.
    if not (req.cust_id and req.tenor and req.amount and req.currency):
        req.valid = False
        return

    # Hard cap on revisions.
    if req.pricing_no > MAX_REVISIONS:
        return

    # Refresh customer info (supplies AUM for the API call).
    ps.cust_info = get_customer_info(req.cust_id)

    # Normalise currency.
    ccy = req.currency.upper()
    if ccy in ("TL", "TRL"):
        ccy = "TRY"
    req.currency = ccy

    revise_number = req.pricing_no + 1  # 1-indexed for the API

    # If the user demanded a specific rate, send it; otherwise send "".
    demanded = ps.revision_check.demanded_price
    requested_price = float(demanded) if demanded else ""

    rate = _call_pricer_api(
        cust_id              = req.cust_id,
        amount               = req.amount,
        tenor                = req.tenor,
        currency             = ccy,
        aum                  = ps.cust_info.total_aum,
        revise_number        = revise_number,
        requested_price      = requested_price,
        revision_probability = revision_probability,
    )

    if rate is None:
        req.valid = False
        return

    # Commit: remember old price before overwriting.
    req.valid = True
    req.pricing_no += 1
    if req.pricing_no > 1:
        req.previous_price = req.price
    req.price = rate

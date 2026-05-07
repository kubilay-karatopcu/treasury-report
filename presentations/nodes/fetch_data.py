"""
Run the basket fetch via the DataClient → DuckDB bridge.

Only invoked when plan_fetch decides `state.fetch_mode == "refetch"`.
Heavy work (Oracle round-trip) happens here; manifest is unchanged.
"""
from __future__ import annotations

import logging

from flask import current_app

log = logging.getLogger(__name__)


def fetch_data(state):
    """Populate state.session's DuckDB with all basket items."""
    if state.session is None:
        log.warning("fetch_data: no session bound, skipping")
        return state

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        log.warning("fetch_data: no DATA_CLIENT in config, skipping")
        return state

    basket = state.manifest.get("basket", [])
    loaded = state.session.fetch_basket(dc, basket)
    state.loaded_views = loaded
    return state

"""Phase 9.b.1 — KESIF_USE_COSMOGRAPH flag wiring.

The flag is a per-environment switch that controls whether the React
bundle mounts the Cosmograph GraphCanvas (true) or the placeholder canvas
(false). Defaults off so deploys without the commercial license don't
accidentally hit a paywall.
"""
from __future__ import annotations

import json
import re


def _extract_kesif_data(html_bytes: bytes) -> dict:
    """Pull the embedded <script id="kesif-data"> JSON out of the rendered
    template so we can assert on what the React bundle will see."""
    text = html_bytes.decode("utf-8")
    m = re.search(r'<script id="kesif-data"[^>]*>(.*?)</script>', text, re.DOTALL)
    assert m, "kesif-data script tag missing"
    return json.loads(m.group(1))


def test_flag_off_by_default(client):
    r = client.get("/presentations/atolye/kesif")
    assert r.status_code == 200
    data = _extract_kesif_data(r.data)
    assert data["flags"]["use_cosmograph"] is False


def test_flag_on_when_config_set(flask_app):
    flask_app.config["KESIF_USE_COSMOGRAPH"] = True
    flask_app.config["COSMOGRAPH_LICENSE_KEY"] = "test-key-abc"
    c = flask_app.test_client()
    r = c.get("/presentations/atolye/kesif")
    data = _extract_kesif_data(r.data)
    assert data["flags"]["use_cosmograph"] is True
    assert data["cosmograph"]["license_key"] == "test-key-abc"


def test_license_key_null_when_unset(client):
    r = client.get("/presentations/atolye/kesif")
    data = _extract_kesif_data(r.data)
    # Default — no license key set; React side passes undefined to Cosmograph.
    assert data["cosmograph"]["license_key"] is None

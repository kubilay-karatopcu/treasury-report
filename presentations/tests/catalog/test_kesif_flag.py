"""Phase 9.b.1 — Bootstrap payload + Cosmograph license-key wiring.

The Keşif page renders an inline ``<script id="kesif-data">`` blob the
React bundle reads on mount. This test confirms the bootstrap shape +
that the Cosmograph license key (when configured) flows through to the
client without being leaked when unset.
"""
from __future__ import annotations

import json
import re


def _extract_kesif_data(html_bytes: bytes) -> dict:
    text = html_bytes.decode("utf-8")
    m = re.search(r'<script id="kesif-data"[^>]*>(.*?)</script>', text, re.DOTALL)
    assert m, "kesif-data script tag missing"
    return json.loads(m.group(1))


def test_bootstrap_shape(client):
    r = client.get("/presentations/atolye/kesif")
    assert r.status_code == 200
    data = _extract_kesif_data(r.data)
    # Required top-level keys for the React bundle to mount.
    assert "user" in data
    assert "draft" in data
    assert "basket" in data
    assert "cosmograph" in data
    assert "endpoints" in data
    # Endpoints the bundle needs.
    for k in ("catalog_list", "catalog_graph", "basket_update", "draft_promote"):
        assert k in data["endpoints"]


def test_license_key_propagates(flask_app):
    flask_app.config["COSMOGRAPH_LICENSE_KEY"] = "test-key-abc"
    c = flask_app.test_client()
    r = c.get("/presentations/atolye/kesif")
    data = _extract_kesif_data(r.data)
    assert data["cosmograph"]["license_key"] == "test-key-abc"


def test_license_key_null_when_unset(client):
    r = client.get("/presentations/atolye/kesif")
    data = _extract_kesif_data(r.data)
    # No key set → null (not the empty string, not the literal "None").
    assert data["cosmograph"]["license_key"] is None

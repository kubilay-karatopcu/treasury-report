"""PresentationSession — dış-yazar (importer script) sonrası manifest tazeleme.

jobs/deposits_dashboards.py gibi scriptler manifest.json'ı S3'e uygulama
DIŞINDAN yazar. Pod-local bellek kopyası eskimesin: ETag değiştiyse
get_manifest S3'ten yeniden yükler (≥30 sn throttle); HEAD desteklemeyen
DataClient'ta davranış eskisi gibi kalır.
"""
from __future__ import annotations

import json
from pathlib import Path

from presentations.session import PresentationSession


class _FakeDC:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}

    def _upload_bytes(self, key, body, content_type=None):
        self.blobs[key] = body
        self.etags[key] = f'"v{len(self.blobs)}-{hash(body) & 0xffff}"'

    def read_json(self, key):
        if key not in self.blobs:
            raise FileNotFoundError(f"NoSuchKey: {key}")
        return json.loads(self.blobs[key].decode("utf-8"))

    def _head(self, key):
        if key not in self.blobs:
            raise FileNotFoundError(f"NoSuchKey: {key}")
        return {"ETag": self.etags[key]}


def _manifest(v, title):
    return {"id": "p1", "version": v, "meta": {"title": title}, "blocks": []}


def _session(tmp_path, dc):
    return PresentationSession(user_id="A63837", presentation_id="p1",
                               duck_base_dir=Path(tmp_path), dc=dc)


def _external_write(dc, s, manifest):
    """Uygulama dışından S3 yazımı (importer script'in yaptığı)."""
    dc._upload_bytes(s.manifest_s3_key,
                     json.dumps(manifest).encode("utf-8"))


def test_external_write_reloads_after_throttle(tmp_path):
    dc = _FakeDC()
    s = _session(tmp_path, dc)
    s.set_manifest(_manifest(1, "eski"))
    assert s.get_manifest()["meta"]["title"] == "eski"

    _external_write(dc, s, _manifest(2, "yeni"))
    # Throttle penceresi içinde eski kopya dönebilir; pencereyi geçmiş say.
    s._manifest_checked_at = 0.0
    m = s.get_manifest()
    assert m["meta"]["title"] == "yeni"
    assert m["version"] == 2


def test_no_head_support_keeps_old_behavior(tmp_path):
    class _NoHeadDC(_FakeDC):
        def _head(self, key):
            raise AttributeError("head yok")

    dc = _NoHeadDC()
    s = _session(tmp_path, dc)
    s.set_manifest(_manifest(1, "eski"))
    _external_write(dc, s, _manifest(2, "yeni"))
    s._manifest_checked_at = 0.0
    # HEAD yok → dış yazım görülmez (dev stub davranışı, regresyon değil).
    assert s.get_manifest()["meta"]["title"] == "eski"


def test_unchanged_etag_does_not_reload(tmp_path):
    dc = _FakeDC()
    s = _session(tmp_path, dc)
    s.set_manifest(_manifest(1, "eski"))
    # Bellek kopyasını işaretle — reload olursa kaybolur.
    s._manifest["_marker"] = True
    s._manifest_checked_at = 0.0
    assert s.get_manifest().get("_marker") is True

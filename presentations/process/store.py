"""Process documentation store — Süreç Düzenlileştirme W1.

``presentations/blocks/store.py`` deseninin kardeşi: Local (DEV) + S3 (prod),
versiyonlu YAML, atomic create. Saklanan şey TAM descriptor DEĞİL, kullanıcı
tarafından yazılan **dökümantasyon overlay'idir**:

.. code-block:: yaml

    overlay:
      process_id: mevduat.maliyet
      version: 3
      updated_by: A16438
      updated_at: "2026-07-22T14:00:00+00:00"
      documentation:            # süreç düzeyi 4 alan
        purpose: "..."
        business_context: "..."
        decision_support: null
        known_limitations: null
      blocks_documentation:     # blok id → 4 alan
        camon_bubble:
          purpose: "..."

Neden overlay (tam descriptor değil)? Yapı (endpoint, page, anchor, blok
listesi) kodda yaşar (``PROCESS_REGISTRY``) ve deploy'la değişir; kullanıcı
metni ise ekrandan yazılır. İkisini aynı belgede versiyonlamak drift üretir —
overlay yalnız insan-metnini taşır, ``prisma_home.processes`` okuma anında
registry ile birleştirir. D1'de tam descriptor store'a geçilirse bu overlay
şeması descriptor'ın ``documentation*`` alanlarına birebir taşınır.

Kayıt yolu: ``processes/{pid}/vNNNN.yaml``. ``pid`` noktalı olabilir
(``mevduat.maliyet``) — path-güvenliği ``_PID_OK`` regex'iyle sağlanır.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import yaml

log = logging.getLogger(__name__)

_PID_OK = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_DOC_FIELDS = ("purpose", "business_context", "decision_support", "known_limitations")

#: S3 anahtar kökü — blok store'un ``blocks/`` kökünün kardeşi.
KEY_ROOT = "processes"


class ProcessStoreError(Exception):
    pass


class ProcessOverlayExistsError(ProcessStoreError):
    """Aynı versiyon zaten var — ``save_new_version`` yarışı kaybetti, retry."""


def _check_pid(pid: str) -> None:
    if not _PID_OK.match(pid or ""):
        raise ProcessStoreError(f"geçersiz süreç id'si: {pid!r}")


def overlay_key(pid: str, version: int) -> str:
    _check_pid(pid)
    return f"{KEY_ROOT}/{pid}/v{int(version):04d}.yaml"


def _clean_doc(doc: dict | None) -> dict:
    """4 alanı normalize et: strip, boş → None, bilinmeyen alanları at."""
    doc = doc or {}
    return {f: ((doc.get(f) or "").strip() or None) for f in _DOC_FIELDS}


def normalize_overlay(raw: dict[str, Any]) -> dict[str, Any]:
    """Overlay'i şemaya indirger (fazla alan taşımaz, alanları temizler)."""
    pid = raw.get("process_id") or ""
    _check_pid(pid)
    blocks_doc = {}
    for bid, bdoc in (raw.get("blocks_documentation") or {}).items():
        if isinstance(bid, str) and isinstance(bdoc, dict):
            cleaned = _clean_doc(bdoc)
            if any(cleaned.values()):
                blocks_doc[bid] = cleaned
    return {
        "process_id": pid,
        "version": int(raw.get("version") or 1),
        "updated_by": str(raw.get("updated_by") or ""),
        "updated_at": str(raw.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        "documentation": _clean_doc(raw.get("documentation")),
        "blocks_documentation": blocks_doc,
    }


def _serialise(overlay: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        {"overlay": overlay}, allow_unicode=True, sort_keys=False,
    ).encode("utf-8")


def _parse(data: bytes | str) -> dict[str, Any]:
    raw = yaml.safe_load(data if isinstance(data, str) else data.decode("utf-8")) or {}
    if "overlay" not in raw or not isinstance(raw["overlay"], dict):
        raise ProcessStoreError("overlay YAML kökü 'overlay' anahtarını taşımalı")
    return normalize_overlay(raw["overlay"])


class ProcessStore(Protocol):
    def load_latest(self, pid: str) -> dict[str, Any] | None: ...
    def list_versions(self, pid: str) -> list[int]: ...
    def save_new_version(self, overlay: dict[str, Any]) -> dict[str, Any]: ...


class LocalProcessStore:
    """DEV / offline: ``<base_dir>/<pid>/vNNNN.yaml`` (pid noktalı dizin adı)."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _dir(self, pid: str) -> Path:
        _check_pid(pid)
        return self.base_dir / pid

    def list_versions(self, pid: str) -> list[int]:
        d = self._dir(pid)
        if not d.is_dir():
            return []
        out = []
        for p in d.glob("v*.yaml"):
            try:
                out.append(int(p.stem[1:]))
            except ValueError:
                continue
        return sorted(out)

    def load_latest(self, pid: str) -> dict[str, Any] | None:
        versions = self.list_versions(pid)
        if not versions:
            return None
        path = self._dir(pid) / f"v{versions[-1]:04d}.yaml"
        return _parse(path.read_bytes())

    def save_new_version(self, overlay: dict[str, Any]) -> dict[str, Any]:
        ov = normalize_overlay(overlay)
        pid = ov["process_id"]
        versions = self.list_versions(pid)
        ov["version"] = (versions[-1] + 1) if versions else 1
        d = self._dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"v{ov['version']:04d}.yaml"
        # Atomic create (O_EXCL) — eşzamanlı iki kayıt aynı versiyonu yazamaz.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, _serialise(ov))
        finally:
            os.close(fd)
        log.info("process overlay saved: %s v%d", pid, ov["version"])
        return ov


class S3ProcessStore:
    """Prod: DataClient üzerinden S3 — blok store'un helper yüzeyiyle aynı
    (``_upload_bytes``, ``read_bytes``, ``list_prefix``)."""

    def __init__(self, dc):
        self.dc = dc

    def list_versions(self, pid: str) -> list[int]:
        _check_pid(pid)
        prefix = f"{KEY_ROOT}/{pid}/"
        out = []
        try:
            keys = self.dc.list_prefix(prefix) or []
        except Exception:
            log.exception("process store: list_prefix başarısız (%s)", prefix)
            return []
        for k in keys:
            m = re.search(r"/v(\d{4})\.yaml$", k)
            if m:
                out.append(int(m.group(1)))
        return sorted(out)

    def load_latest(self, pid: str) -> dict[str, Any] | None:
        versions = self.list_versions(pid)
        if not versions:
            return None
        data = self.dc.read_bytes(overlay_key(pid, versions[-1]))
        if not data:
            return None
        return _parse(data)

    def save_new_version(self, overlay: dict[str, Any]) -> dict[str, Any]:
        ov = normalize_overlay(overlay)
        pid = ov["process_id"]
        versions = self.list_versions(pid)
        ov["version"] = (versions[-1] + 1) if versions else 1
        key = overlay_key(pid, ov["version"])
        body = _serialise(ov)
        try:
            # Blok store'la aynı atomic conditional create; desteklenmiyorsa
            # (eski backend) en-iyi-çaba düz yazım (küçük yarış penceresi,
            # dökümantasyon overlay'i için kabul edilebilir).
            self.dc._upload_bytes(key, body, content_type="application/x-yaml",
                                  if_none_match=True)
        except TypeError:
            self.dc._upload_bytes(key, body, content_type="application/x-yaml")
        except Exception as exc:
            if "precondition" in str(exc).lower() or "412" in str(exc):
                raise ProcessOverlayExistsError(
                    f"{pid} v{ov['version']} zaten var — yeniden dene"
                ) from exc
            raise
        log.info("s3 process overlay saved: %s v%d", pid, ov["version"])
        return ov

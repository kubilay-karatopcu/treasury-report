"""Concept promotion intents (Phase 7.d, spec §3.4).

When a user wants their presentation-scoped concept to become departmental,
they request a promotion. Phase 7 only *records the intent* — a real review
queue UI lands in Phase 11. The intent is appended to a JSON ledger under the
catalog root so the data team can see pending promotions.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)

_PROMOTIONS_FILE = "_promotions.json"


def _path(catalog_root: Path) -> Path:
    return Path(catalog_root) / _PROMOTIONS_FILE


def load_promotions(catalog_root: Path) -> list[dict[str, Any]]:
    p = _path(catalog_root)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        log.exception("promotions ledger parse failed")
        return []


def record_promotion(
    catalog_root: Path,
    *,
    concept: dict[str, Any],
    presentation_id: str,
    requested_by: str,
    target_scope: str = "dept:treasury",
) -> dict[str, Any]:
    """Append a pending promotion intent. Idempotent per (concept id, pid):
    re-requesting updates the timestamp rather than duplicating."""
    root = Path(catalog_root)
    root.mkdir(parents=True, exist_ok=True)
    ledger = load_promotions(root)

    cid = concept.get("id")
    entry = {
        "concept": concept,
        "concept_id": cid,
        "presentation_id": presentation_id,
        "requested_by": requested_by,
        "target_scope": target_scope,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    # De-dup on (concept_id, pid).
    ledger = [e for e in ledger
              if not (e.get("concept_id") == cid
                      and e.get("presentation_id") == presentation_id)]
    ledger.append(entry)
    _path(root).write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry

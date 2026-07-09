"""Binding review queue + promotion (Phase 7.c.3, spec §5.6).

Glues the inference pipeline to a human-review workflow:

- :func:`build_queue` runs the deterministic stages (+ optional LLM fallback)
  for a table, then drops columns that already carry a ``human_verified``
  binding and any (column, concept) pairs the operator previously rejected.
- :func:`approve_bindings` writes operator-approved proposals into the table's
  YAML under ``catalog/tables/<SCHEMA>/<TABLE>.yaml`` with
  ``confidence: human_verified`` — the only confidence the compiler honours.
- :func:`reject_items` records rejections in a JSON sidecar so they don't
  resurface on the next scan.

Writes go to the local catalog tree (git-tracked). In DEV that's authoritative;
in prod the pod writes its local copy (read by the cached catalog within the
pod lifecycle) and the operator commits the resulting YAML for durability —
see spec §5.6.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from presentations.concepts.registry import ConceptRegistry, _load_yaml
from presentations.concepts.bindings import BindingCatalog
from presentations.concepts.schema import ColumnBinding
from presentations.concepts.inference.types import ColumnProfile
from presentations.concepts.inference.pipeline import infer_bindings, columns_without_proposal
from presentations.concepts.inference.llm_proposer import propose_bindings_llm, CompleteFn


log = logging.getLogger(__name__)

_REVIEW_STATE_FILE = "_review_state.json"


# ── Review state (rejections) ──────────────────────────────────────────────

def _state_path(catalog_root: Path) -> Path:
    return Path(catalog_root) / _REVIEW_STATE_FILE


def load_review_state(catalog_root: Path) -> dict[str, Any]:
    p = _state_path(catalog_root)
    if not p.exists():
        return {"rejected": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("rejected", [])
            return data
    except Exception:
        log.exception("review state parse failed; starting fresh")
    return {"rejected": []}


def _rejected_keys(state: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    out: set[tuple[str, str, str, str]] = set()
    for r in state.get("rejected", []):
        out.add((r.get("schema"), r.get("table"), r.get("column"), r.get("concept")))
    return out


def reject_items(catalog_root: Path, schema: str, table: str,
                 items: list[dict[str, str]]) -> int:
    """Persist (column, concept) rejections so they don't resurface."""
    root = Path(catalog_root)
    root.mkdir(parents=True, exist_ok=True)
    state = load_review_state(root)
    have = _rejected_keys(state)
    n = 0
    for it in items:
        key = (schema, table, it.get("column"), it.get("concept"))
        if None in key or key in have:
            continue
        state["rejected"].append({
            "schema": schema, "table": table,
            "column": it.get("column"), "concept": it.get("concept"),
            "rejected_at": _now_iso(),
        })
        have.add(key)
        n += 1
    _state_path(root).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


# ── Queue ───────────────────────────────────────────────────────────────

def build_queue(
    schema: str,
    table: str,
    profiles: list[ColumnProfile],
    registry: ConceptRegistry,
    catalog: BindingCatalog,
    *,
    complete_fn: CompleteFn | None = None,
    catalog_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Compute the review queue for a table.

    Returns a list of ``{column, dtype, proposals: [...]}`` for columns that
    have at least one proposal, EXCLUDING columns with an existing
    human_verified binding and previously-rejected (column, concept) pairs.
    """
    det = infer_bindings(profiles, registry)

    # LLM fallback only for columns the deterministic stages left empty.
    if complete_fn is not None:
        empty_cols = set(columns_without_proposal(det))
        if empty_cols:
            llm = propose_bindings_llm(
                table, [p for p in profiles if p.name in empty_cols],
                registry, complete_fn)
            for col, props in llm.items():
                det.setdefault(col, []).extend(props)

    # Columns already bound (human_verified) drop out entirely.
    bound_cols = {b.column for b in catalog.get_bindings(schema, table)}
    rejected = _rejected_keys(load_review_state(catalog_root)) if catalog_root else set()

    dtype_by_name = {p.name: p.dtype for p in profiles}
    queue: list[dict[str, Any]] = []
    for col, proposals in det.items():
        if col in bound_cols:
            continue
        kept = [p for p in proposals
                if (schema, table, col, p.concept) not in rejected]
        if not kept:
            continue
        queue.append({
            "column": col,
            "dtype": dtype_by_name.get(col, ""),
            "proposals": [p.to_dict() for p in kept],
        })
    # Stable order: columns with a strong proposal first, then by name.
    queue.sort(key=lambda q: (-max(p["score"] for p in q["proposals"]), q["column"]))
    return queue


# ── Approve → write YAML ──────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _table_yaml_path(catalog_root: Path, schema: str, table: str) -> Path:
    return Path(catalog_root) / "tables" / schema / f"{table}.yaml"


def approve_bindings(
    catalog_root: Path,
    schema: str,
    table: str,
    approved: list[dict[str, Any]],
    *,
    verified_by: str,
    catalog=None,
) -> int:
    """Merge approved proposals into the table YAML as human_verified bindings.

    Each item: ``{column, concept, transform}``. Validates via ColumnBinding
    before writing. Idempotent: re-approving a (column, concept) replaces the
    existing entry rather than duplicating. Preserves all other YAML fields.
    Returns the number of bindings written.

    ``catalog`` verilirse (Cached/S3BindingCatalog) doküman onun üzerinden
    okunup yazılır — PROD'da aktif katalog S3'tür; pod-lokal ``catalog_root``
    dosyasına yazmak onayı compiler'a hiç ulaştırmıyordu (restart'ta siliniyor).
    ``catalog=None`` eski dosya-sistemi davranışını korur (DEV/test).
    """
    if catalog is not None:
        doc = catalog.get_raw_doc(schema, table)
        if not isinstance(doc, dict):
            doc = {}
        return _merge_and_persist(
            doc, schema, table, approved, verified_by=verified_by,
            persist=lambda d: catalog.save_doc(schema, table, d),
        )

    path = _table_yaml_path(catalog_root, schema, table)

    # Load the existing full doc (preserve columns/description/etc.), or seed
    # a minimal one for a brand-new table.
    if path.exists():
        doc = _load_yaml(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            doc = {}
    else:
        doc = {"table": table, "schema": schema}
    def _write_fs(d: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )

    return _merge_and_persist(
        doc, schema, table, approved, verified_by=verified_by, persist=_write_fs,
    )


def _merge_and_persist(
    doc: dict[str, Any],
    schema: str,
    table: str,
    approved: list[dict[str, Any]],
    *,
    verified_by: str,
    persist,
) -> int:
    """Onaylanan binding'leri ``doc``'a merge et, ``persist`` ile kalıcılaştır."""
    doc.setdefault("table", table)
    doc.setdefault("schema", schema)

    existing = doc.get("concept_bindings") or []
    by_key: dict[tuple[str, str], dict] = {}
    for b in existing:
        if isinstance(b, dict) and b.get("column") and b.get("concept"):
            by_key[(b["column"], b["concept"])] = b

    written = 0
    for item in approved:
        column = item.get("column")
        concept = item.get("concept")
        transform = item.get("transform")
        if not column or not concept or not isinstance(transform, dict):
            continue
        candidate = {
            "concept": concept,
            "column": column,
            "transform": transform,
            "confidence": "human_verified",
            "verified_by": verified_by,
            "verified_at": _now_iso(),
        }
        # Validate before persisting — reject malformed transforms early.
        model = ColumnBinding.model_validate(candidate)
        by_key[(column, concept)] = model.model_dump(mode="json", exclude_none=True)
        written += 1

    doc["concept_bindings"] = list(by_key.values())
    persist(doc)
    return written

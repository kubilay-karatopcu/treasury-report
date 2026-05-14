"""
RFC 6902 JSON Patch — subset: replace, add, remove.
Mirrors static/js/editor/lib/patch.js — keep in sync.
"""
from __future__ import annotations
import copy
from typing import Any

from presentations.manifest import IMMUTABLE_BLOCK_FIELDS, ALLOWED_PATCH_PREFIXES, _validate_chart_length

SUPPORTED_OPS = frozenset({"replace", "add", "remove"})


# ── Path helpers ──────────────────────────────────────────────────────────────

def _parse_path(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError(f"Path must start with '/': {path!r}")
    return path[1:].split("/")


def _get_at(obj: Any, parts: list[str]) -> Any:
    for part in parts:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def _set_at(obj: Any, parts: list[str], value: Any) -> None:
    for part in parts[:-1]:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    last = parts[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


def _del_at(obj: Any, parts: list[str]) -> Any:
    for part in parts[:-1]:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    last = parts[-1]
    if isinstance(obj, list):
        idx = int(last)
        val = obj[idx]
        del obj[idx]
        return val
    else:
        val = obj[last]
        del obj[last]
        return val


# ── Single-patch application ──────────────────────────────────────────────────

def _apply_one(state: dict, patch: dict) -> None:
    op = patch["op"]
    path = patch["path"]
    parts = _parse_path(path)

    if op == "replace":
        _set_at(state, parts, patch["value"])

    elif op == "add":
        parent_parts = parts[:-1]
        last = parts[-1]
        parent = _get_at(state, parent_parts) if parent_parts else state

        if isinstance(parent, list):
            if last == "-":
                parent.append(patch["value"])
            else:
                parent.insert(int(last), patch["value"])
        elif isinstance(parent, dict):
            parent[last] = patch["value"]
        else:
            raise ValueError(f"Cannot add into {type(parent).__name__!r}")

    elif op == "remove":
        _del_at(state, parts)

    else:
        raise ValueError(f"Unsupported op: {op!r}")


# ── Public API ────────────────────────────────────────────────────────────────

def apply_patches(state: dict, patches: list[dict]) -> dict:
    """Return a deep-copied new state with all patches applied atomically."""
    new_state = copy.deepcopy(state)
    for patch in patches:
        _apply_one(new_state, patch)
    return new_state


def compute_inverse(state: dict, patches: list[dict]) -> list[dict]:
    """Return the inverse patch list that would undo applying `patches` to `state`."""
    inverse: list[dict] = []
    current = copy.deepcopy(state)

    for patch in patches:
        op = patch["op"]
        path = patch["path"]
        parts = _parse_path(path)

        if op == "replace":
            old_value = _get_at(current, parts)
            inverse.append({"op": "replace", "path": path, "value": old_value})

        elif op == "add":
            last = parts[-1]
            if last == "-":
                # After append, the item lives at index == current length of the parent list.
                parent_parts = parts[:-1]
                parent = _get_at(current, parent_parts) if parent_parts else current
                inv_path = "/" + "/".join(parent_parts + [str(len(parent))])
                inverse.append({"op": "remove", "path": inv_path})
            else:
                inverse.append({"op": "remove", "path": path})

        elif op == "remove":
            old_value = _get_at(current, parts)
            inverse.append({"op": "add", "path": path, "value": copy.deepcopy(old_value)})

        _apply_one(current, patch)

    inverse.reverse()
    return inverse


def classify_paths(patches: list[dict]) -> dict:
    """
    Returns:
      {
        "meta":       [patch, ...],
        "blocks":     {0: [patch, ...], 1: [...], ...},
        "structural": [patch, ...],   # blocks add / remove / whole-block replace
      }
    """
    result: dict = {"meta": [], "blocks": {}, "structural": []}

    for patch in patches:
        parts = _parse_path(patch["path"])

        if parts[0] == "meta":
            result["meta"].append(patch)

        elif parts[0] == "blocks":
            if len(parts) <= 2:
                # /blocks or /blocks/N or /blocks/- — structural change
                result["structural"].append(patch)
            else:
                try:
                    idx = int(parts[1])
                    result["blocks"].setdefault(idx, []).append(patch)
                except ValueError:
                    result["structural"].append(patch)

        else:
            # Unknown top-level — classify alongside meta; validator will reject it.
            result["meta"].append(patch)

    return result


def validate_patches(state: dict, patches: list[dict]) -> list[str]:
    """
    Returns a list of validation error strings.
    Empty list means all patches are acceptable.
    """
    errors: list[str] = []

    for i, patch in enumerate(patches):
        op = patch.get("op")
        path = patch.get("path", "")

        if op not in SUPPORTED_OPS:
            errors.append(f"patch[{i}]: unsupported op {op!r}")
            continue

        if not any(path.startswith(p) for p in ALLOWED_PATCH_PREFIXES):
            errors.append(f"patch[{i}]: path {path!r} is outside allowed scope (/blocks/ or /meta/)")
            continue

        parts = _parse_path(path)
        if parts[0] == "blocks" and len(parts) >= 3:
            field = parts[2]
            if field in IMMUTABLE_BLOCK_FIELDS:
                errors.append(f"patch[{i}]: field {field!r} is immutable")

    if errors:
        return errors

    # Check chart invariants on the resulting state.
    try:
        new_state = apply_patches(state, patches)
        for block in new_state.get("blocks", []):
            errors.extend(_validate_chart_length(block))
    except Exception as exc:
        # Identify the specific patch that failed so the LLM retry sees a
        # useful error (otherwise it gets just "apply error: 'blocks'" with
        # no clue which patch to fix).
        for i, p in enumerate(patches):
            try:
                apply_patches(state, patches[:i + 1])
            except Exception as inner:
                errors.append(
                    f"patch[{i}] ({p.get('op')} {p.get('path')!r}) "
                    f"apply hatası: {inner!r} — yol mevcut mu, indeks geçerli mi kontrol et"
                )
                return errors
        errors.append(f"apply error: {exc!r}")

    return errors

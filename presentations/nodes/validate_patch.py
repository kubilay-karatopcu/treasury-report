"""Validate pending patches against the current manifest."""
from __future__ import annotations

from presentations.patch import validate_patches


def validate_patch(state):
    """Run validate_patches and stash results on state."""
    if not state.pending_patches:
        state.validation_errors = []
        return state

    # Block locked items from being touched (independent of immutability rules).
    locked_errors = _check_locked_blocks(state.manifest, state.pending_patches)

    schema_errors = validate_patches(state.manifest, state.pending_patches)

    state.validation_errors = locked_errors + schema_errors
    return state


def _check_locked_blocks(manifest, patches):
    """Reject any patch whose path targets a locked block."""
    errors = []
    blocks = manifest.get("blocks", [])

    for i, p in enumerate(patches):
        path = p.get("path", "")
        parts = path.lstrip("/").split("/")
        if len(parts) < 2 or parts[0] != "blocks":
            continue
        try:
            idx = int(parts[1])
        except ValueError:
            continue
        if 0 <= idx < len(blocks) and blocks[idx].get("locked"):
            errors.append(f"patch[{i}]: blok #{idx} kilitli, değiştirilemez")

    return errors

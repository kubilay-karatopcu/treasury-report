"""Apply pending patches to the manifest and persist via session."""
from __future__ import annotations

from datetime import datetime, timezone

from presentations.patch import apply_patches


def apply_patch(state):
    """Apply pending patches to state.manifest, bump version, persist via session."""
    if not state.pending_patches:
        state.new_manifest = state.manifest
        return state

    new_manifest = apply_patches(state.manifest, state.pending_patches)
    new_manifest["version"] = state.manifest.get("version", 0) + 1
    new_manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

    if state.session is not None:
        state.session.set_manifest(new_manifest)

    state.new_manifest = new_manifest
    return state

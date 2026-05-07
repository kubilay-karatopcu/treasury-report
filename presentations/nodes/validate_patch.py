"""Validate pending patches against the current (nested) manifest."""
from __future__ import annotations

import re

from presentations.patch import validate_patches


# Path patterns we recognise:
# - /blocks/{N}                 — section level
# - /blocks/{N}/<field>         — section field
# - /blocks/{N}/children/{M}    — leaf block level
# - /blocks/{N}/children/{M}/<field>
_SECTION_PATH_RE = re.compile(r"^/blocks/(?P<si>\d+)(?:/[^/].*)?$")
_LEAF_PATH_RE    = re.compile(r"^/blocks/(?P<si>\d+)/children/(?P<ci>\d+)(?:/[^/].*)?$")


def validate_patch(state):
    if not state.pending_patches:
        state.validation_errors = []
        return state

    locked_errors = _check_locked_blocks(state.manifest, state.pending_patches)
    schema_errors = validate_patches(state.manifest, state.pending_patches)
    state.validation_errors = locked_errors + schema_errors
    return state


def _check_locked_blocks(manifest, patches):
    """Reject patches touching a locked section or a locked leaf inside a section.

    A section is locked if `section.locked == True`. A leaf is locked if
    `child.locked == True` OR if its parent section is locked (lock cascades
    down — a locked section freezes its entire subtree).
    """
    errors = []
    sections = manifest.get("blocks", [])

    for i, p in enumerate(patches):
        path = p.get("path", "")

        # Leaf path takes precedence (more specific).
        m_leaf = _LEAF_PATH_RE.match(path)
        if m_leaf:
            si = int(m_leaf.group("si"))
            ci = int(m_leaf.group("ci"))
            if 0 <= si < len(sections):
                section = sections[si]
                if section.get("locked"):
                    errors.append(
                        f"patch[{i}]: bölüm '{section.get('title','?')}' kilitli, "
                        f"içindeki bloklar değiştirilemez"
                    )
                    continue
                children = section.get("children", []) or []
                if 0 <= ci < len(children) and children[ci].get("locked"):
                    errors.append(
                        f"patch[{i}]: blok '{children[ci].get('id','?')}' kilitli"
                    )
            continue

        m_sec = _SECTION_PATH_RE.match(path)
        if m_sec:
            si = int(m_sec.group("si"))
            if 0 <= si < len(sections) and sections[si].get("locked"):
                # Allow the user to UNLOCK via direct UI patch — but the LLM
                # path goes through the immutable-fields check in validate_patches
                # so it can't write `locked` anyway. Here we only need to
                # protect non-/locked writes.
                # (If the path is exactly /blocks/N/locked, the immutability
                # check in patch.py rejects it for LLM patches.)
                if not path.endswith("/locked"):
                    errors.append(
                        f"patch[{i}]: bölüm '{sections[si].get('title','?')}' kilitli"
                    )

    return errors

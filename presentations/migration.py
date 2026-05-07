"""
One-shot manifest migrators. Run on every manifest load so legacy on-disk
formats keep working as we evolve the schema.

Currently:
- v0 (flat) → v1 (nested sections):
    [section_header, b1, b2, section_header, b3]
      → [{section_header, children: [b1, b2]}, {section_header, children: [b3]}]
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def is_nested(manifest: dict) -> bool:
    """Return True if the manifest is already in nested-sections form."""
    blocks = manifest.get("blocks", [])
    if not blocks:
        return True  # empty manifests are trivially nested-compatible
    # Nested form: every top-level block is a section_header AND has a
    # `children` field (even if empty list).
    for b in blocks:
        if b.get("type") != "section_header":
            return False
        if "children" not in b:
            return False
    return True


def migrate_to_nested(manifest: dict) -> dict:
    """Convert a legacy flat manifest into the nested-sections form.

    Behavior:
    - Walks blocks left-to-right
    - Each section_header opens a new section; subsequent non-section blocks
      become its children until the next section_header
    - Blocks BEFORE the first section_header (preamble) are auto-wrapped in
      an implicit "Giriş" section
    - The original `blocks` list is REPLACED with a list of section blocks

    Idempotent: calling on an already-nested manifest is a no-op.
    """
    if is_nested(manifest):
        return manifest

    new_blocks = []
    current_section = None
    preamble = []

    for b in manifest.get("blocks", []):
        if b.get("type") == "section_header":
            # Flush any pending preamble before the first real section.
            if preamble and not new_blocks:
                new_blocks.append({
                    "id": "h_intro_auto",
                    "type": "section_header",
                    "title": "Giriş",
                    "locked": False,
                    "children": preamble,
                })
                preamble = []

            # Promote this section_header to nested form.
            current_section = {
                "id": b.get("id") or f"h_auto_{len(new_blocks)}",
                "type": "section_header",
                "title": b.get("title", ""),
                "locked": b.get("locked", False),
                "config": b.get("config", {}),
                "children": [],
            }
            new_blocks.append(current_section)
        else:
            if current_section is None:
                preamble.append(b)
            else:
                current_section["children"].append(b)

    # If everything was preamble (no section_headers at all), wrap in one.
    if preamble and not new_blocks:
        new_blocks.append({
            "id": "h_intro_auto",
            "type": "section_header",
            "title": "Giriş",
            "locked": False,
            "children": preamble,
        })

    out = dict(manifest)
    out["blocks"] = new_blocks
    log.info(
        "migration: flat → nested | %d top-level blocks → %d sections",
        len(manifest.get("blocks", [])), len(new_blocks),
    )
    return out


def ensure_nested(manifest: dict | None) -> dict | None:
    """Convenience: migrate if needed, pass through None."""
    if manifest is None:
        return None
    return migrate_to_nested(manifest)

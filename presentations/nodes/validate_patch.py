"""Validate pending patches against the current (nested) manifest."""
from __future__ import annotations

import re

from presentations.patch import validate_patches


# Path patterns we recognise:
# - /blocks/{N}                                    — section level
# - /blocks/{N}/<field>                            — section field
# - /blocks/{N}/children/{M}                       — leaf block OR carousel (under section)
# - /blocks/{N}/children/{M}/<field>
# - /blocks/{N}/children/{M}/children/{K}          — slide inside carousel
# - /blocks/{N}/children/{M}/children/{K}/<field>
_SECTION_PATH_RE = re.compile(r"^/blocks/(?P<si>\d+)(?:/[^/].*)?$")
_LEAF_PATH_RE    = re.compile(r"^/blocks/(?P<si>\d+)/children/(?P<ci>\d+)(?:/[^/].*)?$")
_SLIDE_PATH_RE   = re.compile(r"^/blocks/(?P<si>\d+)/children/(?P<ci>\d+)/children/(?P<ki>\d+)(?:/[^/].*)?$")


def validate_patch(state):
    if not state.pending_patches:
        state.validation_errors = []
        return state

    locked_errors = _check_locked_blocks(state.manifest, state.pending_patches)
    schema_errors = validate_patches(state.manifest, state.pending_patches)
    scope_errors  = _check_selection_scope(
        state.manifest, state.pending_patches, state.selected_block_id
    )
    state.validation_errors = locked_errors + schema_errors + scope_errors
    return state


def _check_selection_scope(manifest, patches, selected_block_id):
    """Eğer kullanıcı bir blok seçtiyse, LLM o bloğun dışına çıkamaz.
    Tüm patches'ın path'i `selected_block_id`'nin path'i ile başlamalı.

    Kullanıcı için: seçili bloğu silmek + farklı bir şey eklemek gibi
    karışık akışlara izin vermez (kullanıcı önce seçimi kaldırsın).
    """
    if not selected_block_id:
        return []

    from presentations.manifest import find_block_by_id
    _, sel_path = find_block_by_id(manifest, selected_block_id)
    if sel_path is None:
        # Seçili blok manifest'te bulunamadıysa scope kontrolü atlanır
        # (zaten bir tutarsızlık var, LLM cevabını bekleyelim).
        return []

    # Orijinal seçili blokun id+type'ı — LLM whole-block replace yaparken bunları korumak zorunda
    from presentations.manifest import find_block_by_id as _find_again
    sel_block, _ = _find_again(manifest, selected_block_id)
    sel_type = (sel_block or {}).get("type", "?")

    out = []
    for i, p in enumerate(patches):
        path = p.get("path", "")
        # Geçerli: tam olarak seçili blok path'i (remove etmek için) veya alt path
        if path == sel_path or path.startswith(sel_path + "/"):
            continue
        out.append(
            f"patch[{i}] (path={path!r}): YANLIŞ HEDEF — kullanıcı '{selected_block_id}' "
            f"(type={sel_type}, path={sel_path!r}) bloğunu seçmiş, talep o bloğa yönelik. "
            f"Bu patch'i ATLA ve bunun yerine ŞU patch'i üret: "
            f'{{"op":"replace","path":"{sel_path}","value":{{"id":"{selected_block_id}",'
            f'"type":"{sel_type}","locked":false,"title":"<yeni başlık>",'
            f'"data_source":{{"original_sql":"<DOLU SQL>"}},"config":{{...}}}}}}. '
            f"id ve type AYNEN korunmalı."
        )
    return out


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

        # Slide path (most specific) — kontrol sırası önemli, leaf'ten önce
        m_slide = _SLIDE_PATH_RE.match(path)
        if m_slide:
            si = int(m_slide.group("si"))
            ci = int(m_slide.group("ci"))
            ki = int(m_slide.group("ki"))
            if 0 <= si < len(sections):
                section = sections[si]
                if section.get("locked"):
                    errors.append(
                        f"patch[{i}]: bölüm '{section.get('title','?')}' kilitli"
                    )
                    continue
                children = section.get("children", []) or []
                if 0 <= ci < len(children):
                    carousel = children[ci]
                    if carousel.get("locked"):
                        errors.append(
                            f"patch[{i}]: carousel '{carousel.get('id','?')}' kilitli"
                        )
                        continue
                    slides = carousel.get("children", []) or []
                    if 0 <= ki < len(slides) and slides[ki].get("locked"):
                        errors.append(
                            f"patch[{i}]: slide '{slides[ki].get('id','?')}' kilitli"
                        )
            continue

        # Leaf path (or carousel container as a whole).
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

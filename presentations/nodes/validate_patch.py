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
    scope_sel_errors = _check_selection_scope(
        state.manifest, state.pending_patches, state.selected_block_id
    )
    contract_errors = _check_scope_contract(
        state.manifest, state.pending_patches, _resolve_scope_contract(state),
    )
    state.validation_errors = (
        locked_errors + schema_errors + scope_sel_errors + contract_errors
    )
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


# ── Phase 8.a — scope contract enforcement (spec §4.1) ──────────────────────

# Direct pinned-filter mutation: /filters/pinned/<id>(/...).
_PINNED_PATH_RE = re.compile(r"^/filters/pinned/(?P<fid>[^/]+)(?:/.*)?$")
# Variable-binding mutation under any block nesting level.
_VAR_BINDING_RE = re.compile(
    r"^(?P<block>/blocks/\d+(?:/children/\d+){0,2})/variable_bindings/(?P<var>[^/]+)(?:/.*)?$"
)
# Scope-owned routing/status paths a Sunum patch must not touch (rule 3).
_ROUTING_PATH_RE = re.compile(
    r"^/(?:scope_ref/.*|status/(?:lazy_tables|cached_tables)|routing)(?:/.*)?$"
)


def _resolve_scope_contract(state):
    """Return the active ScopeContract or None.

    Prefers an explicitly-attached ``state.scope_contract`` (what tests and the
    re-entry flow set). Falls back to loading via the manifest's ``scope_ref``
    from ``current_app.config['SCOPE_STORE']`` — best-effort, never raises (no
    app context / no store / no scope_ref → None → checks are skipped, which is
    the pre-Phase-8 behaviour)."""
    sc = getattr(state, "scope_contract", None)
    if sc is not None:
        return sc
    ref = (state.manifest or {}).get("scope_ref")
    if not isinstance(ref, dict):
        return None
    pid = ref.get("presentation_id")
    version = ref.get("scope_version")
    if not pid or version is None:
        return None
    try:
        from flask import current_app
        store = current_app.config.get("SCOPE_STORE")
        if store is None:
            return None
        return store.load(pid, int(version))
    except Exception:
        return None


def _block_dict_for_var_path(manifest, block_ptr: str):
    """Navigate a ``/blocks/N(/children/M(/children/K)?)?`` pointer to its block
    dict, or None if the path doesn't resolve."""
    node = manifest
    toks = block_ptr.strip("/").split("/")
    i = 0
    while i < len(toks):
        key = toks[i]
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return None
        i += 1
        if i < len(toks) and toks[i].isdigit():
            idx = int(toks[i])
            if not isinstance(node, list) or idx >= len(node):
                return None
            node = node[idx]
            i += 1
    return node if isinstance(node, dict) else None


def _check_scope_contract(manifest, patches, scope):
    """Reject patches that violate the scope contract (§4.1).

    Rules:
      1. Pinned-filter mutation — direct ``/filters/pinned/<id>`` paths, or a
         ``variable_bindings/<var>`` write where the variable binds a pinned
         scope filter.
      2. ``scope_ref`` tampering — only the scope re-entry flow may touch it
         (patches carrying ``_scope_reentry: true``); LLM patches never do.
      3. Lazy→cached coercion — routing is scope-owned; reject mid-session
         routing/status mutations.

    No scope contract → no checks (backwards-compatible)."""
    if scope is None:
        return []

    version = getattr(scope, "version", "?")
    from presentations.scope.binding import is_pinned_bound

    errors = []
    for i, p in enumerate(patches):
        # The scope re-entry flow legitimately rewrites scope_ref / pinned
        # state; its patches carry an internal flag. LLM patches never do.
        if p.get("_scope_reentry") is True:
            continue
        path = p.get("path", "") or ""

        m_pin = _PINNED_PATH_RE.match(path)
        if m_pin:
            errors.append(
                f"Cannot mutate pinned filter '{m_pin.group('fid')}' — "
                f"set in scope contract scope_v{version}"
            )
            continue

        m_var = _VAR_BINDING_RE.match(path)
        if m_var:
            block = _block_dict_for_var_path(manifest, m_var.group("block"))
            var = m_var.group("var")
            binding = ((block or {}).get("variable_bindings") or {}).get(var)
            if binding is not None and is_pinned_bound(scope, binding):
                pf_id = binding.get("from_scope_filter") if isinstance(binding, dict) \
                    else getattr(binding, "from_scope_filter", None)
                errors.append(
                    f"Cannot mutate pinned filter '{pf_id}' — "
                    f"set in scope contract scope_v{version}"
                )
                continue

        if path == "/scope_ref" or path.startswith("/scope_ref/"):
            errors.append(
                f"Cannot modify scope_ref outside the scope re-entry flow "
                f"(path={path!r})"
            )
            continue

        if _ROUTING_PATH_RE.match(path):
            errors.append(
                f"Cannot change table routing in Sunum (path={path!r}); "
                "return to Hazırlık to edit the scope contract"
            )

    return errors

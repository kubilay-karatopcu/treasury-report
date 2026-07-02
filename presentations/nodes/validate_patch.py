"""Validate pending patches against the current (nested) manifest."""
from __future__ import annotations

import re

from presentations.patch import validate_patches


# Block paths are arbitrarily nested: /blocks/{i}(/children/{j})*  followed by an
# optional field segment. We walk the index segments generically rather than
# matching a fixed depth, so section > carousel > canvas > leaf (4 levels) works.


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
    sql_ref_errors = _check_sql_source_refs(state)
    state.validation_errors = (
        locked_errors + schema_errors + scope_sel_errors + contract_errors
        + sql_ref_errors
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


def _block_chain_for_path(manifest, path):
    """Resolve a block patch path to the chain of blocks it descends through —
    e.g. [section, carousel, canvas, leaf] for a 4-level path. Stops at the
    deepest fully-addressed block (a trailing field segment like '/title' or
    '/config/value' is ignored). Returns [] for non-block paths."""
    if not path.startswith("/blocks/"):
        return []
    segs = path.lstrip("/").split("/")   # ['blocks', '0', 'children', '1', ...]
    chain = []
    cur_arr = manifest.get("blocks", [])
    k = 1
    while k < len(segs) and segs[k].isdigit():
        idx = int(segs[k])
        if not (0 <= idx < len(cur_arr)):
            break
        node = cur_arr[idx]
        chain.append(node)
        # Descend only if the next segment is exactly 'children'.
        if k + 1 < len(segs) and segs[k + 1] == "children":
            cur_arr = node.get("children", []) or []
            k += 2
        else:
            break
    return chain


def _check_locked_blocks(manifest, patches):
    """Reject patches touching a locked block or any locked ancestor.

    A block is frozen if its own ``locked`` is True OR any ancestor container
    (section / carousel / canvas) is locked — lock cascades down the whole
    subtree, at any nesting depth. A direct UI unlock of a top-level section
    (path ending in ``/locked``) is allowed when no ancestor is locked.
    """
    errors = []
    for i, p in enumerate(patches):
        path = p.get("path", "")
        chain = _block_chain_for_path(manifest, path)
        if not chain:
            continue
        # Any locked ANCESTOR freezes the target.
        locked_anc = next((a for a in chain[:-1] if a.get("locked")), None)
        if locked_anc is not None:
            errors.append(
                f"patch[{i}]: {locked_anc.get('type', 'blok')} "
                f"'{locked_anc.get('id', '?')}' kilitli"
            )
            continue
        # The target itself. A top-level section may still be unlocked via a
        # direct /locked write (the immutable-fields check guards LLM patches).
        target = chain[-1]
        if target.get("locked"):
            is_section = len(chain) == 1
            if not (is_section and path.endswith("/locked")):
                errors.append(
                    f"patch[{i}]: {target.get('type', 'blok')} "
                    f"'{target.get('id', '?')}' kilitli"
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


# ── Üretilen SQL'in tablo referansı doğrulaması ─────────────────────────────
# LLM'in halüsine ettiği SCHEMA.TABLO adları eskiden apply'a kadar geçip
# çalışma anında ORA-00942 ile patlıyor ve retry döngüsü aynı hayali tabloyu
# tekrar üretiyordu. Burada validate aşamasında yakalanır → hata mesajı
# LLM'e "mevcut tablolar şunlar" diye geri beslenir (retry düzeltebilir).


def _iter_patch_sqls(patches):
    """Pending patch'lerdeki her aday SQL'i (patch_idx, sql) olarak üret.

    Kapsanan şekiller:
      - whole-block value (data_source.original_sql / .sql) + container
        çocukları (herhangi derinlik)
      - path .../data_source (dict value) ve .../original_sql | .../sql
        (string value)
    """
    def _walk_block(idx, node):
        if not isinstance(node, dict):
            return
        ds = node.get("data_source")
        if isinstance(ds, dict):
            sql = ds.get("original_sql") or ds.get("sql")
            if isinstance(sql, str) and sql.strip():
                yield idx, sql
        for child in node.get("children") or []:
            yield from _walk_block(idx, child)

    for i, p in enumerate(patches or []):
        if p.get("op") not in ("add", "replace"):
            continue
        path = p.get("path", "") or ""
        value = p.get("value")
        if isinstance(value, dict) and ("type" in value or "children" in value):
            yield from _walk_block(i, value)
        elif path.endswith("/data_source") and isinstance(value, dict):
            sql = value.get("original_sql") or value.get("sql")
            if isinstance(sql, str) and sql.strip():
                yield i, sql
        elif (path.endswith("/original_sql") or path.endswith("/data_source/sql")) \
                and isinstance(value, str) and value.strip():
            yield i, value


def _known_table_universe(state):
    """Bilinen SCHEMA.TABLO evreni (set[str]) ya da bilinemiyorsa None.

    Kaynaklar: TABLE_DOC_STORE (tek kaynak) + scope contract'ın table_ref'leri.
    Evren BOŞ/erişilemezse None döner — hiçbir şey bilmiyorken asla bloklama
    (test app'leri / store'suz ortamlar).
    """
    universe: set[str] = set()
    try:
        from flask import current_app
        store = current_app.config.get("TABLE_DOC_STORE")
        if store is not None:
            for schema, table in (store.list_tables() or []):
                universe.add(f"{str(schema).upper()}.{str(table).upper()}")
    except Exception:
        pass
    sc = getattr(state, "scope_contract", None)
    if sc is not None:
        for b in getattr(sc, "basket", []) or []:
            ref = getattr(b, "table_ref", None)
            if ref is not None and getattr(ref, "schema_name", None) and getattr(ref, "name", None):
                universe.add(f"{ref.schema_name.upper()}.{ref.name.upper()}")
    return universe or None


def _check_sql_source_refs(state):
    """Patch'lerdeki şema-nitelikli tablo referanslarını evrene karşı doğrula.

    Yalnız ``SCHEMA.TABLO`` biçimindeki referanslar denetlenir — alias-only
    FROM'lar (Hazırlık view'ları, CTE'ler, ``upload__*``) DuckDB routing'inde
    zaten net hatayla çözülür ve burada yanlış-pozitif üretmemeli. Best-effort:
    her istisna sessizce boş liste (chat'i asla bu kontrol kırmaz).
    """
    try:
        patches = state.pending_patches or []
        sqls = list(_iter_patch_sqls(patches))
        if not sqls:
            return []
        universe = _known_table_universe(state)
        if not universe:
            return []

        from presentations.concepts.integration import derive_source_tables

        errors = []
        shown_universe = ", ".join(sorted(universe)[:15]) + (
            f" … (+{len(universe) - 15})" if len(universe) > 15 else "")
        for idx, sql in sqls:
            for schema, table in derive_source_tables(
                    {"data_source": {"original_sql": sql}}):
                ref = f"{schema}.{table}"
                if ref not in universe:
                    errors.append(
                        f"patch[{idx}]: SQL '{ref}' diye bir tabloya başvuruyor — "
                        f"bu tablo katalogda YOK (uydurma). Yalnız şu tabloları "
                        f"kullan: {shown_universe}. Hazırlık view'ları için "
                        f"şema öneki KULLANMA (FROM alias_adi)."
                    )
        return errors
    except Exception:
        return []

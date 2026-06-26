"""Generate patches by calling the LLM with the appropriate system prompt.

Adım 2: ek olarak `catalog.json`'u yükler ve LLM'e geçer. LLM SQL üretirken
sadece bu kataloğa bakar, başka tablo adı uydurmaz.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import current_app

from presentations.llm import load_prompt
from presentations.duck import summarize_views

log = logging.getLogger(__name__)


_DEFAULT_CATALOG_PATH = Path(__file__).parent.parent / "catalog.json"
_catalog_cache: dict | None = None
_catalog_mtime: float | None = None
_catalog_loaded_path: Path | None = None


def _load_catalog() -> dict | None:
    """Hot-reloaded catalog (so adding tables doesn't require restart).
    Path Flask config'inden gelir (DEV_MODE'da farklı dosya gösterir);
    cached in-process; mtime VE path değişiminde yeniden yüklenir."""
    global _catalog_cache, _catalog_mtime, _catalog_loaded_path

    path = Path(current_app.config.get("CATALOG_PATH") or _DEFAULT_CATALOG_PATH)
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        log.warning("catalog not found at %s", path)
        return None

    if _catalog_cache is None or _catalog_mtime != mtime or _catalog_loaded_path != path:
        try:
            with path.open(encoding="utf-8") as f:
                _catalog_cache = json.load(f)
            _catalog_mtime = mtime
            _catalog_loaded_path = path
            log.info("catalog loaded from %s (%d domains)",
                     path, len(_catalog_cache.get("domains", [])))
        except Exception as exc:
            log.exception("catalog failed to load: %s", exc)
            return None

    return _catalog_cache


def _load_table_docs(state) -> list | None:
    """Phase 6.5.b: load all TableDocs from app.config['TABLE_DOC_STORE'].

    We pass *all* migrated docs into the prompt because the LLM hasn't picked
    a target table yet — it needs to see the universe to choose. The token
    budget is fine: 5 tables × ~10 columns × ~80 chars = ~4k tokens.

    Returns None if the store isn't configured (legacy path) so the prompt
    falls back to the flat catalog.
    """
    store = current_app.config.get("TABLE_DOC_STORE")
    if store is None:
        return None
    try:
        return store.list_all_docs()
    except Exception as exc:
        log.warning("table_docs load failed (non-fatal): %s", exc)
        return None


DATA_BOUND_BLOCK_TYPES = {
    "kpi", "bar_chart", "line_chart", "area_chart",
    "pie_chart", "heatmap", "radial_bar", "data_table",
}


def _collect_block_ids(manifest) -> set:
    """Manifest'teki tüm blok id'leri (iç içe children dahil)."""
    ids: set = set()

    def walk(b):
        if not isinstance(b, dict):
            return
        bid = b.get("id")
        if isinstance(bid, str):
            ids.add(bid)
        for c in (b.get("children") or []):
            walk(c)

    for b in (manifest or {}).get("blocks", []):
        walk(b)
    return ids


def _unique_id(desired: str, taken: set) -> str:
    base = desired or "blok"
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def _uniquify_subtree_ids(block: dict, taken: set) -> None:
    """Eklenen bir bloğun (ve tüm alt bloklarının) id'lerini `taken`'a karşı
    benzersizleştir; üretilen yeni id'leri `taken`'a ekle."""
    if not isinstance(block, dict):
        return
    bid = block.get("id")
    new = _unique_id(bid if isinstance(bid, str) else "", taken)
    block["id"] = new
    taken.add(new)
    for c in (block.get("children") or []):
        _uniquify_subtree_ids(c, taken)


def _ensure_unique_block_ids(state, patches):
    """Güvenlik ağı: 'N kopya oluştur' derken LLM (özellikle Qwen GGUF) aynı
    id'yi tekrar kullanabiliyor → bloklar üst üste geliyor, hedefleme
    belirsizleşiyor, frontend React key çakışıyor. /blocks altına `add` edilen
    her bloğun (+ alt bloklarının) id'sini manifest'te ve bu batch içinde zaten
    görülenlere karşı benzersizleştir. `replace` (yerinde düzenleme — id korunur)
    ve /filters add'leri dokunulmaz."""
    if not patches:
        return patches
    taken = _collect_block_ids(state.manifest)
    for p in patches:
        if p.get("op") != "add":
            continue
        if not str(p.get("path") or "").startswith("/blocks"):
            continue
        v = p.get("value")
        if isinstance(v, dict) and v.get("type"):
            _uniquify_subtree_ids(v, taken)
    return patches


def generate_patch(state):
    """
    Read state.user_message + state.manifest + state.selected_block_id,
    plus a DuckDB data summary (legacy basket path) and the catalog
    (new SQL path), call the LLM, set state.pending_patches and state.explanation.

    Post-process: data-bound bloklarda SQL eksikse focused bir 2. LLM çağrısı
    ile SQL'i ayrıca üret (gpt-4o-mini / Qwen 3.5 gibi orta seviye modeller
    bazen ilk turda SQL'i atlıyor — fallback ile kapatıyoruz).
    """
    llm = current_app.config["LLM_CLIENT"]
    system = load_prompt("edit")

    if state.validation_errors:
        feedback = (
            "\n\n[Önceki deneme reddedildi. Lütfen aşağıdaki hataları düzelt:]\n- "
            + "\n- ".join(state.validation_errors)
        )
        user_message = state.user_message + feedback
    else:
        user_message = state.user_message

    data_summary = _build_data_summary(state)
    catalog = _load_catalog()

    # Phase 6.5.b: load extended TableDocs for tables referenced by the
    # manifest (or — if there's no basket yet — all migrated docs). Helps the
    # LLM pick suggested_variable / suggested_semantic_tag / allowed_values
    # consistently when authoring blocks with :params.
    table_docs = _load_table_docs(state)

    patches, explanation, _suggestions = llm.generate_patches(
        system=system,
        user_message=user_message,
        manifest=state.manifest,
        selected_block_id=state.selected_block_id,
        data_summary=data_summary,
        catalog=catalog,
        library=None,   # library önerileri kaldırıldı — tek depo = BLOCK_STORE
        table_docs=table_docs,
    )

    # ── SQL fallback: data-bound block + config patch var, SQL patch yok ──
    # LLM bazen sadece config yazıp SQL'i atlıyor. Backend tarafından eksik
    # SQL'leri kapatan focused mini-call.
    try:
        patches = _fill_missing_sqls(state, patches, llm, catalog)
    except Exception as exc:
        log.warning("generate_patch: SQL fallback failed (non-fatal): %s", exc)

    # ── SQL modify fallback: kullanıcı SQL değişimi istiyor ama LLM aynı SQL'i
    # geri gönderdi (örn. "sırala" dedi ama ORDER BY eklemedi). Modification
    # niyeti + data-bound block + SQL aynı → mini-LLM call ile rewrite.
    try:
        patches = _rewrite_unchanged_sqls(state, patches, llm, catalog)
    except Exception as exc:
        log.warning("generate_patch: SQL rewrite failed (non-fatal): %s", exc)

    # ── Benzersiz blok id güvencesi: 'N kopya' isteklerinde LLM aynı id'yi
    # tekrar kullanabiliyor; eklenen blokların id'lerini benzersizleştir.
    try:
        patches = _ensure_unique_block_ids(state, patches)
    except Exception as exc:
        log.warning("generate_patch: id-dedupe failed (non-fatal): %s", exc)

    state.pending_patches = patches
    state.explanation = explanation
    state.validation_errors = []
    state.suggestions = []   # library önerileri kaldırıldı (tek depo = BLOCK_STORE)
    return state


def _fill_missing_sqls(state, patches, llm, catalog):
    """Patches içinde data-bound block için SQL eksikse focused LLM call ile
    SQL'i üret, patch içine yerleştir.

    İki senaryo:
    - **Whole-block replace patch** içinde data_source.original_sql boş →
      mini LLM call → patch.value.data_source.original_sql'a YERLEŞTİR (in-place).
    - **Sub-path config patch'leri** var ama data_source/original_sql patch'i YOK →
      mini LLM call → patches'a yeni `replace .../data_source/original_sql` ekle.
    """
    if not patches:
        return patches

    from presentations.manifest import find_block_by_id
    from presentations.nodes.execute_block_sqls import _resolve_block_from_path, _block_pointer_from_path

    config_only_blocks: dict[str, str] = {}     # block_id → block_path (sub-path senaryosu)
    sql_touched_blocks: set[str] = set()

    for p in patches:
        path = (p.get("path") or "")
        value = p.get("value")

        # ── Whole-block replace/add: value içinde tam blok ──
        if isinstance(value, dict) and value.get("type") in DATA_BOUND_BLOCK_TYPES:
            ds = value.get("data_source") or {}
            sql = ds.get("original_sql") or ds.get("sql") or ""
            if sql.strip():
                sql_touched_blocks.add(value.get("id") or "")
            else:
                # SQL boş → mini-call ile üret, value içine MUTATE ET
                produced = _generate_sql_for_block(llm, state.user_message, value, catalog)
                if produced:
                    value.setdefault("data_source", {})["original_sql"] = produced
                    sql_touched_blocks.add(value.get("id") or "")
                    log.info("generate_patch: SQL fallback — whole-block inline fill for %s",
                             value.get("id"))
            continue

        # ── Sub-path SQL patch'i ──
        if path.endswith("/data_source/original_sql") or path.endswith("/data_source/sql") \
                or path.endswith("/data_source"):
            blk = _resolve_block_from_path(state.manifest, path)
            if blk and blk.get("id"):
                # SQL içeren patch mi? Value string veya {original_sql} dict olabilir
                v = p.get("value")
                has_sql = False
                if isinstance(v, str) and v.strip():
                    has_sql = True
                elif isinstance(v, dict):
                    sv = v.get("original_sql") or v.get("sql")
                    if isinstance(sv, str) and sv.strip():
                        has_sql = True
                if has_sql:
                    sql_touched_blocks.add(blk["id"])
            continue

        # ── Sub-path config/title patch'i ──
        if "/config" in path or path.endswith("/title"):
            blk = _resolve_block_from_path(state.manifest, path)
            if blk and blk.get("type") in DATA_BOUND_BLOCK_TYPES:
                bid = blk.get("id")
                bptr = _block_pointer_from_path(path)
                if bid and bptr:
                    config_only_blocks.setdefault(bid, bptr)

    # SQL hâlihazırda yazılan block'ları çıkar
    missing = {bid: bptr for bid, bptr in config_only_blocks.items()
               if bid not in sql_touched_blocks}

    if not missing:
        return patches

    # Sub-path config-only durumlar için yeni SQL patch'leri ekle
    added = []
    for bid, bptr in missing.items():
        target_block, _ = find_block_by_id(state.manifest, bid)
        if target_block is None:
            for p in patches:
                v = p.get("value")
                if isinstance(v, dict) and v.get("id") == bid:
                    target_block = v
                    break
        if target_block is None:
            continue

        sql = _generate_sql_for_block(llm, state.user_message, target_block, catalog)
        if not sql:
            continue
        has_existing_ds = bool(target_block.get("data_source"))
        added.append({
            "op": "replace" if has_existing_ds else "add",
            "path": f"{bptr}/data_source/original_sql" if has_existing_ds else f"{bptr}/data_source",
            "value": sql if has_existing_ds else {"original_sql": sql},
        })
        log.info("generate_patch: SQL fallback — synthesised SQL for block %s", bid)

    return patches + added


_SQL_MODIFY_KEYWORDS = (
    # Sıralama
    "sırala", "sıralı", "sıralama", "order", "sort", "ascending", "descending", "asc ", " desc",
    # Filtre
    "filtrele", "filter", "where", "yalnız", "sadece",
    # Aggregasyon değişiklikleri
    "grupla", "group by", "topla", "ortala", "average", "avg", "maksimum", "minimum",
    # Limit / top
    "top ", "limit", "ilk ", "son ", "en yüksek", "en düşük",
    # Tarih/yıl/dönem değişiklikleri
    "yıl", "ay ", "çeyrek", "quarter", "tarih", "201", "202",
    # Kolon ekle/çıkar
    "kolon ekle", "kolon çıkar", "ekle ", "çıkar ",
    # Sayı formatlama / yuvarlama
    "yuvarla", "round", "ondalık", "decimal", "hane", "basamak", "kesirsiz", "tam sayı",
    # Açık SQL niyeti
    "sql", "query", "sorgu",
)


def _wants_sql_change(user_message: str) -> bool:
    msg = (user_message or "").lower()
    return any(kw in msg for kw in _SQL_MODIFY_KEYWORDS)


def _rewrite_unchanged_sqls(state, patches, llm, catalog):
    """Kullanıcı SQL'i modifiye etmek istiyor ama LLM aynı SQL'i geri gönderdi
    (cache HIT senaryosu) ya da hiç patch üretmedi (refuse). Mini-LLM call ile
    SQL'i yeniden üret, patch'i ekle/güncelle.

    Tetikleyici:
    - state.user_message modifikasyon anahtar kelimesi içeriyor (sırala, yuvarla, ...)
    - Selected block data-bound VE patch'te SQL değişikliği yok
      VEYA patch içinde data-bound block var ama SQL ya yok ya da manifest ile aynı
    """
    if not _wants_sql_change(state.user_message):
        return patches

    from presentations.manifest import find_block_by_id
    from presentations.nodes.execute_block_sqls import _resolve_block_from_path, _block_pointer_from_path

    def _existing_sql(block_id):
        if not block_id:
            return ""
        b, _ = find_block_by_id(state.manifest, block_id)
        if not b:
            return ""
        ds = b.get("data_source") or {}
        return (ds.get("original_sql") or "").strip()

    touched: dict[str, dict] = {}  # bid → {"block": ..., "ptr": ..., "patch_sql": str, "patch_ref": (patch, key_path)}

    for p in patches:
        path = p.get("path") or ""
        value = p.get("value")

        if isinstance(value, dict) and value.get("type") in DATA_BOUND_BLOCK_TYPES:
            bid = value.get("id")
            ds = value.get("data_source") or {}
            sql_in_patch = (ds.get("original_sql") or ds.get("sql") or "").strip()
            if bid:
                touched[bid] = {"block": value, "ptr": path,
                                "patch_sql": sql_in_patch, "patch_ref": (p, "whole")}
            continue

        if path.endswith("/data_source/original_sql") or path.endswith("/data_source/sql"):
            v = p.get("value")
            sql_in_patch = v.strip() if isinstance(v, str) else ""
            blk = _resolve_block_from_path(state.manifest, path)
            if blk and blk.get("id"):
                bid = blk["id"]
                bptr = _block_pointer_from_path(path)
                touched[bid] = {"block": blk, "ptr": bptr,
                                "patch_sql": sql_in_patch, "patch_ref": (p, "value")}
            continue

        if path.endswith("/data_source"):
            v = p.get("value") or {}
            sql_in_patch = ""
            if isinstance(v, dict):
                sql_in_patch = (v.get("original_sql") or v.get("sql") or "").strip()
            blk = _resolve_block_from_path(state.manifest, path)
            if blk and blk.get("id"):
                bid = blk["id"]
                bptr = _block_pointer_from_path(path)
                touched[bid] = {"block": blk, "ptr": bptr,
                                "patch_sql": sql_in_patch, "patch_ref": (p, "ds")}
            continue

        if "/config" in path or path.endswith("/title"):
            blk = _resolve_block_from_path(state.manifest, path)
            if blk and blk.get("type") in DATA_BOUND_BLOCK_TYPES and blk.get("id"):
                bid = blk["id"]
                if bid not in touched:
                    bptr = _block_pointer_from_path(path)
                    touched[bid] = {"block": blk, "ptr": bptr,
                                    "patch_sql": "", "patch_ref": None}

    # ── Seçili blok güvencesi: kullanıcı SQL değişikliği istedi ama LLM bu
    # bloğu hiç ele almadı (refuse veya yanlış hedef). Selected block data-bound
    # ise touched listesine ekle, mini-call zorla devreye girsin.
    sel_id = state.selected_block_id
    if sel_id and sel_id not in touched:
        sel_block, sel_path = find_block_by_id(state.manifest, sel_id)
        if sel_block and sel_block.get("type") in DATA_BOUND_BLOCK_TYPES and sel_path:
            touched[sel_id] = {"block": sel_block, "ptr": sel_path,
                               "patch_sql": "", "patch_ref": None}
            log.info("generate_patch: SQL rewrite — selected block %s force-included", sel_id)

    added = []
    for bid, info in touched.items():
        existing = _existing_sql(bid)
        new_sql_in_patch = info["patch_sql"]

        # SQL gerçekten değişti mi?
        if new_sql_in_patch and new_sql_in_patch != existing:
            continue  # değişti, mini-call gerekmiyor

        # Block'un type/title bilgisini topla
        block_for_call = info["block"]
        # Patch içindeki value tamamı block değilse manifest'ten al
        if not isinstance(block_for_call, dict) or "type" not in block_for_call:
            b, _ = find_block_by_id(state.manifest, bid)
            block_for_call = b or {}

        new_sql = _generate_sql_for_block(
            llm, state.user_message, block_for_call, catalog,
            existing_sql=existing or None,
        )
        if not new_sql or new_sql.strip() == existing:
            continue

        # Patch'i güncelle veya yeni patch ekle
        ref = info["patch_ref"]
        if ref is not None:
            patch_obj, kind = ref
            if kind == "whole":
                patch_obj.setdefault("value", {}).setdefault("data_source", {})["original_sql"] = new_sql
            elif kind == "value":
                patch_obj["value"] = new_sql
            elif kind == "ds":
                v = patch_obj.get("value") or {}
                if isinstance(v, dict):
                    v["original_sql"] = new_sql
                    patch_obj["value"] = v
                else:
                    patch_obj["value"] = {"original_sql": new_sql}
            log.info("generate_patch: SQL rewrite — modified in-place for block %s", bid)
        else:
            bptr = info["ptr"]
            has_existing_ds = bool(block_for_call.get("data_source"))
            added.append({
                "op": "replace" if has_existing_ds else "add",
                "path": f"{bptr}/data_source/original_sql" if has_existing_ds else f"{bptr}/data_source",
                "value": new_sql if has_existing_ds else {"original_sql": new_sql},
            })
            log.info("generate_patch: SQL rewrite — appended new SQL patch for block %s", bid)

    return patches + added


def _generate_sql_for_block(llm, user_message: str, block: dict, catalog: dict | None,
                            existing_sql: str | None = None) -> str | None:
    """Focused LLM çağrısı — sadece SQL üret. Tek satır JSON yanıt:
    {"sql": "SELECT ..."}.

    `existing_sql` verilirse: kullanıcı mevcut SQL'i modifiye etmek istiyor
    (örn. ORDER BY ekle, WHERE değiştir). Mevcut SQL bağlam olarak gönderilir.
    """
    btype = block.get("type")
    btitle = block.get("title", "")

    # Catalog'u kısaca aktar — tabloları + kolonları
    cat_lines = []
    for dom in (catalog or {}).get("domains", []):
        for t in dom.get("tables", []):
            cols = ", ".join(c["name"] for c in (t.get("columns") or []))
            cat_lines.append(f"- {t['id']}: {cols}")
    catalog_str = "\n".join(cat_lines)

    sys = (
        "Sen bir SQL üretecisin. Sadece tek bir SELECT yaz (Oracle 19c veya DuckDB)."
        " Sadece JSON döndür: {\"sql\":\"...\"}. Başka açıklama yok."
        " Kategorik x ekseninde ('1M','3M','6M','1Y' / 'Q1','Q2' / ay adları) custom"
        " sıra için ORDER BY CASE kullan, alfabetik bırakma."
        " Sayısal yuvarlama: 'N haneye yuvarla' / 'N ondalık' / 'round' istenirse"
        " SELECT'te ROUND(<expr>, N) kullan. Oracle ve DuckDB ikisinde de ROUND vardır."
    )

    # Block tipine göre SQL şekil tavsiyesi
    shape = {
        "kpi":         "SELECT tek_sayi AS value FROM ...",
        "bar_chart":   "SELECT category, value FROM ... GROUP BY category ORDER BY value DESC",
        "line_chart":  "SELECT date_or_x, value FROM ... ORDER BY date_or_x",
        "area_chart":  "SELECT date_or_x, value FROM ... ORDER BY date_or_x",
        "pie_chart":   "SELECT label, value FROM ... GROUP BY label",
        "heatmap":     "SELECT x, y_label, value FROM ...",
        "radial_bar":  "SELECT tek_sayi AS value FROM ...",
        "data_table":  "SELECT col1, col2, ... FROM ... LIMIT 50",
    }.get(btype, "SELECT ...")

    if existing_sql:
        task_block = (
            f"# Görev\nKullanıcı bu bloğun MEVCUT SQL'ini değiştirmek istiyor. "
            f"Mevcut SQL'i AL, kullanıcının istediği değişikliği uygula, YENİ SQL döndür.\n"
            f"AYNI SQL'i geri verme — kullanıcı bir değişiklik istedi.\n\n"
            f"# Mevcut SQL\n{existing_sql}\n\n"
        )
    else:
        task_block = "# Görev\nBu bloğun veri kaynağı SQL'ini üret.\n\n"

    user = (
        f"{task_block}"
        f"# Block\nTip: {btype}\nBaşlık: {btitle}\n"
        f"Beklenen SQL şekli: {shape}\n\n"
        f"# Kullanıcının talebi\n{user_message}\n\n"
        f"# Mevcut katalog\n{catalog_str or '(katalog yok)'}\n\n"
        f"# Çıktı\n{{\"sql\":\"<SELECT ...>\"}}"
    )

    try:
        # llm.generate_patches'ı yeniden kullanmak yerine raw chat call yap
        # Ama LLMClient interface'inde tek public metot generate_patches.
        # generate_patches'i compose_user_message ile çağırmak yerine elle yap:
        import requests
        payload = {
            "messages": [
                {"role": "system", "content": sys},
                {"role": "user",   "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
            "stream": False,
        }
        if getattr(llm, "model", None):
            payload["model"] = llm.model
        if getattr(llm, "force_json", False):
            payload["response_format"] = {"type": "json_object"}

        resp = requests.post(
            llm.endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {llm.token}"},
            verify=getattr(llm, "verify_ssl", True),
            timeout=getattr(llm, "timeout", 300),
        )
        if not resp.ok:
            log.warning("SQL fallback: provider HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("SQL fallback: LLM call failed: %s", exc)
        return None

    # Parse JSON, extract sql
    import re
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to find {"sql": "..."} pattern
        m = re.search(r'\{[^}]*"sql"\s*:\s*"([^"]+)"[^}]*\}', text)
        if m:
            return m.group(1)
        log.warning("SQL fallback: cannot parse LLM output: %s", text[:200])
        return None

    sql = obj.get("sql", "")
    if isinstance(sql, str) and sql.strip():
        return sql.strip()
    return None


def _build_data_summary(state) -> dict | None:
    """Legacy basket-path summary. Kept for backward compatibility while the
    LLM transitions to SQL generation; harmless if both are present."""
    if state.session is None:
        return None
    try:
        views = state.session.loaded_views()
    except Exception as exc:
        log.warning("generate_patch: loaded_views() failed: %s", exc)
        return None
    if not views:
        return None
    try:
        with state.session.duck_conn() as conn:
            summary = summarize_views(conn, views)
    except Exception as exc:
        log.warning("generate_patch: summarize_views failed: %s", exc)
        return None

    # Annotate produced-view columns with the concept the user bound to them in
    # Hazırlık (manifest.basket[].column_concepts) + a small DISTINCT sample. The
    # LLM otherwise sees only name:type and can't tell PORTFOLIO_SIDE is concept-
    # bound → it hardcodes a WHERE value instead of offering an interactive
    # {{concept_filters}} filter. The distinct values become the filter's
    # allowed_values (the produced view has no table-doc to source them from).
    try:
        cc_by_alias = {
            b["alias"]: {str(k).upper(): v
                         for k, v in (b.get("column_concepts") or {}).items()}
            for b in ((state.manifest or {}).get("basket") or [])
            if isinstance(b, dict) and b.get("alias") and b.get("column_concepts")
        }
        if cc_by_alias:
            with state.session.duck_conn() as conn:
                for view, info in summary.items():
                    cc = cc_by_alias.get(view)
                    if not cc:
                        continue
                    for col in info.get("columns", []):
                        concept = cc.get(str(col.get("name")).upper())
                        if not concept:
                            continue
                        col["concept"] = concept
                        _attach_distinct_values(conn, view, col)
    except Exception as exc:
        log.warning("generate_patch: concept annotation failed (non-fatal): %s", exc)

    return summary


def _attach_distinct_values(conn, view: str, col: dict, cap: int = 50) -> None:
    """Attach the distinct values of a concept-bound produced-view column (low-
    cardinality dim) so the LLM can populate the dashboard filter's
    allowed_values. Capped — a column with more than ``cap`` distinct values
    isn't a sensible enum filter, so we leave allowed_values unset."""
    name = str(col.get("name") or "")
    if not name.replace("_", "").isalnum() or not str(view).replace("_", "").isalnum():
        return
    try:
        rows = conn.execute(
            f'SELECT DISTINCT "{name}" AS v FROM "{view}" '
            f'WHERE "{name}" IS NOT NULL ORDER BY v LIMIT {cap + 1}'
        ).fetchall()
    except Exception:
        return
    vals = [r[0] for r in rows]
    if 0 < len(vals) <= cap:
        col["distinct_values"] = [str(v) for v in vals]
"""
LLM client for the presentations module.

Two implementations:
- QwenClient: real OpenAI-compatible call to the corporate Qwen3.5-27B endpoint
- FakeLLM:    pattern-matching stub for offline local dev

Both expose: generate_patches(system, user_message, manifest, selected_block_id) -> (patches, explanation)
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

import requests


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


class LLMClient(Protocol):
    def generate_patches(
        self,
        system: str,
        user_message: str,
        manifest: dict,
        selected_block_id: str | None = None,
        data_summary: dict | None = None,
        catalog: dict | None = None,
    ) -> tuple[list[dict], str]: ...


# ── Real Qwen client ──────────────────────────────────────────────────────────

class QwenClient:
    """OpenAI-compatible /v1/chat/completions client.

    Used for both:
    - The corporate Qwen3.5-27B GGUF endpoint (verify_ssl=False, force_json=False).
    - Public providers like Groq, OpenRouter, NVIDIA NIM (verify_ssl=True,
      force_json=True for backends that support response_format).

    Tool calling is brittle on GGUF wrappers; we rely on system prompt + JSON
    parsing in message content instead.
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        model: str | None = None,
        timeout: int = 60,
        verify_ssl: bool = True,
        force_json: bool = False,
    ):
        # `model` opsiyonel: Qwen GGUF endpoint'i kabul etmiyor → boş bırak.
        # OpenAI / Groq / OpenRouter gibi public provider'lar için zorunlu.
        self.endpoint = endpoint
        self.token = token
        self.model = model
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.force_json = force_json

    def generate_patches(
        self,
        system,
        user_message,
        manifest,
        selected_block_id=None,
        data_summary=None,
        catalog=None,
        library=None,
        table_docs=None,
    ):
        # Embed manifest snapshot + layout summary + DuckDB data summary +
        # catalog + library summary + Phase 6.5.b table docs into the user
        # message.
        composed_user = compose_user_message(
            manifest, selected_block_id, user_message,
            data_summary=data_summary,
            catalog=catalog,
            library=library,
            table_docs=table_docs,
        )

        # Sanity check: catch runaway prompts before the server does.
        # ~4 chars per token is the standard rule of thumb for English/Turkish.
        approx_tokens = (len(system) + len(composed_user)) // 4
        if approx_tokens > 100_000:
            import logging
            logging.warning(
                "QwenClient: large prompt detected (~%d tokens, %d chars). "
                "Manifest may need pruning.",
                approx_tokens, len(system) + len(composed_user),
            )
 
        # NOTE: Qwen corporate endpoint, `model` body'de varsa reddediyor.
        # OpenAI/Groq/OpenRouter ise model field'ı bekliyor — sadece self.model
        # set edildiyse payload'a eklenir.
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": composed_user},
            ],
            "temperature": 0.2,
            "max_tokens": 2048,
            "stream": False,
        }
        if self.model:
            payload["model"] = self.model
        if self.force_json:
            payload["response_format"] = {"type": "json_object"}
 
        resp = requests.post(
            self.endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        if not resp.ok:
            body = resp.text[:1000]
            raise RuntimeError(
                f"LLM provider HTTP {resp.status_code}: {body}"
            )
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_llm_output(content)

    def complete(self, system: str, user: str, *, max_tokens: int = 1024,
                 temperature: float = 0.1) -> str:
        """Generic single-turn completion → raw message content.

        Used by non-manifest callers (e.g. the Phase 7.c binding proposer)
        that just need a system+user prompt answered. Returns the model's
        text content; the caller parses it.
        """
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if self.model:
            payload["model"] = self.model
        if self.force_json:
            payload["response_format"] = {"type": "json_object"}
        resp = requests.post(
            self.endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"LLM provider HTTP {resp.status_code}: {resp.text[:500]}")
        return resp.json()["choices"][0]["message"]["content"]


def _block_layout_summary(blocks: list[dict]) -> str:
    """Index-table for the LLM. Top-level blocks are sections; their children
    are listed indented with the path that targets them (`/blocks/N/children/M`)."""
    lines = []
    for si, section in enumerate(blocks):
        title = section.get("title", "")
        sid = section.get("id", "?")
        lock = "  🔒" if section.get("locked") else ""
        lines.append(f'▸ [/blocks/{si}]  section_header  "{title}"  (id: {sid}){lock}')
        children = section.get("children", []) or []
        for ci, child in enumerate(children):
            ctype = child.get("type", "?")
            ctitle = child.get("title", "")
            cid = child.get("id", "?")
            clock = "  🔒" if child.get("locked") else ""
            cwidth = f"  width={child.get('width')}" if child.get("width") else ""
            lines.append(
                f'    [/blocks/{si}/children/{ci}]  {ctype:11s} "{ctitle}"  (id: {cid}){cwidth}{clock}'
            )
    return "\n".join(lines) if lines else "  (boş manifest — section eklenebilir)"


def _section_insertion_indices(blocks: list[dict]) -> str:
    """For each section, the path to append a new child block."""
    if not blocks:
        return "  (manifest boş — önce section_header ekle: /blocks/-)"
    lines = []
    for si, section in enumerate(blocks):
        title = section.get("title", "")
        lines.append(
            f'  "{title}" bölümüne yeni blok ekle → /blocks/{si}/children/-'
        )
    return "\n".join(lines)


def _data_summary_section(data_summary: dict | None) -> str:
    """Render the loaded-views snapshot as a readable Turkish section."""
    if not data_summary:
        return ""

    lines = ["", "## Yüklü veri (DuckDB view'leri)"]
    lines.append("Bu veriler basket'ten çekilmiş ve DuckDB'de hazır. KPI/grafik değerlerini")
    lines.append("BU GERÇEK VERİDEN üret — değer uydurma.\n")

    for view, info in data_summary.items():
        lines.append(f"### {view}  ({info['row_count']} satır)")
        cols = ", ".join(f"{c['name']}:{c['type']}" for c in info["columns"])
        lines.append(f"Kolonlar: {cols}")

        if info.get("sample"):
            col_names = [c["name"] for c in info["columns"]]
            header = " | ".join(col_names)
            lines.append("Örnek satırlar:")
            lines.append(f"  {header}")
            for row in info["sample"]:
                lines.append("  " + " | ".join(str(v) for v in row))

        if info.get("stats"):
            lines.append("Sayısal kolonlar — özet istatistikler:")
            for col, s in info["stats"].items():
                argmax_str = ""
                if s.get("argmax"):
                    argmax_str = " | argmax: " + ", ".join(f"{k}={v}" for k, v in s["argmax"].items())
                lines.append(
                    f"  {col}: min={s['min']}, max={s['max']}, avg={s['avg']}{argmax_str}"
                )
        lines.append("")

    return "\n".join(lines)
    
def _catalog_section(catalog: dict | None) -> str:
    """Render the data catalog as a compact, prompt-friendly Turkish section.
    The LLM must ONLY use tables and columns listed here when producing SQL."""
    if not catalog:
        return ""
 
    domains = catalog.get("domains") or []
    if not domains:
        return ""
 
    lines = ["## Mevcut katalog (SQL üretirken SADECE bu tabloları/kolonları kullan)"]
    lines.append("")
    for dom in domains:
        dom_label = dom.get("label") or dom.get("id") or ""
        lines.append(f"### {dom_label}")
        for t in dom.get("tables") or []:
            tid = t.get("id", "")
            desc = t.get("desc", "")
            rows = t.get("rows", "")
            meta_bits = []
            if desc:
                meta_bits.append(desc)
            if rows:
                meta_bits.append(f"~{rows} satır")
            meta_str = f" — {' · '.join(meta_bits)}" if meta_bits else ""
            lines.append(f"**{tid}**{meta_str}")
            cols = t.get("columns") or []
            for c in cols:
                cname = c.get("name", "?")
                ctype = c.get("type", "")
                cvals = c.get("common_values") or []
                if cvals:
                    sample = ", ".join(f'"{v}"' for v in cvals[:10])
                    suffix = "…" if len(cvals) > 10 else ""
                    lines.append(
                        f"  - {cname}  `{ctype}`  (gerçek değerler: {sample}{suffix} — SQL'de bunları AYNEN kullan)"
                    )
                else:
                    lines.append(f"  - {cname}  `{ctype}`")
            filters = t.get("common_filters") or []
            if filters:
                lines.append("  Sık kullanılan filtreler:")
                for f in filters:
                    flabel = f.get("label", "")
                    fexpr = f.get("expression", "")
                    lines.append(f"    • {flabel}: `{fexpr}`")
            lines.append("")
    lines.append("")
    return "\n".join(lines)

def _manifest_for_prompt(manifest: dict) -> dict:
    """Return a copy of the manifest safe to send to the LLM.

    The on-disk manifest carries `data_source.rows` (up to 5000 rows per block)
    and `data_source.preview_rows` for every data-bound block. These can blow
    past any reasonable context window. The LLM doesn't need raw data — it
    sees the loaded views via `data_summary`. Keep only schema/SQL.

    Also drops `data_source.columns` (kept) and other small fields are kept.
    """
    if not isinstance(manifest, dict):
        return manifest

    def _strip_ds(block):
        if not isinstance(block, dict):
            return block
        out = dict(block)
        ds = out.get("data_source")
        if isinstance(ds, dict):
            slim_ds = {k: v for k, v in ds.items()
                       if k not in ("rows", "preview_rows")}
            out["data_source"] = slim_ds
        children = out.get("children")
        if isinstance(children, list):
            out["children"] = [_strip_ds(c) for c in children]
        return out

    out = dict(manifest)
    out["blocks"] = [_strip_ds(b) for b in (manifest.get("blocks") or [])]

    # uploads.sheets[].preview_rows can also be heavy; trim it.
    uploads = manifest.get("uploads") or []
    if uploads:
        slim_uploads = []
        for u in uploads:
            slim_u = dict(u)
            slim_sheets = []
            for s in (u.get("sheets") or []):
                slim_s = {k: v for k, v in s.items() if k != "preview_rows"}
                slim_sheets.append(slim_s)
            slim_u["sheets"] = slim_sheets
            slim_uploads.append(slim_u)
        out["uploads"] = slim_uploads

    return out


_BLOCK_SQL_SHAPE = {
    "kpi":         "SELECT <tek_sayi> AS value FROM ...  (TEK satır, TEK sayı)",
    "bar_chart":   "SELECT <category>, <value> FROM ... GROUP BY <category> ORDER BY <value> DESC",
    "line_chart":  "SELECT <x_or_date>, <value> FROM ... ORDER BY <x_or_date>",
    "area_chart":  "SELECT <x_or_date>, <value> FROM ... ORDER BY <x_or_date>",
    "pie_chart":   "SELECT <label>, <value> FROM ... GROUP BY <label>",
    "heatmap":     "SELECT <x>, <y_label>, <value> FROM ...",
    "radial_bar":  "SELECT <tek_sayi> AS value FROM ...  (TEK satır, TEK sayı)",
    "data_table":  "SELECT <col1>, <col2>, ... FROM ... LIMIT 50",
}


def compose_user_message(
    manifest: dict,
    selected_block_id: str | None,
    user_message: str,
    data_summary: dict | None = None,
    catalog: dict | None = None,
    library: list[dict] | None = None,  # F.5: kullanıcının görebildiği blok özetleri
    table_docs: list | None = None,     # Phase 6.5.b — rich per-table metadata
) -> str:
    blocks = manifest.get("blocks", [])
    layout = _block_layout_summary(blocks)
    sec_indices = _section_insertion_indices(blocks)

    sel_info = "yok"
    sel_shape_hint = ""
    if selected_block_id:
        from presentations.manifest import find_block_by_id
        block, path = find_block_by_id(manifest, selected_block_id)
        if block is not None:
            btype = block.get("type")
            sel_info = f"{selected_block_id} (path: {path}, type: {btype})"
            shape = _BLOCK_SQL_SHAPE.get(btype)
            if shape:
                ds = (block.get("data_source") or {})
                has_sql = bool((ds.get("original_sql") or "").strip())
                empty_note = " — ŞU AN BOŞ, doldurman bekleniyor" if not has_sql else ""
                sel_shape_hint = (
                    f"\n  Beklenen SQL şekli ({btype}{empty_note}): {shape}\n"
                    f"  → Kullanıcı blok tipini söylemese bile bu şekilde SQL üret. "
                    f"data_source.original_sql DOLU yaz."
                )

    full_json = json.dumps(_manifest_for_prompt(manifest), ensure_ascii=False, indent=2)
    data_section = _data_summary_section(data_summary)
    catalog_section = _catalog_section(catalog)
    library_section = _library_summary(library)
    table_docs_section = _table_docs_section(table_docs)

    return (
        "# Bağlam\n\n"
        f"{catalog_section}"
        f"{table_docs_section}"
        "## Blok dizilimi (index sırasıyla)\n"
        f"{layout}\n\n"
        "## Section ekleme index'leri (yeni blok ALTINA ekleme için)\n"
        f"{sec_indices}\n\n"
        f"{library_section}"
        f"## Seçili blok\n  {sel_info}{sel_shape_hint}\n"
        f"{data_section}\n"
        "## Tam manifest (JSON, referans için)\n"
        f"```json\n{full_json}\n```\n\n"
        "# Talep\n"
        f"{user_message}\n"
    )


def _table_docs_section(table_docs) -> str:
    """Render Phase 6.5.b extended table docs as an LLM-friendly section.

    Surfaces ``suggested_variable``, ``suggested_semantic_tag``, and
    ``distinct_values_sample`` per filterable column so the LLM, when it
    starts emitting Phase 6.5 blocks with :binds + variables, can pick
    consistent names + tags + value sets without making them up.

    Only included for tables that have been migrated to the extended
    schema. Pre-migration tables continue to appear in ``_catalog_section``
    with their legacy column shape (no semantic_tag hints).
    """
    if not table_docs:
        return ""

    lines = [
        "## Tablo dokümantasyonu (zengin metadata — :param üretirken kullan)",
        "",
        "Aşağıdaki tablolar Phase 6.5 değişken sistemine migrate edildi. SQL",
        "üretirken :param isimleri için **suggested_variable**'ı, blok.variables",
        "tanımında semantic_tag için **suggested_semantic_tag**'i, allowed_values",
        "için **distinct_values_sample**'i kullan.",
        "",
    ]
    for doc in table_docs:
        # Avoid pulling Pydantic in here — duck-type via attribute lookup so
        # callers can pass plain dicts too if they ever want to.
        schema = getattr(doc, "schema_name", None) or getattr(doc, "schema", "")
        table = getattr(doc, "table", "?")
        desc = getattr(doc, "description", "") or ""
        partition = getattr(doc, "partition_column", None)

        header_bits = [f"**{schema}.{table}**"]
        if desc:
            header_bits.append(desc.split("\n")[0])
        if partition:
            header_bits.append(f"partitioned: {partition}")
        lines.append(" · ".join(header_bits))

        columns = getattr(doc, "columns", {}) or {}
        # If `columns` is a Pydantic dict-of-models, .items() walks them.
        items = columns.items() if hasattr(columns, "items") else []
        for col_name, col in items:
            visible = getattr(col, "visible_in_ui", True)
            if not visible:
                continue
            ctype = getattr(col, "type", "")
            filterable = getattr(col, "filterable", False)
            filter_role = getattr(col, "filter_role", None)
            suggested_var = getattr(col, "suggested_variable", None)
            suggested_tag = getattr(col, "suggested_semantic_tag", None)
            samples = getattr(col, "distinct_values_sample", None)

            if filterable:
                bits = [f"`{col_name}` ({ctype})"]
                if filter_role:
                    bits.append(filter_role)
                if suggested_var and suggested_tag:
                    bits.append(f"→ :{suggested_var} (semantic_tag={suggested_tag})")
                if samples:
                    head = ", ".join(repr(v) for v in samples[:8])
                    bits.append(
                        f"allowed: [{head}{'…' if len(samples) > 8 else ''}]"
                    )
                lines.append("  - " + " · ".join(bits))
        lines.append("")
    return "\n".join(lines) + "\n"


def _library_summary(library: list[dict] | None) -> str:
    """Library blokların 1 satırlık özetlerini prompt'a ekle. LLM yeni blok
    talebinde bu listede uygun bir blok varsa SUGGESTION dönmeli."""
    if not library:
        return ""
    lines = ["## Blok Kütüphanesi (önerilebilir)"]
    lines.append(
        "Aşağıdaki bloklar daha önce ekibinde inşa edildi. Kullanıcı yeni blok "
        "isterse ve uygun bir tane varsa sıfırdan üretmek yerine **suggestion** sun."
    )
    for m in library[:50]:  # max 50, token sınırı
        bid = m.get("library_id", "?")
        btype = m.get("block_type", "?")
        name = m.get("name", "(adsız)")
        desc = (m.get("description") or "").strip()
        tags = ",".join(m.get("tags") or [])
        tables = ",".join(m.get("used_tables") or [])
        bits = [f"[{btype}] {name!r}"]
        if tables: bits.append(f"tables:{tables}")
        if tags: bits.append(f"tags:{tags}")
        if desc: bits.append(f"— {desc[:120]}")
        lines.append(f"- `{bid}` " + " · ".join(bits))
    lines.append("")
    return "\n".join(lines) + "\n"


def _parse_llm_output(content: str) -> tuple[list[dict], str]:
    """Tolerant JSON extraction from the LLM's text output.

    Strips ```json fences and tries to parse. If the model emits extra prose, we
    locate the first `{` and matching `}` and parse just that slice.
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Find the first JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return [], f"LLM çıktısı JSON olarak parse edilemedi: {text[:200]}", []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            return [], f"LLM çıktısı JSON olarak parse edilemedi: {exc}", []

    return (
        data.get("patches", []),
        data.get("explanation", ""),
        data.get("suggestions", []),  # F.5 — library suggestions (opsiyonel)
    )


# ── Local dev stub ────────────────────────────────────────────────────────────

class FakeLLM:
    """Pattern-matching stub for local dev. No network calls.

    Supports a small set of edits so demos work without VPN:
    - "<sayı>"           → seçili KPI'nın value'sunu o sayıyla değiştirir
    - "başlık: X" / "title: X"  → seçili bloğun başlığını X yapar
    - "metni: X" / "text: X"    → seçili narrative'in text'ini X yapar
    - "kaldır" / "sil"   → seçili bloğu kaldırır (section_header değilse)

    Diğer her şey için patches=[] + neden döner.
    """

    def generate_patches(
        self,
        system,
        user_message,
        manifest,
        selected_block_id=None,
        data_summary=None,
        catalog=None,
        library=None,
    ):
        # FakeLLM ignores data_summary/library — it's pure pattern matching.
        # Signature matches QwenClient for drop-in replacement.
        msg = user_message.strip()
        idx, block = _find_block(manifest, selected_block_id)

        # Title change
        m = re.search(r"(?:başlık|title)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE)
        if m and block is not None:
            new_title = m.group(1).strip()
            return (
                [{"op": "replace", "path": f"/blocks/{idx}/title", "value": new_title}],
                f"(yerel stub) Başlık '{block.get('title', '')}' → '{new_title}'.",
                [],
            )

        # Narrative text change
        m = re.search(r"(?:metni|text)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE | re.DOTALL)
        if m and block is not None and block.get("type") == "narrative":
            new_text = m.group(1).strip()
            return (
                [{"op": "replace", "path": f"/blocks/{idx}/config/text", "value": new_text}],
                f"(yerel stub) Narrative metni güncellendi.",
                [],
            )

        # Remove block
        if re.search(r"\b(kaldır|sil|remove|delete)\b", msg, re.IGNORECASE) and block is not None:
            if block.get("type") == "section_header":
                return [], "(yerel stub) Section header bloğu silinemez.", []
            return (
                [{"op": "remove", "path": f"/blocks/{idx}"}],
                f"(yerel stub) '{block.get('title', '')}' bloğu kaldırıldı.",
                [],
            )

        # KPI value change — find first number in message
        if block is not None and block.get("type") == "kpi":
            num_match = re.search(r"(-?\d+(?:[.,]\d+)?)", msg)
            if num_match:
                new_value = float(num_match.group(1).replace(",", "."))
                old_value = block.get("config", {}).get("value")
                return (
                    [{"op": "replace", "path": f"/blocks/{idx}/config/value", "value": new_value}],
                    f"(yerel stub) KPI değeri {old_value} → {new_value}.",
                    [],
                )

        # Meta title change (no block selected)
        if block is None:
            m = re.search(r"(?:başlık|title)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE)
            if m:
                return (
                    [{"op": "replace", "path": "/meta/title", "value": m.group(1).strip()}],
                    f"(yerel stub) Sunum başlığı güncellendi.",
                    [],
                )

        return (
            [],
            "(yerel stub) Bu talebi anlayamadım. Şunları deneyebilirsin: "
            "bir KPI seçip sayı yaz, 'başlık: X', 'metni: X', 'kaldır'. "
            "Gerçek model ofis ortamında devreye girer.",
            [],
        )

    def complete(self, system: str, user: str, **kwargs) -> str:
        """No-op stub — DEV must not fabricate concept bindings. The Phase 7.c
        proposer treats an empty ``columns`` map as "LLM had nothing to add",
        so the deterministic stages stand alone offline."""
        return '{"columns": {}}'


def _find_block(manifest: dict, block_id: str | None):
    if not block_id:
        return None, None
    for i, b in enumerate(manifest.get("blocks", [])):
        if b.get("id") == block_id:
            return i, b
    return None, None
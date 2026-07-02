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
        timeout: int = 300,
        verify_ssl: bool = True,
        force_json: bool = False,
        gen_max_tokens: int = 8192,
    ):
        # `model` opsiyonel: Qwen GGUF endpoint'i kabul etmiyor → boş bırak.
        # OpenAI / Groq / OpenRouter gibi public provider'lar için zorunlu.
        self.endpoint = endpoint
        self.token = token
        self.model = model
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.force_json = force_json
        # G1 — manifest patch üretimi max_tokens. 2048, çok-karosel/çok-blok
        # dashboard'larda JSON'u ortadan kesiyordu ("Expecting ',' delimiter…").
        # 8192 tipik bir dashboard'u rahat kapsar; instantiation'dan ayarlanabilir.
        self.gen_max_tokens = gen_max_tokens

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
            "max_tokens": self.gen_max_tokens,
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
        result = resp.json()
        choice = (result.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        patches, explanation, suggestions = _parse_llm_output(content)
        # G1 — truncation: yanıt max_tokens'a takılıp JSON ortadan kesildiyse
        # yarım patch'i UYGULAMA (manifest'i bozar) — kriptik parse hatası yerine
        # eyleme dönük mesaj. Boş-patch + açıklama: pipeline hata atmaz, kullanıcı
        # isteği küçültür.
        if choice.get("finish_reason") == "length" and not patches:
            explanation = (
                "Yanıt çok uzun olduğu için kesildi (max_tokens sınırı) → JSON "
                "tamamlanamadı. İsteği parçala (ör. karoselleri/blokları tek tek "
                "ekle) ya da daha az şey iste."
            )
        return patches, explanation, suggestions

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

    def suggest_scope_refinements(
        self,
        scope: dict,
        user_message: str,
        bound_concepts: list[dict] | None = None,
        catalog_excerpt: list[dict] | None = None,
        history: list[dict] | None = None,
        selected_alias: str | None = None,
        selected_columns: list[str] | None = None,
        selected_profile: dict | None = None,
        selected_columns_meta: list[dict] | None = None,
    ) -> dict:
        """Phase 8.f Hazırlık chat: scope refinement suggestions.

        Returns ``{"explanation": str, "suggestions": [{"kind": ..., ...}]}``
        matching the contract in PHASE_8_SPEC §5.3. One retry on invalid JSON
        with the error fed back as a follow-up turn (§10.f).

        ``selected_alias`` (Faz P): kullanıcı bir node'a tıkladıysa node-scope —
        model o node'dan ``create_python_node`` önerebilir.
        """
        system = load_prompt("scope_refine")
        composed = compose_scope_user_message(
            scope, user_message,
            bound_concepts=bound_concepts,
            catalog_excerpt=catalog_excerpt,
            history=history,
            selected_alias=selected_alias,
            selected_columns=selected_columns,
            selected_profile=selected_profile,
            selected_columns_meta=selected_columns_meta,
        )
        result = self._call_scope(system, composed)
        if result.get("_invalid"):
            # One retry — feed back the parse error so the model corrects format.
            retry_user = (
                composed
                + "\n\n# Önceki cevabın JSON parse edilemedi\n"
                + f"Hata: {result['_invalid']}\n"
                + "Lütfen SADECE JSON döndür — markdown, prose ya da code fence yok.\n"
            )
            result = self._call_scope(system, retry_user)
        return {
            "explanation": result.get("explanation", ""),
            "suggestions": result.get("suggestions", []) or [],
        }

    def _call_scope(self, system: str, user: str) -> dict:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
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
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_scope_output(content)


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
        col_bits = []
        has_concept = False
        for c in info["columns"]:
            if c.get("concept"):
                bit = f"{c['name']}:{c['type']} → concept: {c['concept']}"
                if c.get("distinct_values"):
                    vals = ", ".join(str(v) for v in c["distinct_values"][:30])
                    bit += f" [değerler: {vals}]"
                col_bits.append(bit)
                has_concept = True
            else:
                col_bits.append(f"{c['name']}:{c['type']}")
        lines.append(f"Kolonlar: {', '.join(col_bits)}")
        if has_concept:
            lines.append(
                "  ↑ '→ concept: X' işaretli kolonlar bu ÜRETİLMİŞ tabloda bir concept'e "
                "bağlı → interaktif filtreye uygun. Kullanıcı bu kolona göre filtre/değer "
                "isterse SQL'e HARDCODE ETME: WHERE'e {{concept_filters}} koy ve "
                "/filters/- patch'i at (semantic_tag=X, allowed_values=[değerler: ...] "
                "listesi)."
            )

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


# ── Phase 8.f — scope refinement helpers ─────────────────────────────────────

def compose_scope_user_message(
    scope: dict,
    user_message: str,
    bound_concepts: list[dict] | None = None,
    catalog_excerpt: list[dict] | None = None,
    history: list[dict] | None = None,
    selected_alias: str | None = None,
    selected_columns: list[str] | None = None,
    selected_profile: dict | None = None,
    selected_columns_meta: list[dict] | None = None,
) -> str:
    """Render the user-side payload for `suggest_scope_refinements`.

    Layout: scope summary → bound concepts → catalog excerpt → chat history →
    user's latest message. Each section is kept short — the LLM only needs
    enough to ground its suggestions, not the full scope JSON dump.
    """
    parts: list[str] = []

    # 1. Scope summary — just what affects suggestion validity.
    basket = scope.get("basket") or []
    pinned = (scope.get("filters") or {}).get("pinned") or []
    interactive = (scope.get("filters") or {}).get("interactive") or []
    joins = scope.get("joins") or []

    parts.append("## Mevcut scope")
    if basket:
        parts.append("**Basket (basket aliasları + kaynak):**")
        for b in basket:
            alias = b.get("alias", "?")
            if b.get("table_ref"):
                ref = b["table_ref"]
                src = f"{ref.get('schema','')}.{ref.get('name','')}"
            elif b.get("derivation"):
                d = b["derivation"]
                src = f"derived from {d.get('source_alias','?')} (group_by={d.get('group_by',[])})"
            else:
                src = "(boş)"
            cols = (b.get("projection") or {}).get("columns") or []
            include_all = (b.get("projection") or {}).get("include_all")
            cols_summary = "include_all" if include_all else f"{len(cols)} kolon"
            parts.append(f"- `{alias}` ← {src} · projection: {cols_summary}")
    else:
        parts.append("(boş — kullanıcı önce tablo eklemeli — Stage 1)")

    if pinned:
        parts.append("\n**Pinned filters:**")
        for f in pinned:
            parts.append(f"- `{f.get('id','?')}` concept={f.get('concept')} op={f.get('op')} value/values/from-to={_filter_value_repr(f)}")
    if interactive:
        parts.append("\n**Interactive filters:**")
        for f in interactive:
            parts.append(f"- `{f.get('id','?')}` concept={f.get('concept')} op={f.get('op')} default={f.get('default_values')}")
    if joins:
        parts.append("\n**Joins:**")
        for j in joins:
            l = j.get("left") or {}
            r = j.get("right") or {}
            parts.append(f"- `{j.get('id','?')}` {l.get('alias')}.{l.get('column')} ↔ {r.get('alias')}.{r.get('column')} ({j.get('kind','?')})")

    # 1b. Seçili node (node-scope) — kullanıcı bir node'a tıkladıysa odak budur.
    # create_python_node önerisi bu node'u kaynak (input_node_df) alır.
    if selected_alias:
        sel = next((b for b in basket if b.get("alias") == selected_alias), None)
        sel_kind = (sel.get("derivation") or {}).get("kind") if sel else None
        parts.append(f"\n## Seçili node (ODAK): `{selected_alias}`")
        if sel is not None:
            if sel.get("table_ref"):
                ref = sel["table_ref"]
                parts.append(f"- kaynak: Oracle tablosu {ref.get('schema','')}.{ref.get('name','')}")
            elif sel.get("derivation"):
                parts.append(f"- kaynak: türetilmiş ({sel_kind})")
            elif sel.get("sql"):
                parts.append("- kaynak: manuel SQL")
        # Öneri 1 — kolon TİPLERİNİ ve örnek değerleri ver (yalnız isim değil).
        # LLM `create_python_node` kodunu tipe göre yazsın: tarih kolonunda `.dt`,
        # integer-ay sanıp `.map()` ile sessizce NaN üretmesin. Profil yoksa
        # (lazy/materialise edilmemiş node) eski davranışa düş: düz isim listesi.
        if selected_profile and selected_profile.get("columns"):
            rc = selected_profile.get("row_count")
            if isinstance(rc, int):
                parts.append(f"- satır sayısı: {rc:,}".replace(",", "."))
            parts.append("- kolonlar (ad : tip — örnek değerler; tipleri VARSAYMA, buradan al):")
            for c in selected_profile["columns"]:
                nm, dt, ex = c.get("name", "?"), c.get("dtype", "?"), c.get("sample", "")
                parts.append(f"  - `{nm}` : {dt}" + (f" — {ex}" if ex else ""))
            # Profil detayı cap'lenmiş olabilir — kalan kolon ADLARI da listeye
            # girsin. Görünmeyen kolon "yok" sayılıyordu (geniş tablolarda
            # kullanıcının istediği gerçek kolon reddediliyordu).
            extra = selected_profile.get("extra_columns") or []
            if extra:
                parts.append("- diğer kolonlar (detaysız, ama MEVCUTLAR): "
                             + ", ".join(f"`{c}`" for c in extra))
        elif selected_columns:
            parts.append("- kolonlar: " + ", ".join(f"`{c}`" for c in selected_columns))
        # M3 (madde 9) — kolon DOKÜMANI (ad + concept + açıklama): kullanıcı kolon
        # adını yanlış / açıklayıcı / Türkçe yazsa bile GERÇEK kolona eşleyebilsin
        # (profil dtype+örnek verir; bu, anlam/semantik eşlemesi içindir).
        if selected_columns_meta:
            documented = [c for c in selected_columns_meta
                          if c.get("concept") or (c.get("description") or "").strip()]
            if documented:
                parts.append("- kolon dokümanı (yanlış/açıklayıcı yazılan kolonu BURADAN gerçek ada eşle):")
                for c in documented:
                    nm = c.get("name", "?")
                    cn = c.get("concept")
                    ds = (c.get("description") or "").strip()
                    bits = [f"  - `{nm}`"]
                    if cn:
                        bits.append(f"[{cn}]")
                    if ds:
                        bits.append("— " + ds[:160])
                    parts.append(" ".join(bits))
        if sel_kind == "python":
            # ODAK zaten bir python node → MEVCUT script'i DÜZENLE (yeni node yaratma).
            cur_code = (sel.get("derivation") or {}).get("python_code") or ""
            parts.append(
                f"- Bu node ZATEN bir Python node. Kullanıcı dönüşümü değiştirmek "
                f"isterse → `edit_python_node` öner (alias=`{selected_alias}`, "
                "tüm güncel python_code ile). YENİ node ÜRETME."
            )
            if cur_code:
                parts.append("- mevcut script:\n```\n" + cur_code[:1500] + "\n```")
        else:
            parts.append(
                "- Kullanıcı bu node üzerinde bir VERİ İŞLEMİ (dönüşüm, hesaplama, "
                "pivot, temizlik, çok-adımlı mantık) istiyorsa → `create_python_node` "
                "öner (Python ile yeni node). Kaynak HER ZAMAN bu seçili node olur."
            )

    # 2. Bound concepts — the legal set for filter suggestions.
    if bound_concepts:
        parts.append("\n## Bağlı concept'ler (filter önerirken sadece bunları kullan)")
        for bc in bound_concepts:
            tables = ", ".join(bc.get("bound_in") or [])
            parts.append(f"- `{bc.get('concept','?')}` → {tables}")
    else:
        parts.append("\n## Bağlı concept'ler\n(yok — filter önerisi üretme; clarify et)")

    # 3. Catalog excerpt — column names + common values for each basket table.
    if catalog_excerpt:
        parts.append("\n## Tablo katalog özeti")
        for t in catalog_excerpt:
            tid = t.get("id", "?")
            desc = t.get("desc") or ""
            parts.append(f"**{tid}** — {desc}")
            cols = t.get("columns") or []
            for c in cols[:30]:
                bits = [f"`{c.get('name','?')}`"]
                ctype = c.get("type")
                if ctype:
                    bits.append(f"({ctype})")
                if c.get("key"):
                    bits.append("[key]")
                cv = c.get("common_values")
                if cv:
                    bits.append("vals: " + ", ".join(str(x) for x in cv[:5]))
                parts.append("  - " + " ".join(bits))
            # 30+ kolonlu tablolarda kalan kolon ADLARINI da ver — sessiz
            # kesme LLM'e "böyle bir kolon yok" dedirtiyordu (64 kolonlu
            # rezervasyon tablolarında kullanıcı talebi yanlış reddediliyordu).
            if len(cols) > 30:
                rest = [f"`{c.get('name','?')}`" for c in cols[30:]]
                parts.append("  - diğer kolonlar (MEVCUT): " + ", ".join(rest))

    # 4. Chat history (last N turns, max ~6).
    if history:
        parts.append("\n## Önceki mesajlar")
        for turn in history[-6:]:
            role = turn.get("role", "user")
            content = (turn.get("content") or "")[:300]
            parts.append(f"- **{role}:** {content}")

    parts.append("\n# Yeni talep")
    parts.append(user_message)
    return "\n".join(parts)


def _filter_value_repr(f: dict) -> str:
    if f.get("from") is not None or f.get("to") is not None:
        return f"{f.get('from','')}…{f.get('to','')}"
    if f.get("values"):
        return repr(f["values"][:5])
    if f.get("value") is not None:
        return repr(f["value"])
    return "(none)"


def _parse_scope_output(content: str) -> dict:
    """Tolerant JSON extraction for the scope-refinement contract.

    On parse failure returns ``{"_invalid": "<error>"}`` so the caller can
    trigger a single retry with the error fed back to the model (§10.f).
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"_invalid": f"no JSON object in output (snippet: {text[:200]!r})"}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            return {"_invalid": str(exc)}
    if not isinstance(data, dict):
        return {"_invalid": "top-level JSON must be an object"}
    sugg = data.get("suggestions")
    if sugg is not None and not isinstance(sugg, list):
        return {"_invalid": "`suggestions` must be a list"}
    return data


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
        table_docs=None,
    ):
        # FakeLLM ignores data_summary/library/table_docs — pure pattern matching.
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

    # ── Phase 9.c — discovery proposals (offline keyword matching) ──────────
    def propose_tables(
        self,
        *,
        user_request: str,
        catalog_entries,
        current_basket=None,
        chat_history=None,
        user_department=None,
    ) -> dict:
        """Offline-only table proposer for the Keşif chat.

        Keyword match against table names + descriptions + bound concepts,
        with a small same-department bonus. Returns the §5.3 shape so the
        discovery client's _shape_result can consume it directly.
        """
        msg = (user_request or "").lower().strip()
        if not msg:
            return {
                "explanation": "(yerel stub) Ne aradığını birkaç anahtar kelimeyle yazar mısın?",
                "proposals": [],
                "highlight_graph_node_ids": [],
            }

        # Tokens to look for in name + desc + concept list. Greedy split on
        # whitespace + simple Turkish suffix trimming. Real Qwen will do this
        # vastly better; this is just enough for dev demos to feel alive.
        #
        # Tiny TR→EN bridge: when the user types "mevduat" we also want to
        # match "deposit" inside table names like DEPOSITS_DAILY. Keep this
        # small — it's a dev stub, not a real translator.
        TR_EN_BRIDGE = {
            "mevduat": "deposit",     "mevduatlar": "deposit",
            "kredi":   "loan",        "krediler":   "loan",
            "şube":    "branch",      "şubeler":    "branch",
            "müşteri": "customer",    "müşteriler": "customer",
            "faiz":    "rate",        "oran":       "rate",
            "tahvil":  "bond",        "bono":       "bond",
            "döviz":   "fx",          "kur":        "fx",
            "rakip":   "competitor",  "rakipler":   "competitor",
            "vade":    "maturity",    "ürün":       "product",
            "hesap":   "account",     "ödeme":      "payment",
            "işlem":   "transaction", "risk":       "risk",
            "likidite":"liquidity",   "gelir":      "income",
            "tarih":   "date",        "günlük":     "daily",
        }
        raw_tokens = [t for t in re.split(r"[\s,.\?!]+", msg) if len(t) >= 3]
        tokens: list[str] = []
        for t in raw_tokens:
            tokens.append(t)
            if t in TR_EN_BRIDGE:
                tokens.append(TR_EN_BRIDGE[t])
        skip = set(current_basket and (b.get("table") for b in current_basket) or [])

        # Name matches outweigh description matches; concept bindings count
        # as medium-strong. Tiebreak: more distinct tokens matched > one
        # token matched many times.
        scored = []
        for entry in catalog_entries:
            tid = f"{entry.schema_name}.{entry.name}"
            if tid in skip:
                continue
            name = entry.name.lower()
            desc = (entry.description or "").lower()
            concepts = " ".join(entry.concepts_bound or []).lower()

            score = 0.0
            distinct_hits = 0
            for t in tokens:
                if t in name:
                    score += 1.5
                    distinct_hits += 1
                elif t in concepts:
                    score += 1.0
                    distinct_hits += 1
                elif t in desc:
                    score += 0.5
                    distinct_hits += 1
            if distinct_hits > 1:
                score += 0.3 * (distinct_hits - 1)   # coverage bonus
            if entry.department and entry.department == user_department:
                score += 0.5
            if score > 0:
                scored.append((score, entry))

        # Score desc, then name asc for stable tie-break (alphabetical).
        scored.sort(key=lambda x: (-x[0], x[1].name))
        top = scored[:5]

        proposals = [
            {
                "schema": e.schema_name,
                "name": e.name,
                "rationale": (
                    f"İsim ve/veya açıklama '{msg[:60]}' talebine uyuyor."
                    if score > 0.5 else "Departman eşleşmesi."
                ),
                "match_score": min(1.0, 0.5 + score * 0.15),
                "suggested_companion": None,
            }
            for score, e in top
        ]
        highlights = [f"{p['schema']}.{p['name']}" for p in proposals]

        if proposals:
            head = (
                "(yerel stub) Anahtar kelimelere göre şu tablolar uygun "
                "görünüyor — sıralama match_score'a göre."
            )
            return {
                "explanation": head,
                "proposals": proposals,
                "highlight_graph_node_ids": highlights,
            }
        return {
            "explanation": (
                "(yerel stub) Bu anahtar kelimelerle eşleşen tablo bulamadım. "
                "Başka kelimelerle dener misin?"
            ),
            "proposals": [],
            "highlight_graph_node_ids": [],
        }

    # ── Phase 8.f — scope refinement (pattern matched, offline-safe) ────────
    def suggest_scope_refinements(
        self,
        scope: dict,
        user_message: str,
        bound_concepts: list[dict] | None = None,
        catalog_excerpt: list[dict] | None = None,
        history: list[dict] | None = None,
        selected_alias: str | None = None,
        selected_columns: list[str] | None = None,
        selected_profile: dict | None = None,
        selected_columns_meta: list[dict] | None = None,
    ) -> dict:
        """Offline canned responses keyed on user intent.

        Covers the suggestion kinds so the demo flow is exercisable without
        VPN. Pattern matching is intentionally simple — the real model lives
        on the office machine; this stub just keeps the UX alive in dev.
        """
        msg = (user_message or "").strip().lower()

        # Faz P — node-scope: kullanıcı bir node'a tıklıysa ve bir veri işlemi
        # istiyorsa python öner. ODAK zaten python node ise MEVCUT'u düzenle.
        if selected_alias and re.search(
            r"python|script|hesapla|dönüş|donus|pivot|topla|oran|yeni\s*kolon|"
            r"kolon\s*ekle|normalize|temizle|grupla|birleştir|ortalama|kümülat|değiştir|guncelle|güncelle",
            msg,
        ):
            sel = next((b for b in (scope.get("basket") or [])
                        if b.get("alias") == selected_alias), None)
            is_python = (sel.get("derivation") or {}).get("kind") == "python" if sel else False
            if is_python:
                return {
                    "explanation": (
                        f"(yerel stub) '{selected_alias}' python node'unun script'ini "
                        "güncelliyorum."
                    ),
                    "suggestions": [{
                        "kind": "edit_python_node",
                        "alias": selected_alias,
                        "python_code": (
                            "# input_node_df: kaynağın DataFrame'i (pd, np hazır)\n"
                            "output_node_df = input_node_df.copy()\n"
                        ),
                        "rationale": "Mevcut Python dönüşümünü güncelle.",
                    }],
                }
            return {
                "explanation": (
                    f"(yerel stub) '{selected_alias}' node'undan Python ile yeni bir "
                    "node üretebilirsin — input_node_df girişi, output_node_df çıkışı."
                ),
                "suggestions": [{
                    "kind": "create_python_node",
                    "source_alias": selected_alias,
                    "new_alias": f"{selected_alias}_py",
                    "python_code": (
                        "# input_node_df: kaynağın DataFrame'i (pd, np hazır)\n"
                        "output_node_df = input_node_df.copy()\n"
                    ),
                    "rationale": "Prosedürel dönüşüm için Python node.",
                }],
            }
        basket = scope.get("basket") or []
        aliases = [b.get("alias") for b in basket if b.get("alias")]
        raw_aliases = [b.get("alias") for b in basket if b.get("table_ref") and b.get("alias")]
        bound = {bc.get("concept") for bc in (bound_concepts or [])}
        existing_pinned_concepts = {f.get("concept") for f in (scope.get("filters") or {}).get("pinned", [])}

        def _has_words(*ws: str) -> bool:
            return any(w in msg for w in ws)

        # Bilgi soruları — kataloglu tablolar hakkında soru (no suggestion,
        # just an answer). "Mevduat tabloları neler?", "Hangi tablolar var?",
        # "NII verileri" gibi pattern'lar.
        is_info_query = re.search(
            r"\b(ne|neler|hangi|nedir|var\s*m[ıi])\b.*\btablo|\btablo\w*\b.*\b(ne|neler|hangi|nedir|var\s*m[ıi])\b|tablolar\w*\s*$",
            msg,
        )
        if is_info_query and catalog_excerpt:
            # Topic keywords narrow the listing. Without a topic, list
            # everything (up to a cap).
            topic_keys = {
                "mevduat": ("mevduat", "deposit"),
                "nii":     ("nii", "faiz", "interest", "rate"),
                "sektor":  ("rakip", "competitor", "sekt", "market"),
            }
            matched_keys = [k for k, kws in topic_keys.items() if any(w in msg for w in kws)]
            lines = []
            for t in catalog_excerpt:
                tid = t.get("id") or ""
                desc = (t.get("desc") or "").strip()
                hay = (tid + " " + desc).lower()
                if matched_keys and not any(w in hay for k in matched_keys for w in topic_keys[k]):
                    continue
                lines.append(f"- **{tid}**: {desc}")
            if lines:
                header = ("Mevduatla ilgili tablolar:" if "mevduat" in matched_keys
                          else "NII / Faiz tabloları:" if "nii" in matched_keys
                          else "Sektör / Rakip tabloları:" if "sektor" in matched_keys
                          else "Katalogda mevcut tablolar:")
                return {
                    "explanation": header + "\n" + "\n".join(lines[:10]),
                    "suggestions": [],
                }
            return {
                "explanation": "Aradığın kriterlerde tablo bulamadım. Sol panelden kategorileri açıp inceleyebilirsin.",
                "suggestions": [],
            }

        # Tablo eklemek (reject — Stage 1's job). Match a broad "table … add"
        # / "add … table" pattern so common phrasings ("loans tablosunu da
        # ekle", "yeni tablo ekle") all land here.
        if re.search(r"tablo\w*\b.*\bekle\b|\bekle\w*\b.*\btablo", msg):
            return {
                "explanation": "Yeni tablo eklemek Hazırlık'ta değil — soldaki katalog panelinden seçersen basket'e otomatik girecek (Keşif aşaması).",
                "suggestions": [],
            }

        # Q4 / tarih pin.
        if _has_words("q4", "çeyrek", "son çeyrek", "kilitle", "sabitle") and "as_of_time" in bound and "as_of_time" not in existing_pinned_concepts:
            return {
                "explanation": "Tarihi Q4 2025'e pin'lemeni öneririm — Sunum'da kimse değiştiremez.",
                "suggestions": [{
                    "kind": "add_filter",
                    "mode": "pinned",
                    "concept": "as_of_time",
                    "op": "between",
                    "from": "2025-10-01",
                    "to": "2025-12-31",
                    "applies_to": [],
                    "rationale": "Pin'li tarih scope'a sabitlenir.",
                }],
            }

        # Currency filter.
        if _has_words("try", "tl cinsi", "tl cinsinden", "currency", "para birimi") and "currency" in bound:
            return {
                "explanation": "Sadece TL hesaplar üzerinde durmak istiyorsan currency'yi TRY'ye filtrele.",
                "suggestions": [{
                    "kind": "add_filter",
                    "mode": "pinned",
                    "concept": "currency",
                    "op": "in",
                    "values": ["TRY"],
                    "applies_to": [],
                    "rationale": "Çoklu para birimi karışıklığını önler.",
                }],
            }

        # Agregasyon → Python node (politika: tek-kaynak dönüşüm HER ZAMAN
        # Python; create_aggregate kaldırıldı, apply reddeder — stub da
        # gerçek modelle aynı sözleşmeyi üretmeli).
        agg_match = re.search(r"(şube|branch|currency|segment)\s+(?:baz|göre).*(topla|toplam|sum|agg|aggregate)", msg)
        if not agg_match and ("aggregate" in msg or "agregat" in msg or "topla" in msg) and raw_aliases:
            agg_match = re.search(r"(şube|branch|currency|segment)", msg)
        if agg_match and raw_aliases:
            dim = agg_match.group(1)
            col_map = {"şube": "BRANCH_CODE", "branch": "BRANCH_CODE", "currency": "CUR", "segment": "SEGMENT"}
            group_col = col_map.get(dim, "BRANCH_CODE")
            src = raw_aliases[0]
            measure_col = "BALANCE_TRY" if "balance_try" in str(catalog_excerpt).lower() else "TRY_BALANCE"
            new_alias = f"{src}_by_{group_col.lower()}"
            code = (
                f"g = input_node_df.groupby('{group_col}', as_index=False)"
                f".agg(SUM_{measure_col}=('{measure_col}', 'sum'))\n"
                f"output_node_df = g.sort_values('SUM_{measure_col}', ascending=False)\n"
            )
            return {
                "explanation": f"`{src}` tablosunu `{group_col}` bazında toplayan bir Python node oluşturuyorum.",
                "suggestions": [{
                    "kind": "create_python_node",
                    "source_alias": src,
                    "new_alias": new_alias,
                    "python_code": code,
                    "rationale": f"{dim} seviyesinde toplam (tek-kaynak agregasyon → Python).",
                }],
            }

        # Calculated column / join + fark hesaplama. Match phrasings like
        # "iki tabloyu join'le ve fark hesapla", "rakip oranı ile bizim oran
        # farkını al", "X-Y hesapla". Needs at least 2 raw basket aliases.
        is_calc_intent = re.search(
            r"\b(fark|gap|orad?n)\b|\b(hesapla|calc)\w*\b|[a-z_]+\s*-\s*[a-z_]+",
            msg,
        )
        if is_calc_intent and len(raw_aliases) >= 2:
            a, b = raw_aliases[0], raw_aliases[1]
            # Pick a column name that appears in BOTH aliases as a join key
            # candidate. catalog_excerpt holds the per-table column lists.
            join_col = None
            try:
                cols_by_alias = {}
                aliases_by_tid = {it.get("alias"): (it.get("table_ref") or {}) for it in basket}
                tid_by_alias = {al: f"{r.get('schema','')}.{r.get('name','')}".strip(".") for al, r in aliases_by_tid.items()}
                col_sets = {}
                for t in (catalog_excerpt or []):
                    cols = [c.get("name") for c in (t.get("columns") or [])]
                    col_sets[t.get("id")] = set(cols)
                shared = col_sets.get(tid_by_alias.get(a), set()) & col_sets.get(tid_by_alias.get(b), set())
                # Prefer a key-flagged column if one's a key in either side.
                preferred = ["BRANCH_CODE", "DATE", "DAT", "CUSTOMER_NUMBER"]
                join_col = next((c for c in preferred if c in shared), next(iter(shared), None))
            except Exception:
                join_col = None
            if join_col:
                # Alias regex caps at 40 chars — truncate when both sources
                # have long names. Trailing "_calc" is the suffix we keep.
                new_alias = (f"{a}_vs_{b}_calc")[:40].rstrip("_")
                return {
                    "explanation": (
                        f"'{a}' ve '{b}' tablolarını '{join_col}' kolonu üzerinden "
                        "birleştirip iki kaynaktaki sayısal değerlerin farkını "
                        "veren bir hesaplama tablosu öneriyorum. Sonuç tabloda "
                        "tek bir kolon ('DIFF') olacak; gerçek kolon adlarını "
                        "(AMOUNT/BALANCE_TRY/RATE…) düzeltmek isteyebilirsin."
                    ),
                    "suggestions": [{
                        "kind": "create_calculation",
                        "new_alias": new_alias,
                        "source_aliases": [a, b],
                        "join_keys": [{
                            "left_alias": a, "left_column": join_col,
                            "right_alias": b, "right_column": join_col,
                        }],
                        "columns": [{
                            "name": "DIFF",
                            "expr": f"{a}.AMOUNT - {b}.AMOUNT",
                        }],
                        "rationale": (
                            "Örnek expr — AMOUNT yerine BALANCE_TRY / RATE / "
                            "INTEREST_RATE gibi gerçek değer kolonlarını koy."
                        ),
                    }],
                }

        # Empty / belirsiz.
        return {
            "explanation": (
                "(yerel stub) Şu örneklerden birini deneyebilirsin: "
                "'Q4 2025'e kilitle', 'TL'ye filtrele', 'şube bazında topla', "
                "'iki tablonun farkını hesapla', 'mevduat tabloları neler'. "
                "Gerçek model ofis ortamında devreye girer."
            ),
            "suggestions": [],
        }


def _find_block(manifest: dict, block_id: str | None):
    if not block_id:
        return None, None
    for i, b in enumerate(manifest.get("blocks", [])):
        if b.get("id") == block_id:
            return i, b
    return None, None
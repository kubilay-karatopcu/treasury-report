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
        model: str,
        token: str,
        timeout: int = 60,
        verify_ssl: bool = True,
        force_json: bool = False,
    ):
        self.endpoint = endpoint
        self.model = model
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.force_json = force_json

    def generate_patches(self, system, user_message, manifest, selected_block_id=None, data_summary=None):
        # Embed manifest snapshot + layout summary + DuckDB data summary into the
        # user message. The system prompt stays static (cache-friendly).
        composed_user = compose_user_message(manifest, selected_block_id, user_message, data_summary)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": composed_user},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        if self.force_json:
            # OpenAI-compatible JSON mode (Groq, OpenRouter, OpenAI, etc).
            payload["response_format"] = {"type": "json_object"}

        resp = requests.post(
            self.endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        if not resp.ok:
            # Surface the provider's actual error so we can diagnose 400/429 etc.
            body = resp.text[:1000]
            raise RuntimeError(
                f"LLM provider HTTP {resp.status_code}: {body}"
            )
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_llm_output(content)


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


def compose_user_message(
    manifest: dict,
    selected_block_id: str | None,
    user_message: str,
    data_summary: dict | None = None,
) -> str:
    blocks = manifest.get("blocks", [])
    layout = _block_layout_summary(blocks)
    sec_indices = _section_insertion_indices(blocks)

    sel_info = "yok"
    if selected_block_id:
        from presentations.manifest import find_block_by_id
        block, path = find_block_by_id(manifest, selected_block_id)
        if block is not None:
            sel_info = f"{selected_block_id} (path: {path}, type: {block.get('type')})"

    full_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    data_section = _data_summary_section(data_summary)

    return (
        "# Bağlam\n\n"
        "## Blok dizilimi (index sırasıyla)\n"
        f"{layout}\n\n"
        "## Section ekleme index'leri (yeni blok ALTINA ekleme için)\n"
        f"{sec_indices}\n\n"
        f"## Seçili blok\n  {sel_info}\n"
        f"{data_section}\n"
        "## Tam manifest (JSON, referans için)\n"
        f"```json\n{full_json}\n```\n\n"
        "# Talep\n"
        f"{user_message}\n"
    )


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
            return [], f"LLM çıktısı JSON olarak parse edilemedi: {text[:200]}"
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            return [], f"LLM çıktısı JSON olarak parse edilemedi: {exc}"

    return data.get("patches", []), data.get("explanation", "")


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

    def generate_patches(self, system, user_message, manifest, selected_block_id=None, data_summary=None):
        # FakeLLM ignores data_summary — it's pure pattern matching against the
        # user's text. The signature matches QwenClient for drop-in replacement.
        msg = user_message.strip()
        idx, block = _find_block(manifest, selected_block_id)

        # Title change
        m = re.search(r"(?:başlık|title)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE)
        if m and block is not None:
            new_title = m.group(1).strip()
            return (
                [{"op": "replace", "path": f"/blocks/{idx}/title", "value": new_title}],
                f"(yerel stub) Başlık '{block.get('title', '')}' → '{new_title}'.",
            )

        # Narrative text change
        m = re.search(r"(?:metni|text)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE | re.DOTALL)
        if m and block is not None and block.get("type") == "narrative":
            new_text = m.group(1).strip()
            return (
                [{"op": "replace", "path": f"/blocks/{idx}/config/text", "value": new_text}],
                f"(yerel stub) Narrative metni güncellendi.",
            )

        # Remove block
        if re.search(r"\b(kaldır|sil|remove|delete)\b", msg, re.IGNORECASE) and block is not None:
            if block.get("type") == "section_header":
                return [], "(yerel stub) Section header bloğu silinemez."
            return (
                [{"op": "remove", "path": f"/blocks/{idx}"}],
                f"(yerel stub) '{block.get('title', '')}' bloğu kaldırıldı.",
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
                )

        # Meta title change (no block selected)
        if block is None:
            m = re.search(r"(?:başlık|title)\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", msg, re.IGNORECASE)
            if m:
                return (
                    [{"op": "replace", "path": "/meta/title", "value": m.group(1).strip()}],
                    f"(yerel stub) Sunum başlığı güncellendi.",
                )

        return (
            [],
            "(yerel stub) Bu talebi anlayamadım. Şunları deneyebilirsin: "
            "bir KPI seçip sayı yaz, 'başlık: X', 'metni: X', 'kaldır'. "
            "Gerçek model ofis ortamında devreye girer.",
        )


def _find_block(manifest: dict, block_id: str | None):
    if not block_id:
        return None, None
    for i, b in enumerate(manifest.get("blocks", [])):
        if b.get("id") == block_id:
            return i, b
    return None, None

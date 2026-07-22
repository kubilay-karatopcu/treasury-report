"""LLM doc-proposer — Süreç Düzenlileştirme W3.

Bir sürecin (ve bileşen bloklarının) dökümantasyon TASLAĞINI LLM'den ister.
Sözleşme (docs/PROCESS_REGULARIZATION_PLAN.md §3.5 W3):

- Öneri EFEMERDİR: hiçbir yere otomatik yazılmaz; düzenleme formunda alan alan
  gösterilir, kullanıcı "Taslağı kullan" dedikçe textarea'ya dolar ve normal
  W1 kaydıyla (yeni overlay versiyonu) kalıcılaşır. Auto-publish YOK.
- Prompt dosyada yaşar (``presentations/prompts/doc_proposal.txt``), asla inline.
- Tolerant JSON çıkarma: ``concepts/inference/llm_proposer._extract_json``
  deseninin kopyası (modül flask'sız/bağımsız kalsın diye lokal).
- DEV (FakeLLM boş dönerse): deterministik stub taslak üretilir ki onay akışı
  offline test edilebilsin — stub metinleri "(DEV taslağı)" ile işaretlidir.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DOC_FIELDS = ("purpose", "business_context", "decision_support", "known_limitations")
_PROMPT_PATH = Path(__file__).parents[1] / "prompts" / "doc_proposal.txt"


def _system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    """İlk dengeli JSON nesnesini çek (code fence / prose toleranslı)."""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def build_user_prompt(process: dict[str, Any]) -> str:
    """``get_process()`` çıktısından LLM bağlam metni kurar.

    Yalnız elde olan bilgi verilir: süreç meta + mevcut dökümantasyon +
    blok başlıkları/mevcut blok dökümanları. Tablo dokümanları / mart CEC
    bağlamı ileride eklenecek (ETL D3)."""
    lines: list[str] = []
    lines.append(f"SÜREÇ: {process.get('label')} (id: {process.get('id')})")
    if process.get("desc"):
        lines.append(f"Kısa açıklama: {process['desc']}")
    lines.append(f"Tür: {process.get('source_kind', 'custom')} (elle inşa edilmiş canlı pano)")
    doc = process.get("documentation") or {}
    filled = {f: doc.get(f) for f in _DOC_FIELDS if (doc.get(f) or "").strip()}
    if filled:
        lines.append("Mevcut süreç dökümantasyonu (geliştir, çelişme):")
        for f, v in filled.items():
            lines.append(f"  - {f}: {v}")
    blocks = process.get("blocks") or []
    if blocks:
        lines.append("BLOKLAR:")
        for b in blocks:
            lines.append(f"- id: {b.get('id')} · başlık: {b.get('title')}")
            cr = b.get("custom_render") or {}
            if cr.get("page"):
                lines.append(f"  sayfa: {cr['page']}"
                             + (f" · bileşen: {cr['anchor']}" if cr.get("anchor") else ""))
            bdoc = b.get("documentation") or {}
            for f in _DOC_FIELDS:
                if (bdoc.get(f) or "").strip():
                    lines.append(f"  mevcut {f}: {bdoc[f]}")
    return "\n".join(lines)


def _clean_side(raw: dict | None) -> dict:
    raw = raw or {}
    out = {}
    for f in _DOC_FIELDS:
        v = raw.get(f)
        out[f] = v.strip() if isinstance(v, str) and v.strip() else None
    return out


def _stub_proposal(process: dict[str, Any]) -> dict[str, Any]:
    """DEV stub — offline onay akışı testi için deterministik taslak."""
    label = process.get("label", process.get("id", ""))
    mk = lambda alan: f"{label} — {alan} (DEV taslağı; prod'da Qwen üretir)."
    return {
        "documentation": {
            "purpose": mk("amaç"),
            "business_context": mk("iş bağlamı"),
            "decision_support": mk("karar desteği"),
            "known_limitations": mk("bilinen sınırlar"),
        },
        "blocks_documentation": {
            (b.get("id") or ""): {
                "purpose": f"{b.get('title', b.get('id'))} — amaç (DEV taslağı).",
                "business_context": None,
                "decision_support": None,
                "known_limitations": None,
            }
            for b in (process.get("blocks") or []) if b.get("id")
        },
        "source": "stub",
    }


def propose_documentation(llm, process: dict[str, Any], *, dev_mode: bool = False) -> dict[str, Any]:
    """LLM'den taslak iste → normalize et. Hata/boş yanıt: DEV'de stub, prod'da
    ``{"error": ...}``. Dönen şekil route'un JSON cevabıdır."""
    valid_block_ids = {b.get("id") for b in (process.get("blocks") or [])}
    try:
        raw_text = llm.complete(_system_prompt(), build_user_prompt(process),
                                max_tokens=1600, temperature=0.2)
    except Exception as exc:
        log.exception("doc proposal LLM çağrısı başarısız: %s", process.get("id"))
        if dev_mode:
            return _stub_proposal(process)
        return {"error": f"LLM çağrısı başarısız: {exc}"}

    parsed = _extract_json(raw_text)
    doc = _clean_side(parsed.get("documentation"))
    blocks_doc = {}
    for bid, bdoc in (parsed.get("blocks_documentation") or {}).items():
        if bid in valid_block_ids and isinstance(bdoc, dict):
            cleaned = _clean_side(bdoc)
            if any(cleaned.values()):
                blocks_doc[bid] = cleaned

    if not any(doc.values()) and not blocks_doc:
        if dev_mode:
            return _stub_proposal(process)
        return {"error": "LLM kullanılabilir bir taslak üretmedi (boş/parse edilemez yanıt)."}
    return {"documentation": doc, "blocks_documentation": blocks_doc, "source": "llm"}

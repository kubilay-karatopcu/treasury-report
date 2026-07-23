"""Uzman Yorumu — Süreç Düzenlileştirme W4a.

Uzman personası, bağlı süreçlerin DÖKÜMANTASYONUNDAN (W1/W3'te yazılan 4 alan)
2-3 cümlelik yorum üretir; uzman sayfasında süreç kartlarının üstünde görünür.
Sayı/veri LLM'e GİTMEZ (metrik sağlayıcı kontratı W4b) — prompt bunu açıkça
yasaklar, yorum "neye bakılır, neden önemli" çerçevesinde kalır.

TTL cache: süreç dökümantasyonu seyrek değişir; yorum uzman başına bellekte
tutulur (varsayılan 30 dk). DEV/FakeLLM ya da hata: deterministik, dürüst
fallback metni (uydurma yorum yok).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "expert_commentary.txt"
_TTL_SECONDS = 1800
#: expert_id → (monotonic_ts, text)
_CACHE: dict[str, tuple[float, str]] = {}


def _build_user_prompt(expert, processes: list[dict]) -> str:
    lines = [f"UZMAN: {expert.name} — alan: {expert.domain_label}"]
    if getattr(expert, "short_description", ""):
        lines.append(f"Uzman tanımı: {expert.short_description}")
    lines.append("SÜREÇLER:")
    for p in processes:
        lines.append(f"- {p.get('label')}")
        doc = p.get("documentation") or {}
        for f, tag in (("purpose", "amaç"), ("business_context", "iş bağlamı"),
                       ("decision_support", "yorum kuralı"),
                       ("known_limitations", "sınırlar")):
            if (doc.get(f) or "").strip():
                lines.append(f"  {tag}: {doc[f]}")
    return "\n".join(lines)


def _full_processes(expert) -> list[dict]:
    """Uzmanın süreçlerini dökümantasyonlu (overlay-merge'li) şekilde getirir."""
    from prisma_home.processes import get_process

    out = []
    for pid in (expert.bound_content or {}).get("processes") or []:
        p = get_process(pid)
        if p is not None:
            out.append(p)
    return out


def get_commentary(expert) -> str | None:
    """Uzman yorumu (TTL cache'li). Süreç yoksa None → bölüm render edilmez."""
    cached = _CACHE.get(expert.id)
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]

    try:
        processes = _full_processes(expert)
    except Exception:
        log.exception("uzman yorumu: süreçler okunamadı (%s)", expert.id)
        return None
    documented = [p for p in processes if p.get("documented")]
    if not documented:
        return None

    llm = current_app.config.get("LLM_CLIENT")
    text: str | None = None
    if llm is not None:
        try:
            raw = llm.complete(_PROMPT_PATH.read_text(encoding="utf-8"),
                               _build_user_prompt(expert, documented),
                               max_tokens=400, temperature=0.3)
            raw = (raw or "").strip()
            # JSON/başlık sızarsa yorum sayma — dürüst fallback'e düş.
            if raw and not raw.startswith("{") and len(raw) > 40:
                text = raw
        except Exception:
            log.exception("uzman yorumu: LLM çağrısı başarısız (%s)", expert.id)

    if text is None:
        # DEV/FakeLLM/hata: dökümantasyondan deterministik, uydurmasız özet.
        labels = " · ".join(p["label"] for p in documented[:4])
        text = (f"{expert.domain_label} süreçleri ({labels}) dökümante edildi; "
                "yorum kuralları süreç kartlarının detayında. Canlı uzman "
                "yorumu LLM bağlandığında burada görünecek.")

    _CACHE[expert.id] = (time.monotonic(), text)
    return text


def invalidate(expert_id: str | None = None) -> None:
    if expert_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(expert_id, None)

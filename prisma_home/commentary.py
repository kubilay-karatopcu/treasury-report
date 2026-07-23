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


def get_process_metrics() -> list[dict]:
    """W4b — süreç metrik sağlayıcısından kompakt KPI listesi.

    Provider app.config'ten okunur (mevduat_panel import edilmez). Yoksa/hata
    verirse boş liste — yorum dökümantasyon-temelli kalır."""
    provider = current_app.config.get("PROCESS_METRICS_PROVIDER")
    if provider is None:
        return []
    try:
        return list(provider() or [])
    except Exception:
        log.exception("süreç metrikleri alınamadı")
        return []


def _build_user_prompt(expert, processes: list[dict],
                       metrics: list[dict] | None = None) -> str:
    lines = [f"UZMAN: {expert.name} — alan: {expert.domain_label}"]
    if getattr(expert, "short_description", ""):
        lines.append(f"Uzman tanımı: {expert.short_description}")
    if metrics:
        lines.append("GÜNCEL METRİKLER (yalnız bunları kullanabilirsin):")
        for m in metrics:
            d = f" ({m['delta']})" if m.get("delta") else ""
            lines.append(f"  - {m.get('k')}: {m.get('v')}{d}")
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

    metrics = get_process_metrics()
    llm = current_app.config.get("LLM_CLIENT")
    text: str | None = None
    if llm is not None:
        try:
            raw = llm.complete(_PROMPT_PATH.read_text(encoding="utf-8"),
                               _build_user_prompt(expert, documented, metrics),
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


_ASK_PROMPT_PATH = Path(__file__).parent / "prompts" / "expert_ask.txt"


def answer_question(expert, question: str) -> str:
    """W4b — "…'ye sor": senkron tek-tur cevap (SSE muadili backlog).

    Bağlam = persona + süreç dökümanları + metrikler; sayı kısıtı prompt'ta.
    LLM yoksa/hata: dürüst yönlendirme metni."""
    question = (question or "").strip()[:500]
    if not question:
        return "Soru boş görünüyor."
    try:
        documented = [p for p in _full_processes(expert) if p.get("documented")]
    except Exception:
        documented = []
    llm = current_app.config.get("LLM_CLIENT")
    if llm is not None and documented:
        try:
            user = (_build_user_prompt(expert, documented, get_process_metrics())
                    + f"\n\nSORU: {question}")
            raw = (llm.complete(_ASK_PROMPT_PATH.read_text(encoding="utf-8"),
                                user, max_tokens=500, temperature=0.3) or "").strip()
            if raw and not raw.startswith("{"):
                return raw
        except Exception:
            log.exception("uzman sorusu cevaplanamadı (%s)", expert.id)
    return ("Şu an canlı cevap üretemiyorum (LLM erişilemedi). Sorunun cevabı "
            "büyük olasılıkla süreç panolarında — aşağıdaki Süreçler bölümünden "
            "ilgili panoya bakabilirsin.")

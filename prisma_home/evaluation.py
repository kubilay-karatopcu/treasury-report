"""Blok değerlendirmesi — Süreç Düzenlileştirme W5a (piramit Aşama A).

Her dökümante bileşen bloğu, KENDİ dökümantasyonu + KENDİ güncel veri
digest'iyle LLM'e ayrı ayrı değerlendirtilir (plan §3.5 W5). Digest'ler
``app.config["PROCESS_BLOCK_DIGESTS"]`` sağlayıcısından gelir (mevduat_panel
kaydeder; bu modül mevduat_panel'i import ETMEZ — W4b izolasyon sözleşmesi).

Hash'li invalidation: değerlendirme ``(digest_hash, doc_hash)`` anahtarıyla
cache'lenir — veri ve dökümantasyon değişmediyse LLM'e GİDİLMEZ (0 çağrı).
Tüm hesap arka planda koşar (commentary refresher döngüsü); istek yolu yalnız
``get_block_evaluation`` ile bellekten okur, asla beklemez.

Aşama B (süreç değerlendirmesi) ve C (uzman anlatısı) W5b'de bu cache'i
``all_evaluations()`` üzerinden tüketecek.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "block_evaluation.txt"

#: block_id → {"text", "digest_hash", "doc_hash", "pid", "process_label",
#:             "title", "has_data", "ts"}
#: Tek yazar (refresher thread'i) + dict okurları — GIL altında güvenli.
_EVAL: dict[str, dict] = {}


def _hash(obj) -> str:
    """Deterministik içerik hash'i (invalidation anahtarı)."""
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def get_block_digest(block_id: str) -> list[dict]:
    """Bloğun kompakt veri özeti — PROCESS_BLOCK_DIGESTS sağlayıcısından.

    Sağlayıcı yoksa/hata verirse boş liste (değerlendirme dökümantasyon-temelli
    kalır). Kontrat: ≤15 satır {k, v, delta, tone}; sayılar önceden formatlı."""
    registry = current_app.config.get("PROCESS_BLOCK_DIGESTS") or {}
    fn = registry.get(block_id)
    if fn is None:
        return []
    try:
        return list(fn() or [])[:15]
    except Exception:
        log.exception("blok digest'i üretilemedi: %s", block_id)
        return []


def _digest_lines(digest: list[dict]) -> list[str]:
    out = []
    for row in digest:
        d = f" ({row['delta']})" if row.get("delta") else ""
        out.append(f"  - {row.get('k')}: {row.get('v')}{d}")
    return out


def _build_block_prompt(process_label: str, block: dict,
                        digest: list[dict]) -> str:
    doc = block.get("documentation") or {}
    lines = [f"BLOK: {block.get('title')} (süreç: {process_label})"]
    for f, tag in (("purpose", "amaç"), ("business_context", "iş bağlamı"),
                   ("decision_support", "yorum kuralı"),
                   ("known_limitations", "sınırlar")):
        if (doc.get(f) or "").strip():
            lines.append(f"{tag}: {doc[f]}")
    if digest:
        lines.append("GÜNCEL VERİ (yalnız bunları kullanabilirsin):")
        lines.extend(_digest_lines(digest))
    else:
        lines.append("GÜNCEL VERİ: yok (sayı kullanma).")
    return "\n".join(lines)


def _fallback_text(block: dict, has_data: bool) -> str:
    """LLM'siz/hatalı durumda dürüst, uydurmasız metin."""
    if has_data:
        return ("Güncel veri özeti hazır; canlı blok değerlendirmesi LLM "
                "bağlandığında burada görünecek. Yorum kuralı: "
                + ((block.get("documentation") or {}).get("decision_support")
                   or "blok dökümantasyonunda."))
    return ("Bu blok için güncel veri özeti (digest) henüz tanımlı değil; "
            "değerlendirme dökümantasyon çerçevesinde kalır.")


def evaluate_block(pid: str, process_label: str, block: dict) -> bool:
    """Tek bloğu değerlendirir (app/istek bağlamı çağıranda kurulu olmalı).

    Dönüş: yeniden HESAPLANDI mı? — hash eşleşirse False (cache'ten; LLM'e de
    gidilmez — kabul kriteri "değişmeden ikinci tur 0 LLM çağrısı" buradan).
    LLM yalnız digest doluyken çağrılır; digest'siz hesap deterministik
    fallback üretir (yine hash'lenir, her turda yeniden üretilmez).
    """
    bid = block.get("id") or ""
    if not bid:
        return False
    digest = get_block_digest(bid)
    digest_hash = _hash(digest)
    doc_hash = _hash(block.get("documentation") or {})
    cur = _EVAL.get(bid)
    if cur and cur["digest_hash"] == digest_hash and cur["doc_hash"] == doc_hash:
        return False

    llm = current_app.config.get("LLM_CLIENT")
    text: str | None = None
    # LLM yalnız veri varken çağrılır: digest'siz değerlendirme sayı uydurma
    # riskine değmez — dürüst fallback yeterli (W5b süreç aşaması dökümanı
    # zaten kullanacak).
    if llm is not None and digest:
        try:
            raw = llm.complete(_PROMPT_PATH.read_text(encoding="utf-8"),
                               _build_block_prompt(process_label, block, digest),
                               max_tokens=300, temperature=0.2)
            raw = (raw or "").strip()
            if raw and not raw.startswith("{") and len(raw) > 30:
                text = raw
        except Exception:
            log.exception("blok değerlendirmesi: LLM çağrısı başarısız (%s)", bid)

    if text is None:
        text = _fallback_text(block, bool(digest))

    _EVAL[bid] = {
        "text": text,
        "digest_hash": digest_hash,
        "doc_hash": doc_hash,
        "pid": pid,
        "process_label": process_label,
        "title": block.get("title") or bid,
        "has_data": bool(digest),
        "ts": time.time(),
    }
    return True


def evaluate_all_blocks(app) -> dict:
    """Tüm dökümante bileşen bloklarını değerlendirir — ARKA PLAN girişi.

    test_request_context: get_process → _safe_url → url_for istek bağlamı
    ister (commentary._compute_and_store ile aynı ders). Dönüş: istatistik
    {"computed": n, "cached": m, "blocks": k} — log + test için."""
    stats = {"computed": 0, "cached": 0, "blocks": 0}
    with app.test_request_context():
        from prisma_home.processes import PROCESS_REGISTRY, get_process

        for pid in PROCESS_REGISTRY:
            try:
                process = get_process(pid)
            except Exception:
                log.exception("blok değerlendirmesi: süreç okunamadı (%s)", pid)
                continue
            if not process:
                continue
            for block in process.get("blocks") or []:
                if not block.get("documented"):
                    continue
                stats["blocks"] += 1
                try:
                    computed = evaluate_block(pid, process.get("label", pid), block)
                    stats["computed" if computed else "cached"] += 1
                except Exception:
                    log.exception("blok değerlendirmesi başarısız: %s",
                                  block.get("id"))
    log.info("blok değerlendirme turu: %s", stats)
    return stats


def get_block_evaluation(block_id: str) -> dict | None:
    """İstek yolu okuyucusu — anında, asla hesaplamaz."""
    return _EVAL.get(block_id)


def all_evaluations() -> dict[str, dict]:
    """Aşama B girdisi (W5b): block_id → değerlendirme kaydı (kopya)."""
    return dict(_EVAL)


def invalidate(block_id: str | None = None) -> None:
    if block_id is None:
        _EVAL.clear()
    else:
        _EVAL.pop(block_id, None)

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
    llm = current_app.config.get("LLM_CLIENT")
    cur = _EVAL.get(bid)
    if cur and cur["digest_hash"] == digest_hash and cur["doc_hash"] == doc_hash:
        # Fallback kaydı + LLM denenebilir durumda (kullanıcı geri bildirimi
        # 2026-07-23: geçici LLM hatası fallback'i bir sonraki veri değişimine
        # kadar KİLİTLİYORDU) → hash eşleşse de yeniden dene. Gerçek metin ya
        # da LLM'siz/verisiz fallback → cache'ten.
        if not (cur.get("is_fallback") and llm is not None and digest):
            return False

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

    is_fallback = text is None
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
        "is_fallback": is_fallback,
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
    """Aşama B girdisi: block_id → değerlendirme kaydı (kopya)."""
    return dict(_EVAL)


# ════════════════════════════════════════════════════════════════════════════
# Aşama B — süreç değerlendirmesi (W5b)
#
# Girdi: süreç dökümantasyonu (4 alan) + o sürecin Aşama-A blok
# değerlendirmeleri. Çıktı: [[blok:<id>]] atıflı 3-4 cümlelik sentez —
# citations.parse_citations ile doğrulanıp segment listesi olarak saklanır.
# Hash anahtarı (doc_hash, children_hash): bloklar ve döküman değişmediyse
# LLM'e gidilmez; A'daki bir değişiklik children_hash üzerinden B'yi
# kendiliğinden tazeler (piramit yukarı yayılım).
# ════════════════════════════════════════════════════════════════════════════

_PROC_PROMPT_PATH = Path(__file__).parent / "prompts" / "process_evaluation.txt"

#: pid → {"text", "segments", "cites", "doc_hash", "children_hash",
#:        "label", "block_titles", "ts"}
_PROC_EVAL: dict[str, dict] = {}


def _build_process_prompt(process: dict, block_evals: dict[str, dict]) -> str:
    doc = process.get("documentation") or {}
    lines = [f"SÜREÇ: {process.get('label')}"]
    for f, tag in (("purpose", "amaç"), ("business_context", "iş bağlamı"),
                   ("decision_support", "karar desteği"),
                   ("known_limitations", "sınırlar")):
        if (doc.get(f) or "").strip():
            lines.append(f"{tag}: {doc[f]}")
    lines.append("BLOK DEĞERLENDİRMELERİ:")
    for bid, rec in block_evals.items():
        lines.append(f"- [{bid}] {rec.get('title')}: {rec.get('text')}")
    lines.append("ATIF YAPILABİLECEK BLOK ID'LERİ: "
                 + ", ".join(block_evals.keys()))
    return "\n".join(lines)


def _process_fallback(process: dict, block_evals: dict[str, dict]) -> str:
    titles = " · ".join(r.get("title", "") for r in list(block_evals.values())[:4])
    if titles:
        return (f"{process.get('label')} bloklarının ({titles}) güncel "
                "değerlendirmeleri hazır; süreç sentezi LLM bağlandığında "
                "burada görünecek.")
    return (f"{process.get('label')} için blok değerlendirmesi henüz yok; "
            "süreç yorumu dökümantasyon çerçevesinde kalır.")


def evaluate_process(pid: str, process: dict,
                     block_evals: dict[str, dict]) -> bool:
    """Tek süreci değerlendirir. Dönüş: yeniden hesaplandı mı?

    LLM yalnız en az bir blok değerlendirmesi varken çağrılır (A ile aynı
    uydurma disiplini); atıflar block_evals kümesine karşı doğrulanır."""
    from prisma_home.citations import parse_citations

    doc_hash = _hash(process.get("documentation") or {})
    children_hash = _hash({bid: rec.get("text") for bid, rec in block_evals.items()})
    llm = current_app.config.get("LLM_CLIENT")
    cur = _PROC_EVAL.get(pid)
    if cur and cur["doc_hash"] == doc_hash and cur["children_hash"] == children_hash:
        # Fallback + LLM denenebilir → hash eşleşse de yeniden dene (geçici
        # LLM hatası fallback'i kilitlemesin — A'daki notla aynı).
        if not (cur.get("is_fallback") and llm is not None and block_evals):
            return False

    parsed: dict | None = None
    if llm is not None and block_evals:
        try:
            raw = llm.complete(_PROC_PROMPT_PATH.read_text(encoding="utf-8"),
                               _build_process_prompt(process, block_evals),
                               max_tokens=400, temperature=0.2)
            raw = (raw or "").strip()
            if raw and not raw.startswith("{") and len(raw) > 40:
                parsed = parse_citations(raw, set(block_evals))
        except Exception:
            log.exception("süreç değerlendirmesi: LLM çağrısı başarısız (%s)", pid)

    is_fallback = parsed is None or not parsed["text"]
    if is_fallback:
        fb = _process_fallback(process, block_evals)
        parsed = {"text": fb, "segments": [{"text": fb, "cites": []}], "cites": []}

    _PROC_EVAL[pid] = {
        **parsed,
        "doc_hash": doc_hash,
        "children_hash": children_hash,
        "label": process.get("label", pid),
        "block_titles": {bid: rec.get("title", bid)
                         for bid, rec in block_evals.items()},
        "is_fallback": is_fallback,
        "ts": time.time(),
    }
    return True


def evaluate_all_processes(app) -> dict:
    """Tüm dökümante süreçler için Aşama B — ARKA PLAN girişi.

    Aşama A'nın (evaluate_all_blocks) SONRASINDA çağrılmalı; A cache'inden
    okur. Dönüş: {"computed": n, "cached": m, "processes": k}."""
    stats = {"computed": 0, "cached": 0, "processes": 0}
    with app.test_request_context():
        from prisma_home.processes import PROCESS_REGISTRY, get_process

        for pid in PROCESS_REGISTRY:
            try:
                process = get_process(pid)
            except Exception:
                log.exception("süreç değerlendirmesi: süreç okunamadı (%s)", pid)
                continue
            if not process or not process.get("documented"):
                continue
            block_evals = {
                b["id"]: _EVAL[b["id"]]
                for b in process.get("blocks") or []
                if b.get("documented") and b.get("id") in _EVAL
            }
            stats["processes"] += 1
            try:
                computed = evaluate_process(pid, process, block_evals)
                stats["computed" if computed else "cached"] += 1
            except Exception:
                log.exception("süreç değerlendirmesi başarısız: %s", pid)
    log.info("süreç değerlendirme turu: %s", stats)
    return stats


def get_process_evaluation(pid: str) -> dict | None:
    """İstek yolu okuyucusu — anında, asla hesaplamaz."""
    return _PROC_EVAL.get(pid)


def all_process_evaluations() -> dict[str, dict]:
    """Aşama C girdisi: pid → süreç değerlendirme kaydı (kopya)."""
    return dict(_PROC_EVAL)


def invalidate(block_id: str | None = None) -> None:
    if block_id is None:
        _EVAL.clear()
        _PROC_EVAL.clear()
    else:
        _EVAL.pop(block_id, None)

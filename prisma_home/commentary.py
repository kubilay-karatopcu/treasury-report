"""Uzman Yorumu — Süreç Düzenlileştirme W4a.

Uzman personası, bağlı süreçlerin DÖKÜMANTASYONUNDAN (W1/W3'te yazılan 4 alan)
2-3 cümlelik yorum üretir; uzman sayfasında süreç kartlarının üstünde görünür.

İSTEK YOLU HİÇ BLOKLANMAZ (kullanıcı kararı 2026-07-23: "uzmana tıklayınca
yavaş açılmasın; belirli aralıkla trigger edip güncelle"). LLM çağrısı ve
metrik/Oracle okuması ARKA PLANDA yapılır:
  - `get_commentary()` sıcak cache varsa onu döner; yoksa arka plan tazelemesi
    planlar ve ANINDA ucuz/deterministik bir metin döner (LLM beklemez).
  - `start_commentary_refresher()` açılışta tüm uzmanları periyodik ısıtır
    (interval < TTL → cache hep sıcak), böylece ilk tıklama da içerikli gelir.

Sayı/veri yalnız "GÜNCEL METRİKLER" sağlayıcısından gelir (metrik sağlayıcı
kontratı W4b); prompt uydurma sayıyı yasaklar. DEV/FakeLLM ya da hata:
deterministik, dürüst fallback metni (uydurma yorum yok).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "expert_commentary.txt"
_TTL_SECONDS = 1800
#: uzmanları periyodik ısıtma aralığı — TTL'den kısa tutulur ki cache soğumasın.
_REFRESH_INTERVAL = 900
#: expert_id → (monotonic_ts, text)
_CACHE: dict[str, tuple[float, str]] = {}
#: arka planda hesaplaması süren uzmanlar (çift LLM çağrısını önler).
_INFLIGHT: set[str] = set()
_LOCK = threading.Lock()


def get_process_metrics() -> list[dict]:
    """W4b — süreç metrik sağlayıcısından kompakt KPI listesi.

    Provider app.config'ten okunur (mevduat_panel import edilmez). Yoksa/hata
    verirse boş liste — yorum dökümantasyon-temelli kalır. Ağır Oracle okuması
    olabileceğinden YALNIZ arka plan hesabında çağrılır, istek yolunda değil."""
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
    """Uzmanın süreçlerini dökümantasyonlu (overlay-merge'li) şekilde getirir.

    Bellek-içi registry + küçük overlay store okuması — Oracle/LLM YOK, istek
    yolunda çağrılması güvenli (ucuz)."""
    from prisma_home.processes import get_process

    out = []
    for pid in (expert.bound_content or {}).get("processes") or []:
        p = get_process(pid)
        if p is not None:
            out.append(p)
    return out


def _fallback_text(expert, documented: list[dict]) -> str:
    """LLM'siz, uydurmasız deterministik özet (istek yolunda ve DEV'de kullanılır)."""
    labels = " · ".join(p["label"] for p in documented[:4])
    return (f"{expert.domain_label} süreçleri ({labels}) dökümante edildi; "
            "yorum kuralları süreç kartlarının detayında. Canlı uzman yorumu "
            "arka planda hazırlanıyor, birazdan burada görünecek.")


def _compute_and_store(app, expert) -> None:
    """AĞIR yol (LLM + metrik/Oracle) — YALNIZ arka planda çağrılır.

    Sonucu (LLM metni ya da fallback) TTL cache'e yazar. İstek yolu buna
    hiçbir zaman senkron girmez.

    test_request_context (app_context değil): get_process → _safe_url içeride
    url_for çağırıyor; url_for istek bağlamı (ya da SERVER_NAME) ister, yoksa
    RuntimeError atar. Kurulan URL yorumda kullanılmıyor (yalnız label +
    dökümantasyon okunur), bu yüzden sahte istek bağlamı güvenli."""
    with app.test_request_context():
        try:
            documented = [p for p in _full_processes(expert) if p.get("documented")]
        except Exception:
            log.exception("uzman yorumu: süreçler okunamadı (%s)", expert.id)
            return
        if not documented:
            return

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
            text = _fallback_text(expert, documented)
        _CACHE[expert.id] = (time.monotonic(), text)


def _schedule(app, expert) -> None:
    """Uzman için arka plan tazelemesi planlar (aynı uzman için tek uçuş)."""
    with _LOCK:
        if expert.id in _INFLIGHT:
            return
        _INFLIGHT.add(expert.id)

    def _run():
        try:
            _compute_and_store(app, expert)
        finally:
            with _LOCK:
                _INFLIGHT.discard(expert.id)

    threading.Thread(target=_run, name=f"commentary-{expert.id}", daemon=True).start()


def get_commentary(expert) -> str | None:
    """Uzman yorumu — İSTEK YOLU, LLM'i ASLA beklemez.

    Sıcak cache → onu döner. Değilse: arka plan tazelemesi planlar ve anında
    bayat cache'i (varsa) ya da ucuz deterministik fallback'i döner. Süreç
    yoksa None → bölüm render edilmez."""
    cached = _CACHE.get(expert.id)
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]

    # Soğuk/bayat: arka planda tazele, istek yolunu bloklama.
    try:
        _schedule(current_app._get_current_object(), expert)
    except Exception:
        log.exception("uzman yorumu: arka plan tazeleme planlanamadı (%s)", expert.id)

    if cached:
        return cached[1]  # bayat ama anında; bir sonraki yüklemede tazelenir.

    # Hiç cache yok → ucuz, Oracle/LLM'siz deterministik metin.
    try:
        documented = [p for p in _full_processes(expert) if p.get("documented")]
    except Exception:
        log.exception("uzman yorumu: süreçler okunamadı (%s)", expert.id)
        return None
    if not documented:
        return None
    return _fallback_text(expert, documented)


def warm_all(app) -> None:
    """Tüm uzmanların yorumunu bir kez (arka planda) hesaplar — periyodik ısıtma."""
    store = app.config.get("EXPERT_STORE")
    if store is None:
        return
    try:
        experts = store.list_all()
    except Exception:
        log.exception("uzman yorumu ısıtma: uzman listesi alınamadı")
        return
    for e in experts:
        try:
            _compute_and_store(app, e)
        except Exception:
            log.exception("uzman yorumu ısıtma: %s başarısız", getattr(e, "id", "?"))


def start_commentary_refresher(app, interval: int = _REFRESH_INTERVAL,
                               initial_delay: int = 20) -> None:
    """Açılışta daemon thread'de periyodik ısıtma başlatır — çağıran bloklanmaz.

    İlk tur kısa bir gecikmeyle koşar (prewarm motorları — özellikle
    outstanding_daily — RAM'e ısıtsın; böylece metrik okuması Oracle'a soğuk
    gitmez ve boot'ta havuz çekişmesi azalır), sonra `interval` saniyede bir
    tazeler. Interval TTL'den kısa tutulduğundan cache hep sıcak kalır ve ilk
    tıklama da içerikli gelir."""
    def _loop():
        time.sleep(initial_delay)
        while True:
            # W5a — piramit Aşama A: önce bloklar değerlendirilir (hash'li
            # cache; veri değişmediyse 0 LLM çağrısı), sonra uzman yorumu.
            # W5b süreç/uzman aşamalarını bu çıktılara zincirleyecek.
            try:
                from prisma_home.evaluation import evaluate_all_blocks
                evaluate_all_blocks(app)
            except Exception:
                log.exception("blok değerlendirme turu başarısız")
            try:
                warm_all(app)
            except Exception:
                log.exception("uzman yorumu ısıtma turu başarısız")
            time.sleep(interval)

    threading.Thread(target=_loop, name="commentary-refresher", daemon=True).start()


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

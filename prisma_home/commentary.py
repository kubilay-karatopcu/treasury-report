"""Uzman Yorumu — Süreç Düzenlileştirme W4a → W5b (piramit Aşama C).

Uzman personası, bağlı süreçlerin AŞAMA-B DEĞERLENDİRMELERİNDEN (evaluation.py;
her biri kendi bloklarının verili değerlendirmelerinden sentezlendi) 4-6
cümlelik brifing ANLATISI üretir; bulgular [[blok:<id>]] atıflarıyla bloklara
bağlanır (citations.parse_citations doğrular — uydurma id düşer). Global
metrikler (W4b sağlayıcısı) çapa değer olarak prompt'ta kalır.

İSTEK YOLU HİÇ BLOKLANMAZ (2026-07-23 kararı): get_commentary sıcak kaydı
anında döner; soğukta ucuz fallback + arka plan planlaması. Tazeleme
hash-güdümlüdür: girdiler (süreç değerlendirmeleri + metrikler) değişmediyse
LLM'e gidilmez — periyodik döngü (refresh_pipeline: A → B → C) ve mevduat
data-refresh sonrası hook aynı boru hattını koşar.

Sayı köken zinciri: C yalnız GÜNCEL METRİKLER + Aşama-B metinlerinde geçen
sayıları kullanabilir (prompt kuralı; B de A'ya, A da digest'e zincirli).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from flask import current_app

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "expert_commentary.txt"
#: uzmanları periyodik ısıtma aralığı (sn) — hash'ler değişmediyse tur ucuzdur.
_REFRESH_INTERVAL = 900
#: expert_id → {"text","segments","cites","input_hash","block_titles","ts"}
_CACHE: dict[str, dict] = {}
#: arka planda hesaplaması süren uzmanlar (çift LLM çağrısını önler).
_INFLIGHT: set[str] = set()
_LOCK = threading.Lock()


def _hash(obj) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def get_process_metrics() -> list[dict]:
    """W4b — süreç metrik sağlayıcısından kompakt KPI listesi.

    Provider app.config'ten okunur (mevduat_panel import edilmez). Yoksa/hata
    verirse boş liste. Ağır olabileceğinden YALNIZ arka planda çağrılır."""
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
    """W4b "…'ye sor" bağlamı — süreç DÖKÜMANTASYONU temelli (eski davranış)."""
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
    """Uzmanın süreçleri, dökümantasyonlu (overlay-merge'li). Ucuz — LLM yok."""
    from prisma_home.processes import get_process

    out = []
    for pid in (expert.bound_content or {}).get("processes") or []:
        p = get_process(pid)
        if p is not None:
            out.append(p)
    return out


def _fallback_text(expert, documented: list[dict]) -> str:
    labels = " · ".join(p["label"] for p in documented[:4])
    return (f"{expert.domain_label} süreçleri ({labels}) dökümante edildi; "
            "yorum kuralları süreç kartlarının detayında. Canlı uzman brifingi "
            "arka planda hazırlanıyor, birazdan burada görünecek.")


def _build_briefing_prompt(expert, proc_evals: dict[str, dict],
                           metrics: list[dict],
                           catalog: dict[str, str]) -> str:
    """Aşama C prompt'u: persona + metrik çapa + B değerlendirmeleri + katalog."""
    lines = [f"UZMAN: {expert.name} — alan: {expert.domain_label}"]
    if getattr(expert, "short_description", ""):
        lines.append(f"Uzman tanımı: {expert.short_description}")
    if metrics:
        lines.append("GÜNCEL METRİKLER (çapa — yalnız bunlar ve süreç "
                     "değerlendirmelerindeki sayılar):")
        for m in metrics:
            d = f" ({m['delta']})" if m.get("delta") else ""
            lines.append(f"  - {m.get('k')}: {m.get('v')}{d}")
    lines.append("SÜREÇ DEĞERLENDİRMELERİ:")
    for pid, rec in proc_evals.items():
        cites = f" (dayanaklar: {', '.join(rec['cites'])})" if rec.get("cites") else ""
        lines.append(f"- {rec.get('label', pid)}: {rec.get('text')}{cites}")
    lines.append("ATIF KATALOĞU (id → blok adı):")
    for bid, title in catalog.items():
        lines.append(f"  - {bid}: {title}")
    return "\n".join(lines)


def _compute_and_store(app, expert) -> None:
    """AĞIR yol (LLM) — YALNIZ arka planda. Hash eşleşirse LLM'e gidilmez.

    test_request_context: get_process → url_for istek bağlamı ister."""
    from prisma_home import evaluation
    from prisma_home.citations import parse_citations

    with app.test_request_context():
        try:
            documented = [p for p in _full_processes(expert) if p.get("documented")]
        except Exception:
            log.exception("uzman brifingi: süreçler okunamadı (%s)", expert.id)
            return
        if not documented:
            return

        pids = [p["id"] for p in documented]
        proc_evals = {pid: rec for pid in pids
                      if (rec := evaluation.get_process_evaluation(pid))}
        # Atıf kataloğu: bağlı süreçlerin dökümante blokları (id → başlık).
        catalog: dict[str, str] = {}
        for p in documented:
            for b in p.get("blocks") or []:
                if b.get("documented") and b.get("id"):
                    catalog[b["id"]] = b.get("title") or b["id"]

        metrics = get_process_metrics()
        input_hash = _hash({
            "desc": getattr(expert, "short_description", ""),
            "evals": {pid: rec.get("text") for pid, rec in proc_evals.items()},
            "metrics": metrics,
            "catalog": sorted(catalog),
        })
        cached = _CACHE.get(expert.id)
        if cached and cached.get("input_hash") == input_hash:
            return

        llm = current_app.config.get("LLM_CLIENT")
        parsed: dict | None = None
        if llm is not None and proc_evals:
            try:
                raw = llm.complete(
                    _PROMPT_PATH.read_text(encoding="utf-8"),
                    _build_briefing_prompt(expert, proc_evals, metrics, catalog),
                    max_tokens=700, temperature=0.3)
                raw = (raw or "").strip()
                if raw and not raw.startswith("{") and len(raw) > 40:
                    parsed = parse_citations(raw, set(catalog))
            except Exception:
                log.exception("uzman brifingi: LLM çağrısı başarısız (%s)", expert.id)

        if parsed is None or not parsed["text"]:
            fb = _fallback_text(expert, documented)
            parsed = {"text": fb, "segments": [{"text": fb, "cites": []}],
                      "cites": []}

        _CACHE[expert.id] = {
            **parsed,
            "input_hash": input_hash,
            "block_titles": catalog,
            "ts": time.time(),
        }


def _schedule(app, expert) -> None:
    """Uzman için arka plan hesaplaması planlar (uzman başına tek uçuş)."""
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
    """Uzman brifing metni — İSTEK YOLU, LLM'i ASLA beklemez.

    Kayıt varsa metnini döner (tazeliği refresh_pipeline hash'lerle yönetir).
    Yoksa: arka plan hesaplaması planlar, ucuz deterministik fallback döner.
    Süreç yoksa None → bölüm render edilmez."""
    cached = _CACHE.get(expert.id)
    if cached:
        return cached["text"]

    try:
        _schedule(current_app._get_current_object(), expert)
    except Exception:
        log.exception("uzman brifingi: arka plan planlanamadı (%s)", expert.id)

    try:
        documented = [p for p in _full_processes(expert) if p.get("documented")]
    except Exception:
        log.exception("uzman brifingi: süreçler okunamadı (%s)", expert.id)
        return None
    if not documented:
        return None
    return _fallback_text(expert, documented)


def get_commentary_record(expert_id: str) -> dict | None:
    """W5c UI okuyucusu: {text, segments, cites, block_titles, ts} — anında."""
    return _CACHE.get(expert_id)


def warm_all(app) -> None:
    """Tüm uzmanların brifingini bir kez hesaplar (hash eşleşirse 0 çağrı)."""
    store = app.config.get("EXPERT_STORE")
    if store is None:
        return
    try:
        experts = store.list_all()
    except Exception:
        log.exception("uzman brifingi ısıtma: uzman listesi alınamadı")
        return
    for e in experts:
        try:
            _compute_and_store(app, e)
        except Exception:
            log.exception("uzman brifingi ısıtma: %s başarısız", getattr(e, "id", "?"))


def refresh_pipeline(app) -> None:
    """Piramidin tam turu: Aşama A (bloklar) → B (süreçler) → C (uzmanlar).

    Girdiler değişmediyse her aşama hash'ten döner (0 LLM çağrısı) — hem
    periyodik döngü hem mevduat data-refresh hook'u bunu güvenle koşar."""
    from prisma_home.evaluation import evaluate_all_blocks, evaluate_all_processes

    try:
        evaluate_all_blocks(app)
    except Exception:
        log.exception("piramit: blok değerlendirme turu başarısız")
    try:
        evaluate_all_processes(app)
    except Exception:
        log.exception("piramit: süreç değerlendirme turu başarısız")
    try:
        warm_all(app)
    except Exception:
        log.exception("piramit: uzman brifing turu başarısız")


def start_commentary_refresher(app, interval: int = _REFRESH_INTERVAL,
                               initial_delay: int = 20) -> None:
    """Açılışta daemon thread'de periyodik piramit turu başlatır.

    İlk tur kısa gecikmeyle (prewarm motorları ısıtsın); sonrası `interval`
    saniyede bir. Veri değişmedikçe turlar LLM'siz ve ucuzdur."""
    def _loop():
        time.sleep(initial_delay)
        while True:
            refresh_pipeline(app)
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
    (Aşama-B bağlam yükseltmesi W5c'de.) LLM yoksa/hata: dürüst yönlendirme."""
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

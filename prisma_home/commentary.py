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
import re
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


def _full_processes(expert, process_ids: list[str] | None = None) -> list[dict]:
    """Süreçler, dökümantasyonlu (overlay-merge'li). Ucuz — LLM yok.

    W8: process_ids verilirse o bakışın süreçleri kullanılır; verilmezse
    uzmanın bound_content.processes'i (legacy)."""
    from prisma_home.processes import get_process

    pids = process_ids if process_ids is not None else (
        (expert.bound_content or {}).get("processes") or [])
    out = []
    for pid in pids:
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
                           catalog: dict[str, str],
                           briefing_focus: str = "") -> str:
    """Aşama C prompt'u: persona + (W8) departman odağı + metrik çapa +
    B değerlendirmeleri + katalog."""
    lines = [f"UZMAN: {expert.name} — alan: {expert.domain_label}"]
    if getattr(expert, "short_description", ""):
        lines.append(f"Uzman tanımı: {expert.short_description}")
    if briefing_focus:
        # W8 — departman merceği: brifingi bu departmanın bakışıyla çerçevele.
        lines.append(f"DEPARTMAN ODAĞI (brifingi bu mercekle kur): {briefing_focus}")
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


#: W6a — madde satırı: "- ", "• " ya da "* " ile başlar.
_BULLET_RE = re.compile(r"^\s*[-•*]\s+")


def _parse_briefing(raw: str, allowed: set) -> dict:
    """W6a — ham LLM brifingini yapılandırır.

    ≥2 madde satırı varsa headline modu: her madde ayrı parse edilir →
    {"headlines": [{text, cites}], "segments", "cites", "text"}. Madde
    formatı yoksa (eski prompt çıktısı / format tutmayan model) paragraf
    yolu: parse_citations sonucu + headlines=None — şablon paragraf render
    eder, geriye uyum korunur."""
    from prisma_home.citations import parse_citations

    lines = [l for l in (raw or "").splitlines() if l.strip()]
    bullets = [l for l in lines if _BULLET_RE.match(l)]
    if len(bullets) >= 2:
        headlines: list[dict] = []
        segments: list[dict] = []
        cites: list[str] = []
        for line in bullets:
            parsed = parse_citations(_BULLET_RE.sub("", line, count=1), allowed)
            if not parsed["text"]:
                continue
            headlines.append({"text": parsed["text"], "cites": parsed["cites"]})
            segments.extend(parsed["segments"])
            for c in parsed["cites"]:
                if c not in cites:
                    cites.append(c)
        if headlines:
            return {"text": " ".join(h["text"] for h in headlines),
                    "segments": segments, "cites": cites,
                    "headlines": headlines}
    parsed = parse_citations(raw, allowed)
    parsed["headlines"] = None
    return parsed


def _cache_key(expert_id: str, view_key: str) -> str:
    return f"{expert_id}::{view_key}"


def _views_of(expert) -> list[dict]:
    """W8 — uzmanın hesaplanacak bakışları: department_views (varsa) ya da
    tek legacy bakış. Her biri {key, process_ids, briefing_focus, ...}."""
    from prisma_home.expert_views import legacy_view, list_views

    return list_views(expert) or [legacy_view(expert)]


def _compute_and_store(app, expert, view: dict) -> None:
    """AĞIR yol (LLM) — YALNIZ arka planda. Hash eşleşirse LLM'e gidilmez.
    W8: tek bir departman bakışı için brifing üretir (Aşama C çatalı).

    test_request_context: get_process → url_for istek bağlamı ister."""
    from prisma_home import evaluation

    ckey = _cache_key(expert.id, view["key"])
    focus = view.get("briefing_focus") or ""
    with app.test_request_context():
        try:
            documented = [p for p in _full_processes(expert, view["process_ids"])
                          if p.get("documented")]
        except Exception:
            log.exception("uzman brifingi: süreçler okunamadı (%s/%s)",
                          expert.id, view["key"])
            return
        if not documented:
            return

        pids = [p["id"] for p in documented]
        proc_evals = {pid: rec for pid in pids
                      if (rec := evaluation.get_process_evaluation(pid))}
        catalog: dict[str, str] = {}
        for p in documented:
            for b in p.get("blocks") or []:
                if b.get("documented") and b.get("id"):
                    catalog[b["id"]] = b.get("title") or b["id"]

        metrics = get_process_metrics()
        input_hash = _hash({
            "desc": getattr(expert, "short_description", ""),
            "focus": focus,
            "evals": {pid: rec.get("text") for pid, rec in proc_evals.items()},
            "metrics": metrics,
            "catalog": sorted(catalog),
        })
        llm = current_app.config.get("LLM_CLIENT")
        cached = _CACHE.get(ckey)
        if cached and cached.get("input_hash") == input_hash:
            if not (cached.get("is_fallback") and llm is not None and proc_evals):
                return

        parsed: dict | None = None
        flagged = 0
        if llm is not None and proc_evals:
            try:
                raw = llm.complete(
                    _PROMPT_PATH.read_text(encoding="utf-8"),
                    _build_briefing_prompt(expert, proc_evals, metrics, catalog, focus),
                    max_tokens=700, temperature=0.3)
                raw = (raw or "").strip()
                if raw and not raw.startswith("{") and len(raw) > 40:
                    from prisma_home.numbers import validate_numbers

                    num_src = [f"{m.get('v')} {m.get('delta', '')}" for m in metrics]
                    num_src += [rec.get("text", "") for rec in proc_evals.values()]
                    nv = validate_numbers(raw, num_src)
                    flagged = nv["flagged"]
                    if flagged:
                        log.info("uzman brifingi %s/%s: %d madde sayı-doğrulamadan "
                                 "düştü", expert.id, view["key"], flagged)
                    if len(nv["text"]) > 40:
                        parsed = _parse_briefing(nv["text"], set(catalog))
            except Exception:
                log.exception("uzman brifingi: LLM çağrısı başarısız (%s/%s)",
                              expert.id, view["key"])

        is_fallback = parsed is None or not parsed["text"]
        if is_fallback:
            fb = _fallback_text(expert, documented)
            parsed = {"text": fb, "segments": [{"text": fb, "cites": []}],
                      "cites": [], "headlines": None}

        _CACHE[ckey] = {
            **parsed,
            "input_hash": input_hash,
            "block_titles": catalog,
            "is_fallback": is_fallback,
            "numbers_flagged": flagged,   # W7a — sağlık/gözlem (W7c)
            "ts": time.time(),
        }


def _schedule(app, expert, view: dict) -> None:
    """Bir (uzman, bakış) için arka plan hesaplaması planlar (tek uçuş)."""
    ckey = _cache_key(expert.id, view["key"])
    with _LOCK:
        if ckey in _INFLIGHT:
            return
        _INFLIGHT.add(ckey)

    def _run():
        try:
            _compute_and_store(app, expert, view)
        finally:
            with _LOCK:
                _INFLIGHT.discard(ckey)

    threading.Thread(target=_run, name=f"commentary-{ckey}", daemon=True).start()


def get_commentary(expert, view: dict) -> str | None:
    """Bir departman bakışının brifing metni — İSTEK YOLU, LLM'i ASLA beklemez.

    Kayıt varsa metnini döner; yoksa arka plan hesaplaması planlar ve ucuz
    deterministik fallback döner. Bakışın süreci yoksa None."""
    cached = _CACHE.get(_cache_key(expert.id, view["key"]))
    if cached:
        return cached["text"]

    try:
        _schedule(current_app._get_current_object(), expert, view)
    except Exception:
        log.exception("uzman brifingi: arka plan planlanamadı (%s)", expert.id)

    try:
        documented = [p for p in _full_processes(expert, view["process_ids"])
                      if p.get("documented")]
    except Exception:
        log.exception("uzman brifingi: süreçler okunamadı (%s)", expert.id)
        return None
    if not documented:
        return None
    return _fallback_text(expert, documented)


def get_commentary_record(expert_id: str, view_key: str) -> dict | None:
    """W5c/W8 UI okuyucusu: (uzman, bakış) brifing kaydı — anında."""
    return _CACHE.get(_cache_key(expert_id, view_key))


def warm_all(app) -> None:
    """Tüm uzmanların TÜM bakışlarının brifingini ısıtır (hash eşleşirse 0 çağrı)."""
    store = app.config.get("EXPERT_STORE")
    if store is None:
        return
    try:
        experts = store.list_all()
    except Exception:
        log.exception("uzman brifingi ısıtma: uzman listesi alınamadı")
        return
    for e in experts:
        for view in _views_of(e):
            try:
                _compute_and_store(app, e, view)
            except Exception:
                log.exception("uzman brifingi ısıtma: %s/%s başarısız",
                              getattr(e, "id", "?"), view.get("key"))


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
    """expert_id verilirse o uzmanın TÜM bakış kayıtları (prefix) temizlenir."""
    if expert_id is None:
        _CACHE.clear()
    else:
        pref = f"{expert_id}::"
        for k in [k for k in _CACHE if k == expert_id or k.startswith(pref)]:
            _CACHE.pop(k, None)


_ASK_PROMPT_PATH = Path(__file__).parent / "prompts" / "expert_ask.txt"


def answer_question(expert, question: str, context: dict | None = None,
                    process_ids: list[str] | None = None) -> str:
    """W4b→W8 — "…'ye sor": senkron tek-tur cevap (SSE muadili backlog).

    Bağlam = persona + süreç dökümanları + metrikler + GÜNCEL Aşama-B süreç
    değerlendirmeleri; sayı kısıtı prompt'ta. W6c: ``context =
    {slide_text, block_id}`` "ŞU AN GÖSTERİLEN SLAYT" olarak eklenir. W8:
    process_ids verilirse cevap YALNIZ o departman bakışının süreçlerine
    dayanır (kapsam sızması olmaz). LLM yoksa/hata: dürüst yönlendirme."""
    question = (question or "").strip()[:500]
    if not question:
        return "Soru boş görünüyor."
    try:
        documented = [p for p in _full_processes(expert, process_ids)
                      if p.get("documented")]
    except Exception:
        documented = []
    llm = current_app.config.get("LLM_CLIENT")
    if llm is not None and documented:
        try:
            user = _build_user_prompt(expert, documented, get_process_metrics())
            # W5c — güncel süreç değerlendirmeleri (varsa) bağlama eklenir;
            # SORU her zaman en sonda kalır.
            from prisma_home import evaluation

            eval_lines = []
            for p in documented:
                rec = evaluation.get_process_evaluation(p["id"])
                if rec:
                    eval_lines.append(f"- {rec.get('label', p['id'])}: {rec['text']}")
            if eval_lines:
                user += ("\n\nSÜREÇ DEĞERLENDİRMELERİ (güncel — sayı için "
                         "bunları da kullanabilirsin):\n" + "\n".join(eval_lines))
            # W6c — sunum modalı bağlamı: izlenen slide + bloğun güncel
            # Aşama-A değerlendirmesi. SORU her zaman en sonda kalır.
            if isinstance(context, dict):
                slide_text = str(context.get("slide_text") or "").strip()[:500]
                block_id = str(context.get("block_id") or "").strip()[:64]
                if slide_text:
                    user += f"\n\nŞU AN GÖSTERİLEN SLAYT: {slide_text}"
                if block_id:
                    brec = evaluation.get_block_evaluation(block_id)
                    if brec:
                        user += (f"\nSlayttaki blok: {brec.get('title', block_id)}"
                                 f" — güncel değerlendirmesi: {brec.get('text')}")
            user += f"\n\nSORU: {question}"
            raw = (llm.complete(_ASK_PROMPT_PATH.read_text(encoding="utf-8"),
                                user, max_tokens=500, temperature=0.3) or "").strip()
            if raw and not raw.startswith("{"):
                return raw
        except Exception:
            log.exception("uzman sorusu cevaplanamadı (%s)", expert.id)
    return ("Şu an canlı cevap üretemiyorum (LLM erişilemedi). Sorunun cevabı "
            "büyük olasılıkla süreç panolarında — aşağıdaki Süreçler bölümünden "
            "ilgili panoya bakabilirsin.")

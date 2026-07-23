"""Atıf token parser'ı — Süreç Düzenlileştirme W5b (plan §3.5 W5 kontrat 3).

LLM, cümle sonlarına ``[[blok:<block_id>]]`` token'ı yazar (Qwen'de tool
calling kırık → metin token'ı + sunucu doğrulaması). Bu modül token'ları
parse eder, İZİNLİ id kümesine karşı doğrular ve render'a hazır segment
listesi üretir:

    {"text":     "token'sız düz metin",
     "segments": [{"text": "...", "cites": ["camon_wf"]}, ...],
     "cites":    ["camon_wf", ...]}          # sıralı, tekrarsız

Kurallar:
- Geçersiz/uydurma id SESSİZCE düşer, cümle metni bozulmadan kalır.
- Ardışık token'lar aynı segmentin cites listesinde birikir.
- Token'sız metin tek segmentli (cites=[]) döner — eski cache'ler ve
  fallback metinleri çipsiz düzgün render olur (W5c kabul kriteri).

Saf modül: flask'a bağımlı değil (test edilebilirlik).
"""
from __future__ import annotations

import re
from typing import Iterable

#: ``[[blok:<id>]]`` — id: harf/rakam/nokta/altçizgi/tire. Önündeki boşluklar
#: token'la birlikte yutulur (cümle sonunda "metin [[blok:x]]" → "metin").
_TOKEN_RE = re.compile(r"\s*\[\[\s*blok\s*:\s*([A-Za-z0-9_.\-]+)\s*\]\]")


def parse_citations(raw: str | None, allowed_ids: Iterable[str]) -> dict:
    """Ham LLM metnini doğrulanmış atıf segmentlerine ayırır."""
    allowed = set(allowed_ids or [])
    raw = (raw or "").strip()
    if not raw:
        return {"text": "", "segments": [], "cites": []}

    segments: list[dict] = []
    cites_order: list[str] = []
    buf = ""          # aktif segmentin metni
    buf_cites: list[str] = []
    pos = 0

    def _flush():
        nonlocal buf, buf_cites
        text = buf.strip()
        if text or buf_cites:
            segments.append({"text": text, "cites": list(buf_cites)})
        buf, buf_cites = "", []

    for m in _TOKEN_RE.finditer(raw):
        buf += raw[pos:m.start()]
        pos = m.end()
        bid = m.group(1)
        if bid in allowed:
            if bid not in buf_cites:
                buf_cites.append(bid)
            if bid not in cites_order:
                cites_order.append(bid)
        # Geçersiz id: token metinden silinir, segment birikmeye devam eder.
        if buf_cites:
            # Ardışık token kontrolü: sıradaki şey yine token ise flush etme,
            # aynı segmentte biriksin.
            nxt = _TOKEN_RE.match(raw, pos)
            if nxt is None:
                # Token'dan hemen sonra gelen noktalama önceki cümleye aittir
                # ("...kaynaklı [[blok:x]]." deseni) — segmente yut, sonraki
                # segment nokta ile başlamasın.
                pm = re.match(r"\s*([.!?,;:]+)", raw[pos:])
                if pm:
                    buf += pm.group(1)
                    pos += pm.end()
                _flush()
    buf += raw[pos:]
    _flush()

    text = " ".join(s["text"] for s in segments if s["text"]).strip()
    # Token silinince oluşabilecek çift boşluk / boşluk+noktalama düzeltmesi.
    text = re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", text))
    for s in segments:
        s["text"] = re.sub(r"\s+([.,;:!?])", r"\1",
                           re.sub(r"[ \t]{2,}", " ", s["text"]))
    return {"text": text, "segments": segments, "cites": cites_order}

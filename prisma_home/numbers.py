"""Sayı doğrulayıcı — Süreç Düzenlileştirme W7a (halüsinasyon guard).

Piramidin her aşamasında LLM çıktısındaki BİRİME BAĞLI sayıları (bps, %, ₺,
pp, M/B, adet, gün...) kaynak havuzuna karşı doğrular. Kaynak havuzunda karşılığı
olmayan bir sayı içeren cümle/madde DÜŞÜRÜLÜR — böylece "yalnız digest'teki
sayıları kullan" kuralı prompt'a güvenmekten ENFORCE edilmişe döner
(citations.py'nin sayısal kardeşi).

Tasarım kararı — PRECISION önceliği: iyi bir brifingi yanlışlıkla kesmek (false
positive), bir uydurma sayıyı kaçırmaktan (false negative) daha kötüdür — çünkü
tüm cümleler düşerse aşama fallback'e gider (dürüst ama içeriksiz). Bu yüzden:
- Yalnız BİRİME BAĞLI sayılar denetlenir (çıplak "3", tarih parçaları, sıra
  sayıları denetlenmez — bunlar yapısaldır, uydurma metrik değil).
- Eşleştirme CÖMERTtir: hem tam-sayı yuvarlaması hem 2-ondalık; ayraç
  belirsizliği (Türkçe '.'-binlik/','-ondalık vs Python '{:,.2f}') için sayı
  birden çok yorumla havuza girer. Kaynakta 487.2 varken çıktının 487 yazması
  (meşru yuvarlama) EŞLEŞİR; 42 varken 58 yazması eşleşmez → düşer.

Saf modül: flask'a bağımlı değil (test edilebilirlik).
"""
from __future__ import annotations

import re
from typing import Iterable

#: Madde satırı (citations/commentary ile aynı imza).
_BULLET_RE = re.compile(r"^\s*[-•*]\s+")

#: Ham sayı token'ı (işaret + rakam + ayraçlar).
_NUM = r"[-+]?\d[\d.,]*"

#: Birime bağlı sayı: ₺/% önde YA DA bps/pp/M/B/TL/... arkada. Yakalanan
#: gruplardan hangisi doluysa sayıdır.
_UNIT_NUM = re.compile(
    r"₺\s?(" + _NUM + r")"
    r"|(" + _NUM + r")\s?(?:bps|baz\s?puan|puan|pp|%|M|B|T|TL|milyar|milyon|adet|gün)\b"
    r"|%\s?(" + _NUM + r")"
)

#: Cümle sınırı — sayı-içi nokta ('%4.8', '31.05.2026') bölmez; yalnız
#: cümle-sonu noktalama + boşluk.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _values(token: str) -> set[float]:
    """Bir sayı token'ının olası float değerleri (ayraç belirsizliği için çoklu)."""
    t = token.replace("₺", "").replace("+", "").replace("-", "").replace(" ", "")
    if not re.search(r"\d", t):
        return set()
    out: set[float] = set()
    for variant in (t.replace(",", ""),                       # ',' binlik
                    t.replace(".", "").replace(",", "."),     # '.' binlik ',' ond.
                    t.replace(",", ".")):                     # tek ayraç ondalık
        try:
            out.add(float(variant))
        except ValueError:
            pass
    return out


def _keys(token: str) -> set:
    """Token → eşleştirme anahtarları (tam-sayı + 2-ondalık yuvarlama)."""
    ks: set = set()
    for v in _values(token):
        ks.add(round(v))
        ks.add(round(v, 2))
    return ks


def _pool_keys(sources: Iterable[str]) -> set:
    """İzinli havuz: kaynak metinlerdeki TÜM sayılar (birim şartı yok — cömert)."""
    ks: set = set()
    for s in sources:
        for m in re.finditer(_NUM, s or ""):
            ks |= _keys(m.group())
    return ks


def _unit_numbers(text: str) -> list[str]:
    """Metindeki birime bağlı sayı token'ları."""
    out = []
    for m in _UNIT_NUM.finditer(text or ""):
        tok = m.group(1) or m.group(2) or m.group(3)
        if tok:
            out.append(tok)
    return out


def unit_number_keys(text: str) -> set:
    """Metindeki BİRİME BAĞLI sayıların eşleştirme anahtarları (FB5 auto-atıf).

    Bir maddenin sayılarının hangi bloğun verisinden geldiğini bulmak için
    kullanılır: blok değerlendirmesinin anahtar kümesiyle kesişim → o blok."""
    ks: set = set()
    for tok in _unit_numbers(text or ""):
        ks |= _keys(tok)
    return ks


def validate_numbers(text: str | None, allowed_sources: Iterable[str]) -> dict:
    """Desteklenmeyen birim-sayılı cümle/maddeleri düşürür.

    Dönüş: {"text": temizlenmiş metin, "dropped": [düşen birimler],
    "flagged": int}. Madde satırı (- ...) tekil birimdir (bad → madde düşer);
    düz satır cümlelere bölünür. Newline/madde yapısı korunur → citations /
    _parse_briefing sonrasında çalıştırılabilir."""
    raw = text or ""
    if not raw.strip():
        return {"text": "", "dropped": [], "flagged": 0}
    allowed = _pool_keys(allowed_sources)

    out_lines: list[str] = []
    dropped: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        if _BULLET_RE.match(line):
            bad = [n for n in _unit_numbers(line) if not (_keys(n) & allowed)]
            if bad:
                dropped.append(line.strip())
            else:
                out_lines.append(line)
            continue
        # Düz satır: cümle bazında ele.
        kept = []
        for sent in _SENT_SPLIT.split(line):
            if not sent.strip():
                continue
            bad = [n for n in _unit_numbers(sent) if not (_keys(n) & allowed)]
            if bad:
                dropped.append(sent.strip())
            else:
                kept.append(sent.strip())
        if kept:
            out_lines.append(" ".join(kept))

    cleaned = "\n".join(out_lines).strip()
    return {"text": cleaned, "dropped": dropped, "flagged": len(dropped)}

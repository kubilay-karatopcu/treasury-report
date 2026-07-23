"""W7a — sayı doğrulayıcı (numbers.py). Saf modül; flask gerekmez.

Kontrat: birime bağlı, kaynakta karşılığı OLMAYAN sayı içeren cümle/madde
düşer; doğru sayı (yeniden formatlansa da) geçer; çıplak sayı denetlenmez;
tüm cümleler düşerse text boş → çağıran fallback'e gider.
"""
from __future__ import annotations

from prisma_home.numbers import validate_numbers


SRC = ["Mix etkisi +42 bps", "WAvg faiz %4.80", "Toplam Stok ₺487M",
       "Fark -12 pp", "Bitiş 3,210 bps"]


def _clean(text, src=SRC):
    return validate_numbers(text, src)


def test_supported_number_passes():
    out = _clean("Maliyet mix kaynaklı, +42 bps katkı var.")
    assert out["flagged"] == 0
    assert "+42 bps" in out["text"]


def test_fabricated_number_sentence_dropped():
    out = _clean("Maliyet mix kaynaklı. Faiz +58 bps arttı.")
    assert out["flagged"] == 1
    assert "58" not in out["text"]
    assert "Maliyet mix kaynaklı." in out["text"]


def test_reformatted_number_matches():
    # Kaynak %4.80 → çıktı Türkçe '%4,8' / yuvarlanmış '%5' meşru sayılır.
    assert _clean("Ortalama faiz %4,8 seviyesinde.")["flagged"] == 0
    assert _clean("Ortalama faiz %5 civarı.")["flagged"] == 0


def test_thousands_and_currency_variants():
    assert _clean("Stok ₺487M oldu.")["flagged"] == 0
    assert _clean("Köprü 3.210 bps.")["flagged"] == 0          # '.' binlik
    assert _clean("Köprü 9.999 bps.")["flagged"] == 1          # yok → düşer


def test_bare_numbers_not_checked():
    # Birim yok → yapısal sayı, denetlenmez (false-positive önleme).
    out = _clean("İlk 3 sinyale bakıyoruz, 2026 yılı için.")
    assert out["flagged"] == 0
    assert out["text"].strip() != ""


def test_bullets_dropped_individually():
    raw = ("- Maliyet mix kaynaklı +42 bps [[blok:x]].\n"
           "- Faiz +58 bps fırladı [[blok:y]].\n"
           "- Fark -12 pp genişledi.")
    out = _clean(raw)
    assert out["flagged"] == 1
    lines = out["text"].splitlines()
    assert len(lines) == 2                       # ikinci madde düştü
    assert "58" not in out["text"]
    assert any("+42 bps" in l for l in lines)
    assert any("-12 pp" in l for l in lines)


def test_all_fabricated_yields_empty():
    out = _clean("Faiz +58 bps. Stok ₺999M.")
    assert out["text"] == ""                      # → çağıran fallback'e gider
    assert out["flagged"] == 2


def test_empty_and_none():
    assert validate_numbers(None, SRC)["text"] == ""
    assert validate_numbers("   ", SRC)["text"] == ""


def test_empty_pool_flags_unit_numbers():
    # Kaynakta sayı yoksa birim-sayı = uydurma → düşer.
    assert validate_numbers("Faiz %4.80 arttı.", [])["flagged"] == 1
    # Ama çıplak sayı yine geçer.
    assert validate_numbers("Üç sinyal var.", [])["flagged"] == 0

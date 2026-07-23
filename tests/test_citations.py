"""W5b — [[blok:<id>]] atıf token parser'ı + doğrulayıcısı (citations.py).

Kontrat (plan §3.5 W5 madde 3): geçersiz/uydurma id sessizce düşer, cümle
kalır; ardışık token'lar aynı segmentte birikir; token'sız metin tek segment.
Saf modül — flask gerekmez.
"""
from __future__ import annotations

from prisma_home.citations import parse_citations

ALLOWED = {"camon_wf", "np_rvhm", "bamon_bridge"}


def test_empty_and_none():
    for raw in (None, "", "   "):
        out = parse_citations(raw, ALLOWED)
        assert out == {"text": "", "segments": [], "cites": []}


def test_plain_text_single_segment_no_cites():
    out = parse_citations("Maliyet stabil seyrediyor.", ALLOWED)
    assert out["text"] == "Maliyet stabil seyrediyor."
    assert out["segments"] == [{"text": "Maliyet stabil seyrediyor.",
                                "cites": []}]
    assert out["cites"] == []


def test_valid_citation_attached_and_stripped():
    out = parse_citations(
        "Maliyet artışı mix kaynaklı [[blok:camon_wf]]. Fiyatlama sakin.",
        ALLOWED)
    assert "[[" not in out["text"]
    assert out["segments"][0]["cites"] == ["camon_wf"]
    assert out["segments"][0]["text"].startswith("Maliyet artışı")
    assert out["segments"][1] == {"text": "Fiyatlama sakin.", "cites": []}
    assert out["cites"] == ["camon_wf"]


def test_invalid_id_dropped_sentence_kept():
    out = parse_citations("Bulgu bir [[blok:uydurma_id]]. Bulgu iki "
                          "[[blok:np_rvhm]].", ALLOWED)
    assert "uydurma_id" not in str(out)
    assert out["cites"] == ["np_rvhm"]
    # İlk cümlenin metni korunur (token silinir, segment akar).
    assert "Bulgu bir" in out["text"] and "Bulgu iki" in out["text"]


def test_consecutive_tokens_accumulate_one_segment():
    out = parse_citations(
        "Vade açığı büyüyor [[blok:camon_wf]] [[blok:bamon_bridge]].",
        ALLOWED)
    seg = out["segments"][0]
    assert seg["cites"] == ["camon_wf", "bamon_bridge"]
    assert out["cites"] == ["camon_wf", "bamon_bridge"]


def test_duplicate_citation_deduped_in_order():
    out = parse_citations(
        "A [[blok:np_rvhm]]. B [[blok:camon_wf]]. C [[blok:np_rvhm]].",
        ALLOWED)
    assert out["cites"] == ["np_rvhm", "camon_wf"]


def test_whitespace_and_case_tolerant_token():
    out = parse_citations("Bulgu [[ blok : camon_wf ]].", ALLOWED)
    assert out["cites"] == ["camon_wf"]
    assert "[[" not in out["text"]


def test_punctuation_cleanup_after_strip():
    out = parse_citations("Sinyal net [[blok:camon_wf]] .", ALLOWED)
    assert out["text"] == "Sinyal net."

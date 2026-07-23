"""W5a — blok değerlendirmesi: digest sağlayıcı, hash'li cache, fallback.

Kabul kriterleri (docs/PROCESS_REGULARIZATION_PLAN.md §3.5 W5a):
- digest ≤15 satır döner; sağlayıcı hatası boş listeye düşer,
- DEV/FakeLLM ya da LLM'siz ortamda deterministik dürüst stub,
- digest + dökümantasyon değişmeden ikinci tur 0 LLM çağrısı.
"""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home import evaluation
from prisma_home.evaluation import (
    evaluate_all_blocks,
    evaluate_block,
    get_block_digest,
    get_block_evaluation,
)


class _CountingLLM:
    """Geçerli düz metin döner; çağrı sayar (0-çağrı kabul kriteri için)."""
    def __init__(self, text="Waterfall'da en büyük pozitif çubuk mix etkisi; "
                            "fiyat aksiyonundan önce ürün yönlendirmesi gerekir."):
        self.text = text
        self.calls = 0

    def complete(self, system, user, **kw):
        self.calls += 1
        return self.text


class _BrokenLLM:
    def complete(self, *a, **kw):
        raise RuntimeError("network down")


def _mk_app(llm=None, digests=None) -> Flask:
    app = Flask(__name__)
    if llm is not None:
        app.config["LLM_CLIENT"] = llm
    if digests is not None:
        app.config["PROCESS_BLOCK_DIGESTS"] = digests
    return app


def _block(bid="camon_wf", **doc_over):
    doc = {
        "purpose": "Dönem faiz maliyetini bileşenlere ayıran waterfall.",
        "business_context": "Fiyat mı mix mi sorusu aksiyon hedefini belirler.",
        "decision_support": "En büyük pozitif çubuk ana sürükleyicidir.",
        "known_limitations": "Tekil büyük müşteri çubuğu domine edebilir.",
    }
    doc.update(doc_over)
    return {"id": bid, "title": "Deposit Rate Waterfall",
            "documentation": doc, "documented": True}


_DIGEST = [
    {"k": "Başlangıç", "v": "3.210 bps", "delta": "", "tone": ""},
    {"k": "Mix etkisi", "v": "+42 bps", "delta": "", "tone": "neg"},
    {"k": "Fiyat etkisi", "v": "+11 bps", "delta": "", "tone": "neg"},
]


@pytest.fixture(autouse=True)
def _clean_cache():
    evaluation.invalidate()
    yield
    evaluation.invalidate()


# ── get_block_digest ────────────────────────────────────────────────────────

class TestDigestProvider:
    def test_missing_registry_yields_empty(self):
        with _mk_app().test_request_context():
            assert get_block_digest("camon_wf") == {"rows": [], "view": None}

    def test_provider_error_yields_empty(self):
        def boom():
            raise RuntimeError("engine cold")
        with _mk_app(digests={"camon_wf": boom}).test_request_context():
            assert get_block_digest("camon_wf") == {"rows": [], "view": None}

    def test_rows_capped_at_15(self):
        rows = [{"k": f"m{i}", "v": str(i)} for i in range(40)]
        with _mk_app(digests={"b": lambda: rows}).test_request_context():
            assert len(get_block_digest("b")["rows"]) == 15

    def test_dict_contract_with_view_passthrough(self):
        """W6b — yeni sözleşme: {"rows", "view"} aynen geçer; view A kaydına düşer."""
        digest = {"rows": list(_DIGEST),
                  "view": {"label": "Dönem: X → Y",
                           "controls": [{"id": "ca-mon-date0", "value": "2026-06-30"}]}}
        app = _mk_app(llm=_CountingLLM(), digests={"camon_wf": lambda: digest})
        with app.test_request_context():
            assert get_block_digest("camon_wf")["view"]["label"] == "Dönem: X → Y"
            evaluate_block("mevduat.maliyet", "Cost", _block())
            rec = get_block_evaluation("camon_wf")
        assert rec["view"]["controls"][0]["id"] == "ca-mon-date0"


# ── evaluate_block: cache + LLM disiplini ───────────────────────────────────

class TestEvaluateBlock:
    def test_llm_text_cached_and_no_second_call(self):
        llm = _CountingLLM()
        app = _mk_app(llm=llm, digests={"camon_wf": lambda: _DIGEST})
        with app.test_request_context():
            assert evaluate_block("mevduat.maliyet", "Cost", _block()) is True
            rec = get_block_evaluation("camon_wf")
            assert rec["text"] == llm.text
            assert rec["has_data"] is True
            # Değişmeden ikinci tur: hesap YOK, LLM çağrısı YOK.
            assert evaluate_block("mevduat.maliyet", "Cost", _block()) is False
        assert llm.calls == 1

    def test_digest_change_recomputes(self):
        llm = _CountingLLM()
        digest = {"rows": list(_DIGEST)}
        app = _mk_app(llm=llm, digests={"camon_wf": lambda: digest["rows"]})
        with app.test_request_context():
            evaluate_block("mevduat.maliyet", "Cost", _block())
            digest["rows"] = _DIGEST + [{"k": "Bitiş", "v": "3.263 bps"}]
            assert evaluate_block("mevduat.maliyet", "Cost", _block()) is True
        assert llm.calls == 2

    def test_doc_change_recomputes(self):
        llm = _CountingLLM()
        app = _mk_app(llm=llm, digests={"camon_wf": lambda: _DIGEST})
        with app.test_request_context():
            evaluate_block("mevduat.maliyet", "Cost", _block())
            evaluate_block("mevduat.maliyet", "Cost",
                           _block(decision_support="Yeni kural."))
        assert llm.calls == 2

    def test_no_digest_no_llm_call_honest_fallback(self):
        llm = _CountingLLM()
        app = _mk_app(llm=llm)  # digest sağlayıcı yok
        with app.test_request_context():
            evaluate_block("mevduat.maliyet", "Cost", _block())
            rec = get_block_evaluation("camon_wf")
        assert llm.calls == 0
        assert rec["has_data"] is False
        assert "digest" in rec["text"]

    def test_broken_llm_falls_back(self):
        app = _mk_app(llm=_BrokenLLM(), digests={"camon_wf": lambda: _DIGEST})
        with app.test_request_context():
            evaluate_block("mevduat.maliyet", "Cost", _block())
            rec = get_block_evaluation("camon_wf")
        assert rec is not None and len(rec["text"]) > 20

    def test_json_leak_rejected(self):
        app = _mk_app(llm=_CountingLLM(text='{"html": "çöp"}'),
                      digests={"camon_wf": lambda: _DIGEST})
        with app.test_request_context():
            evaluate_block("mevduat.maliyet", "Cost", _block())
            rec = get_block_evaluation("camon_wf")
        assert not rec["text"].startswith("{")


# ── evaluate_all_blocks: registry taraması ──────────────────────────────────

class TestEvaluateAll:
    def test_second_round_all_cached(self):
        llm = _CountingLLM()
        app = _mk_app(llm=llm, digests={"camon_wf": lambda: _DIGEST})
        s1 = evaluate_all_blocks(app)
        calls_after_first = llm.calls
        s2 = evaluate_all_blocks(app)
        assert s1["blocks"] == s2["blocks"] > 0
        assert s2["computed"] == 0
        assert s2["cached"] == s2["blocks"]
        assert llm.calls == calls_after_first  # ikinci tur 0 LLM çağrısı

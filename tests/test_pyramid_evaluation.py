"""W5b — piramit Aşama B (süreç) + Aşama C (uzman brifingi) + tam zincir.

Kabul kriterleri (plan §3.5 W5b):
- uydurma blok id'si atıflardan düşer (cümle kalır),
- istek yolu (get_commentary) hiçbir aşamayı beklemez — cache/fallback anında,
- her aşamanın dürüst fallback'i var,
- girdiler değişmeden ikinci tam tur 0 LLM çağrısı (hash zinciri).
"""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home import commentary, evaluation


class _CountingLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, system, user, **kw):
        self.calls += 1
        self.last_user = user
        return self.text


class _StubExpert:
    id = "dep"
    name = "Mevduat Uzmanı"
    domain_label = "Mevduat"
    short_description = "TL mevduat maliyeti ve hacmi."
    bound_content = {"processes": ["mevduat.maliyet"]}


class _StubStore:
    def list_all(self):
        return [_StubExpert()]


def _mk_app(llm=None, digests=None, store=None) -> Flask:
    app = Flask(__name__)
    if llm is not None:
        app.config["LLM_CLIENT"] = llm
    if digests is not None:
        app.config["PROCESS_BLOCK_DIGESTS"] = digests
    app.config["EXPERT_STORE"] = store or _StubStore()
    return app


_DIGEST = [{"k": "Mix etkisi", "v": "+42 bps", "delta": "", "tone": "neg"}]

#: mevduat.maliyet'in üç dökümante bloğu için digest kaydı.
_DIGESTS = {bid: (lambda: list(_DIGEST))
            for bid in ("camon_wf", "camon_bubble", "camon_ratehm")}


@pytest.fixture(autouse=True)
def _clean():
    evaluation.invalidate()
    commentary.invalidate()
    yield
    evaluation.invalidate()
    commentary.invalidate()


# ── Aşama B — süreç değerlendirmesi ─────────────────────────────────────────

class TestProcessEvaluation:
    def test_citations_validated_and_cached(self):
        llm = _CountingLLM(
            "Maliyet bozulması mix kaynaklı görünüyor [[blok:camon_wf]]. "
            "Uydurma dayanak [[blok:boyle_blok_yok]]. Isı haritası yapısal "
            "yayılım göstermiyor [[blok:camon_ratehm]].")
        app = _mk_app(llm=llm, digests=_DIGESTS)
        evaluation.evaluate_all_blocks(app)
        calls_a = llm.calls
        s1 = evaluation.evaluate_all_processes(app)
        assert s1["processes"] >= 1 and s1["computed"] >= 1

        rec = evaluation.get_process_evaluation("mevduat.maliyet")
        assert rec is not None
        assert "boyle_blok_yok" not in str(rec)      # uydurma id düştü
        assert set(rec["cites"]) == {"camon_wf", "camon_ratehm"}
        assert "[[" not in rec["text"]               # token'lar temizlendi
        assert rec["block_titles"]["camon_wf"]       # katalog W5c için hazır

        # Değişmeden ikinci tur: B tamamen cache'ten, LLM çağrısı yok.
        before = llm.calls
        s2 = evaluation.evaluate_all_processes(app)
        assert s2["computed"] == 0 and llm.calls == before
        assert calls_a >= 1  # A gerçekten LLM'e gitmişti (sanity)

    def test_no_block_evals_honest_fallback_without_llm(self):
        llm = _CountingLLM("kullanılmamalı")
        app = _mk_app(llm=llm)  # digest yok → A cache boş
        evaluation.evaluate_all_processes(app)
        rec = evaluation.get_process_evaluation("mevduat.maliyet")
        assert rec is not None and rec["cites"] == []
        assert llm.calls == 0

    def test_block_change_propagates_to_process(self):
        llm = _CountingLLM("Sentez [[blok:camon_wf]].")
        digest = {"rows": list(_DIGEST)}
        app = _mk_app(llm=llm,
                      digests={"camon_wf": lambda: digest["rows"]})
        evaluation.evaluate_all_blocks(app)
        evaluation.evaluate_all_processes(app)
        n1 = llm.calls
        # Blok verisi değişir → A yeniden hesaplar → children_hash değişir →
        # B kendiliğinden tazelenir (yukarı yayılım).
        digest["rows"] = _DIGEST + [{"k": "Fiyat etkisi", "v": "+11 bps"}]
        evaluation.evaluate_all_blocks(app)
        s = evaluation.evaluate_all_processes(app)
        assert s["computed"] >= 1 and llm.calls > n1


# ── Aşama C — uzman brifingi ────────────────────────────────────────────────

class TestExpertBriefing:
    def test_briefing_cites_and_record(self):
        llm = _CountingLLM(
            "Maliyet tarafında baskı mix kaynaklı [[blok:camon_wf]]. "
            "Balon dağılımı yeniden fiyatlama adayı gösteriyor "
            "[[blok:camon_bubble]] [[blok:sahte_id]].")
        app = _mk_app(llm=llm, digests=_DIGESTS)
        commentary.refresh_pipeline(app)

        rec = commentary.get_commentary_record("dep")
        assert rec is not None
        assert rec["cites"] == ["camon_wf", "camon_bubble"]
        assert "sahte_id" not in str(rec["cites"])
        assert "[[" not in rec["text"]
        assert rec["segments"][0]["cites"] == ["camon_wf"]
        # Prompt'a B değerlendirmeleri + katalog girdi (sayı zinciri kaynağı).
        assert "SÜREÇ DEĞERLENDİRMELERİ" in llm.last_user
        assert "ATIF KATALOĞU" in llm.last_user

    def test_full_second_round_zero_llm_calls(self):
        llm = _CountingLLM("Bulgu [[blok:camon_wf]].")
        app = _mk_app(llm=llm, digests=_DIGESTS)
        commentary.refresh_pipeline(app)
        n = llm.calls
        commentary.refresh_pipeline(app)   # hiçbir girdi değişmedi
        assert llm.calls == n

    def test_request_path_never_blocks(self):
        """get_commentary LLM'siz app'te bile anında dürüst metin döner."""
        class _OtherExpert(_StubExpert):
            id = "dep2"   # arka plan thread'i "dep" kaydını ezmesin (izolasyon)
        app = _mk_app()  # LLM yok, digest yok
        with app.test_request_context():
            text = commentary.get_commentary(_OtherExpert())
        assert text and "hazırlanıyor" in text

        # Piramit koşunca istek yolu cache'lenen kaydı döner.
        llm = _CountingLLM("Sinyal net [[blok:camon_wf]].")
        app2 = _mk_app(llm=llm, digests=_DIGESTS)
        commentary.refresh_pipeline(app2)
        with app2.test_request_context():
            text2 = commentary.get_commentary(_StubExpert())
        assert text2 == commentary.get_commentary_record("dep")["text"]

    def test_broken_llm_every_stage_falls_back(self):
        class _Broken:
            def complete(self, *a, **kw):
                raise RuntimeError("down")
        app = _mk_app(llm=_Broken(), digests=_DIGESTS)
        commentary.refresh_pipeline(app)
        b = evaluation.get_block_evaluation("camon_wf")
        p = evaluation.get_process_evaluation("mevduat.maliyet")
        c = commentary.get_commentary_record("dep")
        assert b and len(b["text"]) > 20 and b["is_fallback"]
        assert p and len(p["text"]) > 20 and p["cites"] == []
        assert c and len(c["text"]) > 20 and c["cites"] == []

    def test_fallback_heals_when_llm_recovers(self):
        """Geçici LLM hatası fallback'i KİLİTLEMEZ: girdiler değişmese de
        sonraki tur yeniden dener ve gerçek metin fallback'i ezer
        (2026-07-23 'Brifing henüz hazır değil takılı kaldı' geri bildirimi)."""
        class _Flaky:
            def __init__(self):
                self.fail = True
                self.calls = 0
            def complete(self, system, user, **kw):
                self.calls += 1
                if self.fail:
                    raise RuntimeError("geçici kesinti")
                return "Toparlanma sonrası gerçek bulgu [[blok:camon_wf]]."

        llm = _Flaky()
        app = _mk_app(llm=llm, digests=_DIGESTS)
        commentary.refresh_pipeline(app)          # tüm aşamalar fallback
        assert commentary.get_commentary_record("dep")["is_fallback"]

        llm.fail = False
        commentary.refresh_pipeline(app)          # girdi hash'leri AYNI
        b = evaluation.get_block_evaluation("camon_wf")
        c = commentary.get_commentary_record("dep")
        assert not b["is_fallback"] and "gerçek bulgu" in b["text"]
        assert not c["is_fallback"] and c["cites"] == ["camon_wf"]

        # İyileştikten sonra üçüncü tur: hash'ler tutar, 0 çağrı.
        n = llm.calls
        commentary.refresh_pipeline(app)
        assert llm.calls == n

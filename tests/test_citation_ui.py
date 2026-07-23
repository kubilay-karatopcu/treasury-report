"""W5c — atıf UI backend'i: _citation_entries çözücüsü + "sor" bağlam yükseltmesi.

Kabul (plan §3.5 W5c): çip/kaynakça girdisi doğru embed URL'ini taşır;
registry'den kalkmış blok atıfı sessizce düşer ve numara yeniden dizilir;
answer_question güncel Aşama-B değerlendirmelerini bağlama ekler (SORU sonda).
"""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home import commentary, evaluation
from prisma_home.routes import _citation_entries


_FAKE_PROCESSES = {
    "mevduat.maliyet": {
        "id": "mevduat.maliyet", "label": "Cost Analysis",
        "blocks": [
            {"id": "camon_wf", "title": "Deposit Rate Waterfall",
             "render_url": "/mevduat-panel/?page=cost-analysis",
             "custom_render": {"anchor": "acc-btn-ca-mon-wf"}},
            {"id": "camon_ratehm", "title": "Interest Rate Heatmap",
             "render_url": "/mevduat-panel/?page=cost-analysis",
             "custom_render": {"anchor": None}},
        ],
    },
}


@pytest.fixture
def fake_get_process(monkeypatch):
    monkeypatch.setattr("prisma_home.processes.get_process",
                        lambda pid: _FAKE_PROCESSES.get(pid))


class TestCitationEntries:
    def test_embed_url_and_numbering(self, fake_get_process):
        record = {"cites": ["camon_wf", "camon_ratehm"]}
        out = _citation_entries(record, ["mevduat.maliyet"])
        assert [c["num"] for c in out] == [1, 2]
        assert out[0]["url"] == ("/mevduat-panel/?page=cost-analysis"
                                 "&embed=1&anchor=acc-btn-ca-mon-wf")
        # Anchor'sız blok: yalnız embed parametresi.
        assert out[1]["url"] == "/mevduat-panel/?page=cost-analysis&embed=1"
        assert out[0]["title"] == "Deposit Rate Waterfall"
        assert out[0]["process"] == "Cost Analysis"

    def test_unknown_cite_dropped_and_renumbered(self, fake_get_process):
        record = {"cites": ["kalkmis_blok", "camon_ratehm"]}
        out = _citation_entries(record, ["mevduat.maliyet"])
        assert len(out) == 1
        assert out[0]["id"] == "camon_ratehm" and out[0]["num"] == 1

    def test_empty_record(self, fake_get_process):
        assert _citation_entries({"cites": []}, ["mevduat.maliyet"]) == []
        assert _citation_entries({}, ["mevduat.maliyet"]) == []

    def test_view_state_rides_in_url(self, fake_get_process):
        """W6b — A kaydındaki digest view'i state paramı + label olur."""
        import base64
        import json as _json

        evaluation._EVAL["camon_wf"] = {
            "text": "x", "view": {
                "label": "Dönem: 31.05.2026 → 30.06.2026",
                "controls": [{"id": "ca-mon-date0", "value": "2026-05-31"},
                             {"id": "ca-mon-date1", "value": "2026-06-30"}],
            },
        }
        try:
            out = _citation_entries({"cites": ["camon_wf", "camon_ratehm"]},
                                    ["mevduat.maliyet"])
        finally:
            evaluation.invalidate()
        assert out[0]["state_label"] == "Dönem: 31.05.2026 → 30.06.2026"
        assert "&state=" in out[0]["url"]
        raw = out[0]["url"].split("&state=")[1]
        raw += "=" * ((4 - len(raw) % 4) % 4)
        decoded = _json.loads(base64.urlsafe_b64decode(raw))
        assert decoded["controls"][0] == {"id": "ca-mon-date0",
                                          "value": "2026-05-31"}
        # View'sız blok: state paramı yok, label boş — eski davranış.
        assert "&state=" not in out[1]["url"]
        assert out[1]["state_label"] == ""


class _CapturingLLM:
    def __init__(self):
        self.last_user = None

    def complete(self, system, user, **kw):
        self.last_user = user
        return "Cevap: maliyet baskısı mix kaynaklı görünüyor."


class TestAskContextUpgrade:
    @pytest.fixture(autouse=True)
    def _clean(self):
        evaluation.invalidate()
        yield
        evaluation.invalidate()

    def test_ask_includes_stage_b_and_question_last(self):
        class _Expert:
            id = "dep"
            name = "Mevduat Uzmanı"
            domain_label = "Mevduat"
            short_description = ""
            bound_content = {"processes": ["mevduat.maliyet"]}

        llm = _CapturingLLM()
        app = Flask(__name__)
        app.config["LLM_CLIENT"] = llm
        # Aşama-B kaydını doğrudan tohumla (pipeline'a gerek yok).
        evaluation._PROC_EVAL["mevduat.maliyet"] = {
            "text": "Maliyet artışı +42 bps mix kaynaklı.",
            "label": "Cost Analysis", "segments": [], "cites": [],
            "doc_hash": "x", "children_hash": "y", "block_titles": {}, "ts": 0,
        }
        with app.test_request_context():
            answer = commentary.answer_question(_Expert(), "maliyet ne durumda?")
        assert answer.startswith("Cevap")
        assert "SÜREÇ DEĞERLENDİRMELERİ" in llm.last_user
        assert "Maliyet artışı +42 bps" in llm.last_user
        # SORU her zaman bağlamın en sonunda.
        assert llm.last_user.rstrip().endswith("SORU: maliyet ne durumda?")

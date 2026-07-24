"""W8 — departman bakışları: resolver/erişim + piramit C çatalı.

Kabul: bakış varsa SIKI erişim (eşleşmeyen departman reddedilir); aynı uzman
altında iki departman farklı süreç setiyle farklı brifing alır; A/B aşamaları
paylaşılır, C bakışa göre çatallanır.
"""
from __future__ import annotations

import pytest
from flask import Flask

from prisma_home import commentary, evaluation
from prisma_home.expert_views import (
    can_access,
    legacy_view,
    list_views,
    resolve_view,
)


# ── Resolver + erişim (saf) ──────────────────────────────────────────────────

class _E:
    access_scope = {"read": ["*"]}
    bound_content = {"processes": ["a", "b"]}
    department_views: list = []


class TestResolver:
    def test_legacy_when_no_views(self):
        e = _E()
        r = resolve_view(e, "X")
        assert r == {"granted": True, "legacy": True, "view": None}
        assert can_access(e, "X") is True
        assert legacy_view(e)["process_ids"] == ["a", "b"]

    def test_legacy_honours_access_scope(self):
        e = _E()
        e.access_scope = {"read": ["Hazine"]}
        assert can_access(e, "Hazine") is True
        assert can_access(e, "Kredi") is False

    def test_strict_grant_and_deny(self):
        e = _E()
        e.department_views = [
            {"departments": ["Bilanço"], "briefing_focus": "b",
             "topics": [{"title": "Stok", "processes": ["a", "b"]},
                        {"title": "Dönüş", "processes": ["c"]}]},
            {"departments": ["Fonlama"], "topics": [{"title": "T", "processes": ["a"]}]},
        ]
        r = resolve_view(e, "Bilanço")
        assert r["granted"] and not r["legacy"]
        assert r["view"]["process_ids"] == ["a", "b", "c"]
        assert r["view"]["briefing_focus"] == "b"
        assert len(r["view"]["topics"]) == 2
        # Eşleşmeyen / departmansız → SIKI reddedilir.
        assert resolve_view(e, "Kredi")["granted"] is False
        assert can_access(e, "Kredi") is False
        assert can_access(e, "") is False
        assert can_access(e, "Fonlama") is True

    def test_view_keys_deterministic_and_distinct(self):
        e = _E()
        e.department_views = [
            {"departments": ["A"], "topics": [{"title": "x", "processes": ["a"]}]},
            {"departments": ["B"], "topics": [{"title": "y", "processes": ["b"]}]},
        ]
        views = list_views(e)
        assert views[0]["key"] != views[1]["key"]
        assert views[0]["key"] == list_views(e)[0]["key"]   # deterministik


# ── Piramit C çatalı ─────────────────────────────────────────────────────────

class _CountingLLM:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def complete(self, system, user, **kw):
        self.calls += 1
        return self.text


class _ViewExpert:
    id = "dep"
    name = "Mevduat Uzmanı"
    domain_label = "Mevduat"
    short_description = ""
    bound_content = {"processes": []}
    department_views = [
        {"departments": ["Bilanço"], "briefing_focus": "bilanço merceği",
         "topics": [{"title": "Stok", "processes": ["mevduat.maliyet"]}]},
        {"departments": ["Fonlama"], "briefing_focus": "fonlama merceği",
         "topics": [{"title": "Bakiye", "processes": ["mevduat.bakiye"]}]},
    ]


class _Store:
    def list_all(self):
        return [_ViewExpert()]


def _mk_app(llm):
    app = Flask(__name__)
    app.config["LLM_CLIENT"] = llm
    app.config["PROCESS_BLOCK_DIGESTS"] = {
        "camon_wf": lambda: {"rows": [{"k": "Mix", "v": "+42 bps"}], "view": None},
        "bamon_bridge": lambda: {"rows": [{"k": "Net", "v": "₺120M"}], "view": None},
    }
    app.config["EXPERT_STORE"] = _Store()
    return app


@pytest.fixture(autouse=True)
def _clean():
    evaluation.invalidate()
    commentary.invalidate()
    yield
    evaluation.invalidate()
    commentary.invalidate()


class TestPyramidFork:
    def test_two_departments_get_distinct_briefings(self):
        # LLM iki maddeyle iki farklı bloğa atıf yapar; her bakışta yalnız
        # o bakışın kataloğundaki atıf geçerli sayılır (diğeri düşer).
        llm = _CountingLLM(
            "- Maliyet mix kaynaklı [[blok:camon_wf]].\n"
            "- Bakiye köprüsü genişledi [[blok:bamon_bridge]].")
        app = _mk_app(llm)
        commentary.refresh_pipeline(app)

        views = list_views(_ViewExpert())
        ka, kb = views[0]["key"], views[1]["key"]
        recA = commentary.get_commentary_record("dep", ka)   # Bilanço → maliyet
        recB = commentary.get_commentary_record("dep", kb)   # Fonlama → bakiye
        assert recA and recB and ka != kb
        # Bilanço bakışı yalnız maliyet bloğunu görür → camon_wf geçer.
        assert recA["cites"] == ["camon_wf"]
        # Fonlama bakışı yalnız bakiye bloğunu görür → bamon_bridge geçer.
        assert recB["cites"] == ["bamon_bridge"]

    def test_shared_stage_b_not_recomputed_per_view(self):
        llm = _CountingLLM("- Sinyal [[blok:camon_wf]].\n- İkinci [[blok:bamon_bridge]].")
        app = _mk_app(llm)
        commentary.refresh_pipeline(app)
        n = llm.calls
        # İkinci tur: her şey hash'ten (A+B+iki C) → 0 çağrı.
        commentary.refresh_pipeline(app)
        assert llm.calls == n

"""Süreç Düzenlileştirme W3 — LLM doc-proposer testleri."""
from __future__ import annotations

from presentations.process.proposer import (
    build_user_prompt,
    propose_documentation,
)

_PROC = {
    "id": "mevduat.maliyet", "label": "Outstanding Cost Analysis",
    "desc": "cost stuff", "source_kind": "custom",
    "documentation": {"purpose": "mevcut amaç"},
    "blocks": [{"id": "camon_wf", "title": "Waterfall",
                "custom_render": {"page": "cost-analysis", "anchor": "x"},
                "documentation": {}}],
}


class GoodLLM:
    def complete(self, system, user, **kw):
        return ('```json {"documentation": {"purpose": "yeni amaç"},'
                ' "blocks_documentation": {"camon_wf": {"purpose": "blok amacı"},'
                ' "bilinmeyen": {"purpose": "x"}}} ```')


class BadLLM:
    def complete(self, system, user, **kw):
        return "hiç json yok"


class BoomLLM:
    def complete(self, system, user, **kw):
        raise RuntimeError("down")


def test_prompt_contains_process_and_blocks():
    p = build_user_prompt(_PROC)
    assert "Outstanding Cost Analysis" in p
    assert "camon_wf" in p
    assert "mevcut amaç" in p          # mevcut döküman bağlama girer


def test_good_llm_parsed_and_filtered():
    r = propose_documentation(GoodLLM(), _PROC)
    assert r["source"] == "llm"
    assert r["documentation"]["purpose"] == "yeni amaç"
    assert "camon_wf" in r["blocks_documentation"]
    assert "bilinmeyen" not in r["blocks_documentation"]   # süreçte olmayan blok düşer


def test_bad_llm_prod_errors_dev_stubs():
    assert "error" in propose_documentation(BadLLM(), _PROC)
    stub = propose_documentation(BadLLM(), _PROC, dev_mode=True)
    assert stub["source"] == "stub"
    assert "camon_wf" in stub["blocks_documentation"]


def test_llm_exception_prod_errors_dev_stubs():
    assert "error" in propose_documentation(BoomLLM(), _PROC)
    assert propose_documentation(BoomLLM(), _PROC, dev_mode=True)["source"] == "stub"

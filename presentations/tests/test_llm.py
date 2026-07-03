from presentations.llm import (
    _block_layout_summary,
    _manifest_for_prompt,
    _section_insertion_indices,
    compose_user_message,
    _parse_llm_output,
)


def _sample_blocks():
    """Nested-shape (post-Phase 8): top-level is sections, leaves under children."""
    return [
        {
            "id": "h_overview", "type": "section_header", "title": "Genel Bakış",
            "locked": False, "children": [
                {"id": "k1", "type": "kpi", "title": "Mevduat"},
                {"id": "k2", "type": "kpi", "title": "NII"},
            ],
        },
        {
            "id": "h_branches", "type": "section_header", "title": "Şube Performansı",
            "locked": False, "children": [
                {"id": "c1", "type": "bar_chart", "title": "Top 8"},
            ],
        },
        {
            "id": "h_nii", "type": "section_header", "title": "NII Trend",
            "locked": False, "children": [
                {"id": "c2", "type": "line_chart", "title": "12 Ay"},
            ],
        },
    ]


class TestLayoutSummary:
    def test_lists_top_level_sections_with_paths(self):
        out = _block_layout_summary(_sample_blocks())
        assert "/blocks/0" in out and "Genel Bakış" in out
        assert "/blocks/2" in out and "NII Trend" in out
        # Sections are marked with ▸
        assert "▸" in out

    def test_lists_children_with_path(self):
        out = _block_layout_summary(_sample_blocks())
        # Children show their full path
        assert "/blocks/0/children/0" in out
        assert "/blocks/2/children/0" in out


class TestSectionInsertionIndices:
    def test_each_section_gets_a_children_path(self):
        out = _section_insertion_indices(_sample_blocks())
        assert '"Genel Bakış" bölümüne yeni blok ekle → /blocks/0/children/-' in out
        assert '"Şube Performansı" bölümüne yeni blok ekle → /blocks/1/children/-' in out
        assert '"NII Trend" bölümüne yeni blok ekle → /blocks/2/children/-' in out

    def test_empty_manifest_message(self):
        out = _section_insertion_indices([])
        assert "boş" in out


class TestComposeUserMessage:
    def test_includes_all_components(self):
        msg = compose_user_message(
            {"blocks": _sample_blocks()},
            selected_block_id="c2",
            user_message="taşı",
        )
        assert "Blok dizilimi" in msg
        assert "Section ekleme" in msg
        assert "Seçili blok" in msg
        # Selected block path is included
        assert "/blocks/2/children/0" in msg
        assert "Talep" in msg
        assert "taşı" in msg

    def test_no_selection(self):
        msg = compose_user_message(
            {"blocks": _sample_blocks()},
            selected_block_id=None,
            user_message="ekle",
        )
        assert "yok" in msg


class TestParseLlmOutput:
    # _parse_llm_output 3-tuple döner: (patches, explanation, suggestions).
    def test_plain_json(self):
        patches, expl, _sugg = _parse_llm_output(
            '{"patches": [{"op":"replace","path":"/x","value":1}], "explanation": "ok"}'
        )
        assert len(patches) == 1
        assert expl == "ok"

    def test_with_code_fence(self):
        text = '```json\n{"patches": [], "explanation": "no-op"}\n```'
        patches, expl, _sugg = _parse_llm_output(text)
        assert patches == []
        assert expl == "no-op"

    def test_with_prose_around(self):
        text = 'Sure, here it is:\n{"patches": [], "explanation": "ok"}\nLet me know!'
        patches, expl, _sugg = _parse_llm_output(text)
        assert expl == "ok"

    def test_garbage_returns_error(self):
        patches, expl, _sugg = _parse_llm_output("totally not json")
        assert patches == []
        assert "parse edilemedi" in expl.lower()


class TestManifestForPrompt:
    """Ofis bulgusu (2026-07-03): CREATE_TM eksenli grafiklerde config
    5000-elemanlı dizilerle doluyor, chat_history 200 mesaja çıkıyor →
    prompt 253k token → provider HTTP 400. Prompt kopyası budanmalı."""

    @staticmethod
    def _chart_block(n=5000, with_sql=True):
        block = {
            "id": "c_big", "type": "combo_chart", "title": "Verilen Oran",
            "config": {
                "categories": [f"2026-07-01T10:{i % 60:02d}:{i % 60:02d}" for i in range(n)],
                "series": [
                    {"name": "İşlem", "values": list(range(n)), "kind": "bar", "axis": "right"},
                    {"name": "Oran", "values": [i * 0.1 for i in range(n)], "kind": "line", "axis": "left"},
                ],
            },
        }
        if with_sql:
            block["data_source"] = {
                "original_sql": "SELECT CREATE_TM, COUNT(*) FROM T GROUP BY CREATE_TM",
                "rows": [[1, 2]] * n,
                "preview_rows": [[1, 2]],
            }
        return block

    def test_data_bound_config_arrays_truncated(self):
        m = {"blocks": [{"id": "s", "type": "section_header", "title": "S",
                         "children": [self._chart_block()]}]}
        out = _manifest_for_prompt(m)
        cfg = out["blocks"][0]["children"][0]["config"]
        assert len(cfg["categories"]) == 24
        assert all(len(s["values"]) == 24 for s in cfg["series"])
        # kind/axis gibi stil alanları korunur
        assert cfg["series"][0]["kind"] == "bar"

    def test_static_block_small_config_untouched(self):
        blk = {"id": "k", "type": "bar_chart", "title": "Elle",
               "config": {"categories": ["a", "b", "c"],
                          "series": [{"name": "x", "values": [1, 2, 3]}]}}
        m = {"blocks": [{"id": "s", "type": "section_header", "children": [blk]}]}
        cfg = _manifest_for_prompt(m)["blocks"][0]["children"][0]["config"]
        assert cfg["categories"] == ["a", "b", "c"]
        assert cfg["series"][0]["values"] == [1, 2, 3]

    def test_static_block_huge_config_capped_at_200(self):
        blk = self._chart_block(n=500, with_sql=False)
        m = {"blocks": [{"id": "s", "type": "section_header", "children": [blk]}]}
        cfg = _manifest_for_prompt(m)["blocks"][0]["children"][0]["config"]
        assert len(cfg["categories"]) == 200
        assert len(cfg["series"][0]["values"]) == 200

    def test_chat_histories_stripped(self):
        m = {"blocks": [],
             "chat_history": [{"role": "user", "text": "x"}] * 200,
             "kesif_chat_history": [{"role": "user", "text": "y"}] * 50}
        out = _manifest_for_prompt(m)
        assert "chat_history" not in out
        assert "kesif_chat_history" not in out

    def test_data_source_rows_still_stripped(self):
        m = {"blocks": [{"id": "s", "type": "section_header",
                         "children": [self._chart_block()]}]}
        ds = _manifest_for_prompt(m)["blocks"][0]["children"][0]["data_source"]
        assert "rows" not in ds and "preview_rows" not in ds
        assert ds["original_sql"].startswith("SELECT")

    def test_original_manifest_not_mutated(self):
        blk = self._chart_block()
        m = {"blocks": [{"id": "s", "type": "section_header", "children": [blk]}],
             "chat_history": [{"role": "user", "text": "x"}]}
        _manifest_for_prompt(m)
        assert len(blk["config"]["categories"]) == 5000
        assert len(blk["config"]["series"][0]["values"]) == 5000
        assert "rows" in blk["data_source"]
        assert "chat_history" in m

    def test_legacy_unknown_keys_also_truncated(self):
        """Jenerik derin budama: bugünkü anahtar adlarını bilmesek de manifest'e
        geçmiş deploy'ların yazdığı HER uzun liste kısaltılmalı (ofis bulgusu:
        anahtar-adı bazlı budama legacy alanları ıskalayıp 670k char bıraktı)."""
        blk = {
            "id": "c", "type": "combo_chart", "title": "L",
            "data_source": {"original_sql": "SELECT 1",
                            "sample": [[1, 2]] * 3000,          # legacy key
                            "columns": ["A"] * 300},
            "config": {"series": [{"name": "s", "data": list(range(5000))}],  # legacy 'data'
                       "weird_legacy": [{"x": 1}] * 4000},
            "cached_rows": [[1]] * 2000,                        # blok kökünde legacy
        }
        m = {"blocks": [{"id": "s", "type": "section_header", "children": [blk]}],
             "filters": [{"id": "f1", "concept": "c",
                          "allowed_values": [str(i) for i in range(3000)]}],
             "legacy_top": [1] * 9000}
        out = _manifest_for_prompt(m)
        b = out["blocks"][0]["children"][0]
        assert len(b["data_source"]["sample"]) == 50
        assert len(b["data_source"]["columns"]) == 50
        assert len(b["config"]["series"][0]["data"]) == 24
        assert len(b["config"]["weird_legacy"]) == 24
        assert len(b["cached_rows"]) == 50
        assert len(out["filters"][0]["allowed_values"]) == 50
        assert len(out["legacy_top"]) == 50

    def test_children_structure_never_truncated(self):
        children = [{"id": f"k{i}", "type": "kpi", "title": str(i),
                     "config": {"value": i}} for i in range(80)]
        m = {"blocks": [{"id": "s", "type": "section_header", "children": children}]}
        out = _manifest_for_prompt(m)
        assert len(out["blocks"][0]["children"]) == 80

    def test_oversized_prompt_raises_actionable_error(self, monkeypatch):
        from presentations import llm as llm_mod
        from presentations.llm import QwenClient
        import pytest

        called = {"post": False}
        monkeypatch.setattr(llm_mod.requests, "post",
                            lambda *a, **k: called.update(post=True))
        # ~1MB'lık user mesajı → tahmini token > 200k → provider'a gitmeden hata
        with pytest.raises(RuntimeError, match="bağlamı çok büyük"):
            QwenClient(endpoint="http://x", token="t").generate_patches(
                "sys", "x" * 1_000_000, {"blocks": []})
        assert called["post"] is False


class TestGenMaxTokensAndTruncation:
    # G1 — max_tokens 2048→8192 + truncation tespiti.
    def test_default_max_tokens_is_high(self):
        from presentations.llm import QwenClient
        assert QwenClient(endpoint="http://x", token="t").gen_max_tokens == 8192

    def test_default_timeout_is_300(self):
        # B3 (Oturum N4) — ofis prod'da 60s timeout uzun üretimleri kesiyordu.
        from presentations.llm import QwenClient
        assert QwenClient(endpoint="http://x", token="t").timeout == 300

    def test_gen_max_tokens_reaches_payload(self, monkeypatch):
        from presentations import llm as llm_mod
        from presentations.llm import QwenClient
        captured = {}

        class _Resp:
            ok = True
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": '{"patches": [], "explanation": "ok"}'},
                                     "finish_reason": "stop"}]}

        monkeypatch.setattr(llm_mod.requests, "post",
                            lambda *a, **k: (captured.update(mt=(k.get("json") or {}).get("max_tokens")), _Resp())[1])
        QwenClient(endpoint="http://x", token="t", gen_max_tokens=12345).generate_patches(
            "sys", "x", {"blocks": []})
        assert captured["mt"] == 12345

    def test_truncation_returns_actionable_message_not_partial_patches(self, monkeypatch):
        from presentations import llm as llm_mod
        from presentations.llm import QwenClient

        class _Resp:
            ok = True
            status_code = 200
            def json(self):
                # max_tokens'a takılıp kesilmiş JSON + finish_reason=length
                return {"choices": [{
                    "message": {"content": '{"patches": [{"op":"add","path":"/blocks/-","val'},
                    "finish_reason": "length",
                }]}

        monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: _Resp())
        patches, expl, _sugg = QwenClient(endpoint="http://x", token="t").generate_patches(
            "sys", "çok karosel ekle", {"blocks": []})
        assert patches == []                                   # yarım patch UYGULANMAZ
        assert ("kesildi" in expl.lower()) or ("max_tokens" in expl.lower())

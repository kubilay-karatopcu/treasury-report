from presentations.llm import (
    _block_layout_summary,
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

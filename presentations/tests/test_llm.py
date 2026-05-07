from presentations.llm import (
    _block_layout_summary,
    _section_insertion_indices,
    compose_user_message,
    _parse_llm_output,
)


def _sample_blocks():
    return [
        {"id": "h_overview", "type": "section_header", "title": "Genel Bakış"},
        {"id": "k1", "type": "kpi", "title": "Mevduat"},
        {"id": "k2", "type": "kpi", "title": "NII"},
        {"id": "h_branches", "type": "section_header", "title": "Şube Performansı"},
        {"id": "c1", "type": "bar_chart", "title": "Top 8"},
        {"id": "h_nii", "type": "section_header", "title": "NII Trend"},
        {"id": "c2", "type": "line_chart", "title": "12 Ay"},
    ]


class TestLayoutSummary:
    def test_lists_all_blocks_with_index(self):
        out = _block_layout_summary(_sample_blocks())
        assert "[0] section_header" in out
        assert "[6] line_chart" in out
        assert "Genel Bakış" in out

    def test_section_headers_marked(self):
        out = _block_layout_summary(_sample_blocks())
        # section_headers get the ▸ marker
        for line in out.splitlines():
            if "section_header" in line:
                assert line.startswith("  ▸")
            elif line.strip():
                assert line.startswith("    ")


class TestSectionInsertionIndices:
    def test_each_section_gets_an_index(self):
        out = _section_insertion_indices(_sample_blocks())
        # Genel Bakış: next header at idx 3 → insert at 3
        assert '"Genel Bakış" altına ekle → /blocks/3' in out
        # Şube Performansı: next header at idx 5 → insert at 5
        assert '"Şube Performansı" altına ekle → /blocks/5' in out
        # NII Trend: last section → append
        assert 'NII Trend' in out and '/blocks/-' in out

    def test_no_sections_message(self):
        out = _section_insertion_indices([
            {"id": "k1", "type": "kpi", "title": "X"},
        ])
        assert "section_header yok" in out


class TestComposeUserMessage:
    def test_includes_all_components(self):
        msg = compose_user_message(
            {"blocks": _sample_blocks()},
            selected_block_id="c2",
            user_message="taşı",
        )
        assert "Blok dizilimi" in msg
        assert "Section ekleme index" in msg
        assert "Seçili blok" in msg
        assert "c2 (idx 6)" in msg
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
    def test_plain_json(self):
        patches, expl = _parse_llm_output('{"patches": [{"op":"replace","path":"/x","value":1}], "explanation": "ok"}')
        assert len(patches) == 1
        assert expl == "ok"

    def test_with_code_fence(self):
        text = '```json\n{"patches": [], "explanation": "no-op"}\n```'
        patches, expl = _parse_llm_output(text)
        assert patches == []
        assert expl == "no-op"

    def test_with_prose_around(self):
        text = 'Sure, here it is:\n{"patches": [], "explanation": "ok"}\nLet me know!'
        patches, expl = _parse_llm_output(text)
        assert expl == "ok"

    def test_garbage_returns_error(self):
        patches, expl = _parse_llm_output("totally not json")
        assert patches == []
        assert "parse edilemedi" in expl.lower()

from presentations.migration import migrate_to_nested, is_nested, ensure_nested


class TestIsNested:
    def test_nested_form_detected(self):
        m = {"blocks": [
            {"id": "h1", "type": "section_header", "title": "T", "children": []}
        ]}
        assert is_nested(m) is True

    def test_flat_form_detected(self):
        m = {"blocks": [
            {"id": "h1", "type": "section_header", "title": "T"},
            {"id": "k1", "type": "kpi"},
        ]}
        assert is_nested(m) is False

    def test_section_without_children_is_flat(self):
        m = {"blocks": [{"id": "h1", "type": "section_header", "title": "T"}]}
        assert is_nested(m) is False

    def test_empty_blocks_is_nested_compatible(self):
        assert is_nested({"blocks": []}) is True


class TestMigrateToNested:
    def test_groups_blocks_under_their_section_header(self):
        m = {"blocks": [
            {"id": "h1", "type": "section_header", "title": "Genel"},
            {"id": "k1", "type": "kpi"},
            {"id": "k2", "type": "kpi"},
            {"id": "h2", "type": "section_header", "title": "Şube"},
            {"id": "c1", "type": "bar_chart"},
        ]}
        out = migrate_to_nested(m)
        assert len(out["blocks"]) == 2
        assert out["blocks"][0]["id"] == "h1"
        assert [c["id"] for c in out["blocks"][0]["children"]] == ["k1", "k2"]
        assert out["blocks"][1]["id"] == "h2"
        assert [c["id"] for c in out["blocks"][1]["children"]] == ["c1"]

    def test_preamble_blocks_get_intro_section(self):
        m = {"blocks": [
            {"id": "k1", "type": "kpi"},
            {"id": "h1", "type": "section_header", "title": "X"},
            {"id": "k2", "type": "kpi"},
        ]}
        out = migrate_to_nested(m)
        assert out["blocks"][0]["title"] == "Giriş"
        assert out["blocks"][0]["children"][0]["id"] == "k1"
        assert out["blocks"][1]["id"] == "h1"

    def test_no_section_headers_wraps_everything(self):
        m = {"blocks": [
            {"id": "k1", "type": "kpi"},
            {"id": "k2", "type": "kpi"},
        ]}
        out = migrate_to_nested(m)
        assert len(out["blocks"]) == 1
        assert out["blocks"][0]["title"] == "Giriş"
        assert len(out["blocks"][0]["children"]) == 2

    def test_idempotent_on_already_nested(self):
        m = {"blocks": [
            {"id": "h1", "type": "section_header", "title": "T", "children": [
                {"id": "k1", "type": "kpi"},
            ]}
        ]}
        out = migrate_to_nested(m)
        assert out is m  # passes through

    def test_empty_manifest(self):
        out = migrate_to_nested({"blocks": []})
        assert out["blocks"] == []

    def test_preserves_other_top_level_keys(self):
        m = {
            "id": "p1",
            "version": 7,
            "meta": {"title": "X"},
            "blocks": [
                {"id": "h1", "type": "section_header", "title": "T"},
                {"id": "k1", "type": "kpi"},
            ],
        }
        out = migrate_to_nested(m)
        assert out["id"] == "p1"
        assert out["version"] == 7
        assert out["meta"]["title"] == "X"


class TestEnsureNested:
    def test_passes_through_none(self):
        assert ensure_nested(None) is None

    def test_migrates_flat(self):
        m = {"blocks": [{"id": "h1", "type": "section_header", "title": "T"}]}
        out = ensure_nested(m)
        assert out["blocks"][0]["children"] == []

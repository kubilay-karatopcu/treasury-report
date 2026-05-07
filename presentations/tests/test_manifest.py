import pytest
from presentations.manifest import validate_block, validate_manifest


class TestValidateBlock:
    def test_valid_kpi(self):
        block = {
            "id": "b1", "type": "kpi", "title": "T", "locked": False,
            "config": {"value": 487.2, "unit": "B TRY", "delta": 4.8, "delta_label": "Q3", "period": "Q4 2025"},
        }
        assert validate_block(block) == []

    def test_kpi_missing_fields(self):
        block = {
            "id": "b1", "type": "kpi", "title": "T", "locked": False,
            "config": {"value": 487.2, "unit": "B TRY"},  # missing delta, delta_label, period
        }
        errors = validate_block(block)
        assert len(errors) == 3

    def test_kpi_non_numeric_value(self):
        block = {
            "id": "b1", "type": "kpi", "title": "T", "locked": False,
            "config": {"value": "not_a_number", "unit": "TRY", "delta": 0.0, "delta_label": "x", "period": "Q4"},
        }
        errors = validate_block(block)
        assert any("numeric" in e for e in errors)

    def test_valid_bar_chart(self):
        block = {
            "id": "c1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": ["A", "B", "C"],
                "series": [{"name": "s", "values": [1.0, 2.0, 3.0]}],
            },
        }
        assert validate_block(block) == []

    def test_bar_chart_length_mismatch(self):
        block = {
            "id": "c1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": ["A", "B", "C"],
                "series": [{"name": "s", "values": [1.0, 2.0]}],  # one short
            },
        }
        errors = validate_block(block)
        assert errors

    def test_bar_chart_multi_series_mismatch(self):
        block = {
            "id": "c1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": ["A", "B"],
                "series": [
                    {"name": "s1", "values": [1.0, 2.0]},       # OK
                    {"name": "s2", "values": [1.0, 2.0, 3.0]},  # too long
                ],
            },
        }
        errors = validate_block(block)
        assert len(errors) == 1
        assert "series[1]" in errors[0]

    def test_valid_line_chart(self):
        block = {
            "id": "lc1", "type": "line_chart", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Jan", "Feb", "Mar"],
                "series": [
                    {"name": "Actual",   "values": [1.0, 2.0, 3.0]},
                    {"name": "Forecast", "values": [1.5, 2.5, 3.5]},
                ],
            },
        }
        assert validate_block(block) == []

    def test_line_chart_length_mismatch(self):
        block = {
            "id": "lc1", "type": "line_chart", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Jan", "Feb", "Mar"],
                "series": [{"name": "s", "values": [1.0, 2.0]}],  # one short
            },
        }
        errors = validate_block(block)
        assert errors

    def test_valid_narrative(self):
        block = {"id": "n1", "type": "narrative", "title": "T", "locked": False,
                 "config": {"text": "Hello world."}}
        assert validate_block(block) == []

    def test_narrative_missing_text(self):
        block = {"id": "n1", "type": "narrative", "title": "T", "locked": False, "config": {}}
        errors = validate_block(block)
        assert errors

    def test_section_header(self):
        block = {"id": "h1", "type": "section_header", "title": "Overview", "config": {}}
        assert validate_block(block) == []

    def test_unknown_type(self):
        block = {"id": "x1", "type": "unknown_type", "title": "T", "config": {}}
        errors = validate_block(block)
        assert errors

    # Phase 6 — width validation
    def test_valid_width_values(self):
        for w in ("full", "1/2", "1/3", "2/3"):
            block = {
                "id": "k1", "type": "kpi", "title": "T", "locked": False, "width": w,
                "config": {"value": 1.0, "unit": "TRY", "delta": 0, "delta_label": "x", "period": "p"},
            }
            assert validate_block(block) == []

    def test_invalid_width_rejected(self):
        block = {
            "id": "k1", "type": "kpi", "title": "T", "locked": False, "width": "1/4",
            "config": {"value": 1.0, "unit": "TRY", "delta": 0, "delta_label": "x", "period": "p"},
        }
        errors = validate_block(block)
        assert any("width" in e for e in errors)

    def test_section_header_must_not_have_width(self):
        block = {"id": "h1", "type": "section_header", "title": "T", "width": "1/2", "config": {}}
        errors = validate_block(block)
        assert any("width" in e for e in errors)

    def test_width_field_optional(self):
        """Existing manifests without `width` must remain valid."""
        block = {
            "id": "k1", "type": "kpi", "title": "T", "locked": False,
            "config": {"value": 1.0, "unit": "TRY", "delta": 0, "delta_label": "x", "period": "p"},
        }
        assert validate_block(block) == []

    # ── New chart types (area / pie / heatmap / radial_bar) ────────────────

    def test_valid_area_chart(self):
        block = {
            "id": "ac1", "type": "area_chart", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Jan", "Feb", "Mar"],
                "series": [{"name": "s1", "values": [1.0, 2.0, 3.0]}],
            },
        }
        assert validate_block(block) == []

    def test_area_chart_length_mismatch(self):
        block = {
            "id": "ac1", "type": "area_chart", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Jan", "Feb", "Mar"],
                "series": [{"name": "s1", "values": [1.0, 2.0]}],
            },
        }
        assert validate_block(block)

    def test_valid_pie_chart(self):
        block = {
            "id": "p1", "type": "pie_chart", "title": "T", "locked": False,
            "config": {"labels": ["A", "B", "C"], "values": [10.0, 20.0, 30.0]},
        }
        assert validate_block(block) == []

    def test_valid_donut_chart(self):
        block = {
            "id": "p1", "type": "pie_chart", "title": "T", "locked": False,
            "config": {"labels": ["A", "B"], "values": [10.0, 20.0], "donut": True},
        }
        assert validate_block(block) == []

    def test_pie_chart_length_mismatch(self):
        block = {
            "id": "p1", "type": "pie_chart", "title": "T", "locked": False,
            "config": {"labels": ["A", "B", "C"], "values": [10.0, 20.0]},
        }
        assert validate_block(block)

    def test_pie_chart_missing_labels(self):
        block = {
            "id": "p1", "type": "pie_chart", "title": "T", "locked": False,
            "config": {"values": [1.0, 2.0]},
        }
        errors = validate_block(block)
        assert any("labels" in e for e in errors)

    def test_valid_heatmap(self):
        block = {
            "id": "hm1", "type": "heatmap", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Q1", "Q2", "Q3"],
                "series": [
                    {"name": "Levent", "values": [10, 20, 30]},
                    {"name": "Maslak", "values": [15, 25, 35]},
                ],
            },
        }
        assert validate_block(block) == []

    def test_heatmap_length_mismatch(self):
        block = {
            "id": "hm1", "type": "heatmap", "title": "T", "locked": False,
            "config": {
                "x_axis": ["Q1", "Q2", "Q3"],
                "series": [{"name": "Levent", "values": [10, 20]}],
            },
        }
        assert validate_block(block)

    def test_valid_radial_bar(self):
        block = {
            "id": "r1", "type": "radial_bar", "title": "T", "locked": False,
            "config": {"value": 78, "max": 100, "label": "Hedef"},
        }
        assert validate_block(block) == []

    def test_radial_bar_missing_value(self):
        block = {
            "id": "r1", "type": "radial_bar", "title": "T", "locked": False,
            "config": {"max": 100},
        }
        errors = validate_block(block)
        assert any("value" in e for e in errors)

    def test_radial_bar_non_numeric_value(self):
        block = {
            "id": "r1", "type": "radial_bar", "title": "T", "locked": False,
            "config": {"value": "high"},
        }
        errors = validate_block(block)
        assert any("numeric" in e for e in errors)

    # bar_chart variants
    def test_bar_chart_with_stacked_flag(self):
        block = {
            "id": "b1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": ["A", "B"],
                "series": [
                    {"name": "s1", "values": [1, 2]},
                    {"name": "s2", "values": [3, 4]},
                ],
                "stacked": True,
            },
        }
        assert validate_block(block) == []

    def test_rejects_object_x_axis_in_line_chart(self):
        """LLM mistake: emitting [{date: ...}] instead of plain strings."""
        block = {
            "id": "lc1", "type": "line_chart", "title": "T", "locked": False,
            "config": {
                "x_axis": [{"date": "2025-01-01"}, {"date": "2025-02-01"}],
                "series": [{"name": "s", "values": [1, 2]}],
            },
        }
        errors = validate_block(block)
        assert any("x_axis" in e for e in errors)

    def test_rejects_object_categories_in_bar_chart(self):
        block = {
            "id": "bc1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": [{"name": "Levent"}, {"name": "Maslak"}],
                "series": [{"name": "s", "values": [10, 20]}],
            },
        }
        errors = validate_block(block)
        assert any("categories" in e for e in errors)

    def test_rejects_object_labels_in_pie_chart(self):
        block = {
            "id": "p1", "type": "pie_chart", "title": "T", "locked": False,
            "config": {
                "labels": [{"label": "A"}, {"label": "B"}],
                "values": [10, 20],
            },
        }
        errors = validate_block(block)
        assert any("labels" in e for e in errors)

    def test_bar_chart_with_horizontal_flag(self):
        block = {
            "id": "b1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": ["LongName1", "LongName2"],
                "series": [{"name": "s", "values": [10, 20]}],
                "horizontal": True,
            },
        }
        assert validate_block(block) == []


class TestValidateManifest:
    _FULL = {
        "id": "p1", "version": 1, "owner_id": "A1",
        "meta": {"title": "T", "eyebrow": "E", "date": "2025", "author_label": "L"},
        "basket": [],
        "blocks": [
            {"id": "h1", "type": "section_header", "title": "Overview", "config": {}},
            {
                "id": "k1", "type": "kpi", "title": "KPI", "locked": False,
                "config": {"value": 1.0, "unit": "TRY", "delta": 0.1, "delta_label": "L", "period": "P"},
            },
        ],
    }

    def test_valid_manifest(self):
        assert validate_manifest(self._FULL) == []

    def test_missing_meta(self):
        errors = validate_manifest({"blocks": []})
        assert any("meta" in e for e in errors)

    def test_missing_blocks(self):
        errors = validate_manifest({"meta": {}})
        assert any("blocks" in e for e in errors)

    def test_invalid_block_propagates(self):
        manifest = {
            "meta": {},
            "blocks": [
                {"id": "b1", "type": "kpi", "title": "T", "locked": False, "config": {}},
            ],
        }
        errors = validate_manifest(manifest)
        assert errors  # kpi missing required config fields

    def test_sample_manifest_is_valid(self):
        import json
        from pathlib import Path
        sample = Path(__file__).parent.parent.parent / "examples" / "sample_manifest.json"
        manifest = json.loads(sample.read_text(encoding="utf-8"))
        errors = validate_manifest(manifest)
        assert errors == [], f"sample_manifest.json failed validation: {errors}"

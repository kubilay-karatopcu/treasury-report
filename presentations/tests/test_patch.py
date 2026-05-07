import json
from pathlib import Path

import pytest

from presentations.patch import apply_patches, compute_inverse, classify_paths, validate_patches

FIXTURES_PATH = Path(__file__).parent.parent.parent / "examples" / "patch_fixtures.json"


@pytest.fixture(scope="module")
def fixtures():
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


# ── apply_patches ─────────────────────────────────────────────────────────────

class TestApplyPatches:
    def test_all_valid_fixtures(self, fixtures):
        for f in fixtures["fixtures"]:
            result = apply_patches(f["before"], f["patches"])
            assert result == f["after"], f"Fixture {f['name']!r} produced wrong result"

    def test_does_not_mutate_original(self, fixtures):
        f = fixtures["fixtures"][0]
        original = json.loads(json.dumps(f["before"]))
        apply_patches(f["before"], f["patches"])
        assert f["before"] == original

    def test_empty_patches_returns_copy(self, fixtures):
        f = fixtures["fixtures"][0]
        result = apply_patches(f["before"], [])
        assert result == f["before"]
        assert result is not f["before"]

    def test_kpi_value_replace(self):
        state = {"blocks": [{"id": "b1", "type": "kpi", "config": {"value": 100.0}}]}
        result = apply_patches(state, [{"op": "replace", "path": "/blocks/0/config/value", "value": 200.0}])
        assert result["blocks"][0]["config"]["value"] == 200.0

    def test_add_block_at_end(self):
        state = {"blocks": [{"id": "b1"}]}
        new_block = {"id": "b2", "type": "narrative"}
        result = apply_patches(state, [{"op": "add", "path": "/blocks/-", "value": new_block}])
        assert len(result["blocks"]) == 2
        assert result["blocks"][1] == new_block

    def test_add_block_at_index(self):
        state = {"blocks": [{"id": "a"}, {"id": "c"}]}
        new_block = {"id": "b"}
        result = apply_patches(state, [{"op": "add", "path": "/blocks/1", "value": new_block}])
        assert [b["id"] for b in result["blocks"]] == ["a", "b", "c"]

    def test_remove_block(self):
        state = {"blocks": [{"id": "a"}, {"id": "b"}]}
        result = apply_patches(state, [{"op": "remove", "path": "/blocks/0"}])
        assert len(result["blocks"]) == 1
        assert result["blocks"][0]["id"] == "b"

    def test_remove_series_from_chart(self):
        state = {"blocks": [{"id": "c1", "type": "line_chart", "config": {
            "x_axis": ["A", "B"],
            "series": [{"name": "S1", "values": [1, 2]}, {"name": "S2", "values": [3, 4]}],
        }}]}
        result = apply_patches(state, [{"op": "remove", "path": "/blocks/0/config/series/1"}])
        assert len(result["blocks"][0]["config"]["series"]) == 1

    def test_meta_replace(self):
        state = {"meta": {"title": "Old"}, "blocks": []}
        result = apply_patches(state, [{"op": "replace", "path": "/meta/title", "value": "New"}])
        assert result["meta"]["title"] == "New"


# ── compute_inverse ───────────────────────────────────────────────────────────

class TestComputeInverse:
    def test_roundtrip_all_fixtures(self, fixtures):
        for f in fixtures["fixtures"]:
            patched = apply_patches(f["before"], f["patches"])
            inverse = compute_inverse(f["before"], f["patches"])
            restored = apply_patches(patched, inverse)
            assert restored == f["before"], f"Fixture {f['name']!r}: inverse round-trip failed"

    def test_inverse_of_replace(self):
        state = {"blocks": [{"config": {"value": 10}}]}
        patches = [{"op": "replace", "path": "/blocks/0/config/value", "value": 99}]
        inverse = compute_inverse(state, patches)
        assert inverse == [{"op": "replace", "path": "/blocks/0/config/value", "value": 10}]

    def test_inverse_of_add_at_end(self):
        state = {"blocks": [{"id": "a"}]}
        patches = [{"op": "add", "path": "/blocks/-", "value": {"id": "b"}}]
        inverse = compute_inverse(state, patches)
        patched = apply_patches(state, patches)
        restored = apply_patches(patched, inverse)
        assert restored == state

    def test_inverse_of_remove(self):
        state = {"blocks": [{"id": "a"}, {"id": "b"}]}
        patches = [{"op": "remove", "path": "/blocks/0"}]
        inverse = compute_inverse(state, patches)
        patched = apply_patches(state, patches)
        restored = apply_patches(patched, inverse)
        assert restored == state


# ── classify_paths ────────────────────────────────────────────────────────────

class TestClassifyPaths:
    def test_meta_patch(self):
        patches = [{"op": "replace", "path": "/meta/title", "value": "X"}]
        result = classify_paths(patches)
        assert result["meta"] == patches
        assert result["blocks"] == {}
        assert result["structural"] == []

    def test_block_patch(self):
        patches = [{"op": "replace", "path": "/blocks/2/config/value", "value": 5}]
        result = classify_paths(patches)
        assert result["blocks"] == {2: patches}
        assert result["meta"] == []
        assert result["structural"] == []

    def test_structural_append(self):
        patches = [{"op": "add", "path": "/blocks/-", "value": {}}]
        result = classify_paths(patches)
        assert result["structural"] == patches

    def test_structural_remove(self):
        patches = [{"op": "remove", "path": "/blocks/0"}]
        result = classify_paths(patches)
        assert result["structural"] == patches

    def test_mixed(self):
        patches = [
            {"op": "replace", "path": "/meta/title", "value": "T"},
            {"op": "replace", "path": "/blocks/0/config/value", "value": 1},
            {"op": "replace", "path": "/blocks/1/title", "value": "B"},
            {"op": "add",     "path": "/blocks/-", "value": {}},
        ]
        result = classify_paths(patches)
        assert len(result["meta"]) == 1
        assert 0 in result["blocks"]
        assert 1 in result["blocks"]
        assert len(result["structural"]) == 1


# ── validate_patches ──────────────────────────────────────────────────────────

class TestValidatePatches:
    def _bar_state(self, n: int = 8):
        vals = list(range(n))
        return {"blocks": [{
            "id": "c1", "type": "bar_chart", "title": "T", "locked": False,
            "config": {
                "categories": [str(i) for i in range(n)],
                "series": [{"name": "v", "values": [float(i) for i in vals]}],
            },
        }]}

    def test_accepts_all_valid_fixtures(self, fixtures):
        for f in fixtures["fixtures"]:
            errors = validate_patches(f["before"], f["patches"])
            assert errors == [], f"Fixture {f['name']!r} unexpectedly rejected: {errors}"

    def test_rejects_immutable_id(self):
        state = {"blocks": [{"id": "b1", "type": "kpi", "locked": False, "config": {}}]}
        errors = validate_patches(state, [{"op": "replace", "path": "/blocks/0/id", "value": "evil"}])
        assert errors

    def test_rejects_immutable_type(self):
        state = {"blocks": [{"id": "b1", "type": "kpi", "locked": False, "config": {}}]}
        errors = validate_patches(state, [{"op": "replace", "path": "/blocks/0/type", "value": "narrative"}])
        assert errors

    def test_rejects_immutable_locked(self):
        state = {"blocks": [{"id": "b1", "type": "kpi", "locked": False, "config": {}}]}
        errors = validate_patches(state, [{"op": "replace", "path": "/blocks/0/locked", "value": True}])
        assert errors

    def test_rejects_unsupported_op(self):
        state = {"blocks": []}
        errors = validate_patches(state, [{"op": "move", "from": "/blocks/0", "path": "/blocks/1"}])
        assert errors

    def test_rejects_path_outside_scope(self):
        state = {"blocks": [], "meta": {}}
        errors = validate_patches(state, [{"op": "replace", "path": "/secret/admin", "value": True}])
        assert errors

    def test_rejects_chart_length_mismatch(self):
        state = self._bar_state(8)
        # Reduce categories to 2 but leave series.values at 8 — invariant broken.
        errors = validate_patches(
            state,
            [{"op": "replace", "path": "/blocks/0/config/categories", "value": ["A", "B"]}],
        )
        assert errors

    def test_accepts_consistent_chart_update(self):
        state = self._bar_state(8)
        errors = validate_patches(state, [
            {"op": "replace", "path": "/blocks/0/config/categories",
             "value": ["A", "B", "C", "D", "E"]},
            {"op": "replace", "path": "/blocks/0/config/series/0/values",
             "value": [1.0, 2.0, 3.0, 4.0, 5.0]},
        ])
        assert errors == []

"""Phase 10B — ExpertStore + /api/experts/* tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from prisma_home.experts import Expert, LocalExpertStore


FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "phase_10" / "experts"


# ── Expert dataclass ─────────────────────────────────────────────────────────

class TestExpert:
    def test_from_dict_round_trip(self):
        d = {
            "id": "liq", "version": 1, "code": "LIQ",
            "name": "Likidite Uzmanı", "domain_label": "Likidite",
            "short_description": "x", "status": "active",
        }
        e = Expert.from_dict(d)
        assert e.id == "liq"
        assert e.code == "LIQ"
        # Defaults fill in the optional fields.
        assert e.persona == {}
        assert e.bound_content["blocks"] == []
        # Round-trip preserves the data we care about.
        for k, v in d.items():
            assert e.to_dict()[k] == v

    def test_from_dict_ignores_unknown_keys(self):
        # Forward-compat: a YAML with extra fields shouldn't crash.
        e = Expert.from_dict({"id": "x", "version": 1, "code": "X",
                              "name": "n", "domain_label": "d",
                              "short_description": "s",
                              "future_field": "ignored"})
        assert not hasattr(e, "future_field")


# ── LocalExpertStore ────────────────────────────────────────────────────────

class TestLocalExpertStore:
    @pytest.fixture
    def store(self):
        return LocalExpertStore(base_dir=FIXTURES)

    def test_loads_six_experts(self, store):
        experts = store.list_all()
        assert len(experts) == 6
        codes = {e.code for e in experts}
        assert codes == {"LIQ", "DEP", "FND", "NII", "SEC", "KRD"}

    def test_load_by_id(self, store):
        liq = store.load("liq")
        assert liq is not None
        assert liq.code == "LIQ"
        assert liq.domain_label == "Likidite"
        assert "likidite" in liq.persona["system_prompt"].lower()

    def test_load_missing_returns_none(self, store):
        assert store.load("nonexistent") is None

    def test_exists(self, store):
        assert store.exists("liq")
        assert store.exists("krd")
        assert not store.exists("xyz")

    def test_list_for_user_with_wildcard_read(self, store):
        # All 6 fixtures have read: ["*"] so every user sees every expert.
        class _User: department = "ANYTHING"
        out = store.list_for_user(_User())
        assert len(out) == 6

    def test_list_for_user_filtered_by_dept(self, tmp_path):
        # Build a custom store with one dept-restricted expert.
        (tmp_path / "x.yaml").write_text(
            yaml.safe_dump({
                "id": "x", "version": 1, "code": "X",
                "name": "n", "domain_label": "d", "short_description": "s",
                "access_scope": {"read": ["BİLANÇO YÖNETİMİ"], "edit": []},
                "ui": {"accent_color": "#fff"},
            }, allow_unicode=True),
            encoding="utf-8",
        )
        st = LocalExpertStore(base_dir=tmp_path)
        class _U: department = "BİLANÇO YÖNETİMİ"
        class _Other: department = "MYU"
        assert len(st.list_for_user(_U())) == 1
        assert len(st.list_for_user(_Other())) == 0

    def test_missing_dir_is_empty_store(self, tmp_path):
        st = LocalExpertStore(base_dir=tmp_path / "does-not-exist")
        assert st.list_all() == []
        assert st.load("liq") is None

    def test_malformed_yaml_skipped(self, tmp_path):
        (tmp_path / "broken.yaml").write_text(": : : not yaml", encoding="utf-8")
        (tmp_path / "ok.yaml").write_text(
            yaml.safe_dump({"id": "ok", "version": 1, "code": "OK",
                            "name": "n", "domain_label": "d", "short_description": "s"},
                           allow_unicode=True),
            encoding="utf-8",
        )
        st = LocalExpertStore(base_dir=tmp_path)
        codes = {e.code for e in st.list_all()}
        # Broken file silently dropped, OK one loaded.
        assert codes == {"OK"}


# ── Fixture YAML schema sanity ──────────────────────────────────────────────

class TestExpertFixtures:
    """The six committed fixtures must satisfy the spec §5.1 schema."""

    @pytest.fixture
    def store(self):
        return LocalExpertStore(base_dir=FIXTURES)

    @pytest.mark.parametrize("expert_id", ["liq", "dep", "fnd", "nii", "sec", "krd"])
    def test_each_expert_has_required_fields(self, store, expert_id):
        e = store.load(expert_id)
        assert e is not None, f"missing expert: {expert_id}"
        assert e.code == expert_id.upper()
        assert len(e.code) in (2, 3), f"code must be 2-3 chars: {e.code}"
        assert e.code.isupper()
        assert e.name
        assert e.domain_label
        assert e.persona.get("system_prompt"), f"{expert_id}: persona.system_prompt required"
        assert isinstance(e.persona.get("voice_examples"), list)
        assert e.briefing_recipe.get("sections"), f"{expert_id}: at least one briefing section"
        # Section ids are unique within the expert.
        section_ids = [s["id"] for s in e.briefing_recipe["sections"]]
        assert len(section_ids) == len(set(section_ids))
        # accent_color is a hex string.
        ac = e.ui.get("accent_color", "")
        assert ac.startswith("#") and len(ac) in (4, 7)


# ── HTTP /api/experts/* ──────────────────────────────────────────────────────

class TestExpertsHTTP:
    def test_list_returns_six_experts(self, auth_client):
        rv = auth_client.get("/api/experts/")
        assert rv.status_code == 200
        payload = rv.get_json()
        assert "experts" in payload
        assert len(payload["experts"]) == 6
        # Slim summary — system_prompt must NOT leak to the client list response.
        for e in payload["experts"]:
            assert "id" in e and "code" in e and "name" in e
            assert "persona" not in e

    def test_get_single_expert_returns_full_dict(self, auth_client):
        rv = auth_client.get("/api/experts/liq")
        assert rv.status_code == 200
        body = rv.get_json()
        assert body["id"] == "liq"
        assert body["code"] == "LIQ"
        assert "system_prompt" in body["persona"]
        assert "briefing_recipe" in body

    def test_get_unknown_returns_404(self, auth_client):
        rv = auth_client.get("/api/experts/no-such-expert")
        assert rv.status_code == 404

"""Phase 10D — snapshot save accepts title/description/bound_experts body."""
from __future__ import annotations

import json

import pytest


def _warm_demo(client):
    client.get("/presentations/p_demo")


class TestSnapshotSaveBody:
    def test_default_post_still_works(self, auth_client):
        """Regression: posting no body keeps the pre-10D behaviour."""
        _warm_demo(auth_client)
        rv = auth_client.post("/presentations/p_demo/snapshot")
        assert rv.status_code == 200
        meta = rv.get_json()
        assert meta["snapshot_id"]
        # New fields are present with defaults.
        assert meta["description"] == ""
        assert isinstance(meta["bound_experts"], list)

    def test_title_override_persists(self, auth_client, flask_app):
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"title": "Özel Snapshot Başlığı"}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        meta = rv.get_json()
        assert meta["title"] == "Özel Snapshot Başlığı"

        # The frozen manifest's meta.title must also reflect the override
        # so the snapshot view shows the chosen name.
        store = flask_app.config["SNAPSHOT_STORE"]
        loaded = store.load(meta["snapshot_id"])
        assert loaded["manifest"]["meta"]["title"] == "Özel Snapshot Başlığı"

    def test_description_persists_in_meta(self, auth_client):
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"description": "Sabah toplantısı için."}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        meta = rv.get_json()
        assert meta["description"] == "Sabah toplantısı için."

    def test_bound_experts_overrides_manifest_field(self, auth_client, flask_app):
        # Even if the manifest has its own bound_experts, the save-body value wins.
        _warm_demo(auth_client)
        registry = flask_app.config["SESSION_REGISTRY"]
        sess = registry.get_or_create("A00000", "p_demo")
        m = sess.get_manifest() or {}
        m["bound_experts"] = ["liq"]
        sess.set_manifest(m)

        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"bound_experts": ["dep", "fnd"]}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        meta = rv.get_json()
        assert meta["bound_experts"] == ["dep", "fnd"]

        # The frozen manifest mirrors the override.
        store = flask_app.config["SNAPSHOT_STORE"]
        loaded = store.load(meta["snapshot_id"])
        assert loaded["manifest"]["bound_experts"] == ["dep", "fnd"]

    def test_invalid_bound_expert_id_returns_400(self, auth_client):
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"bound_experts": ["nonexistent_xyz"]}),
            content_type="application/json",
        )
        assert rv.status_code == 400
        body = rv.get_json()
        assert "Bilinmeyen" in body["error"]

    def test_bound_experts_must_be_list(self, auth_client):
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"bound_experts": "liq"}),  # string, not list
            content_type="application/json",
        )
        assert rv.status_code == 400

    def test_empty_bound_experts_is_valid(self, auth_client):
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({"bound_experts": []}),
            content_type="application/json",
        )
        assert rv.status_code == 200
        assert rv.get_json()["bound_experts"] == []

    def test_combined_save_appears_under_bound_experts_page(self, auth_client):
        """End-to-end: save with bound_experts, then GET /uzmanlar/<id>
        should show the new snapshot in the citation grid."""
        _warm_demo(auth_client)
        rv = auth_client.post(
            "/presentations/p_demo/snapshot",
            data=json.dumps({
                "title":         "Bağlı Sunum Testi",
                "description":   "10D doğrulaması",
                "bound_experts": ["liq", "dep"],
            }),
            content_type="application/json",
        )
        assert rv.status_code == 200
        sid = rv.get_json()["snapshot_id"]

        # LIQ page should show this snapshot.
        for code in ("liq", "dep"):
            page = auth_client.get(f"/uzmanlar/{code}")
            assert page.status_code == 200
            body = page.data.decode("utf-8")
            assert sid in body, f"{code} page missing snapshot {sid}"

        # FND page should NOT show it.
        fnd_page = auth_client.get("/uzmanlar/fnd")
        assert sid not in fnd_page.data.decode("utf-8")

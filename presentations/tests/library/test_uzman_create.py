"""POST /atolye/uzmanlar/api/create — yeni uzman (asistan) oluşturma.

Bu uç yokken uzman ancak store'da önceden varsa düzenlenebiliyordu
(uzman_edit/uzman_save bilinmeyen id'de 404) — yeni uzman doğurmanın yolu
yoktu. Create: 201 + store'a yazar; çakışan id 409; bozuk id 400; boş edit
scope'a oluşturanın departmanı yazılır.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user

from presentations import presentations_bp
from prisma_home.experts import LocalExpertStore


class _FakeUser(UserMixin):
    name = "kubilay"
    sicil = "A16438"
    department = "Treasury"

    def get_id(self):
        return self.sicil


@pytest.fixture
def app(tmp_path: Path):
    app = Flask(__name__,
                template_folder=str(Path(__file__).resolve().parents[2] / "templates"))
    app.config.update(
        SECRET_KEY="test", TESTING=True, LOGIN_DISABLED=True,
        EXPERT_STORE=LocalExpertStore(tmp_path / "experts"),
    )
    lm = LoginManager(app)
    lm.user_loader(lambda _id: _FakeUser())

    @app.before_request
    def _force():
        from flask_login import current_user
        if not getattr(current_user, "is_authenticated", False):
            login_user(_FakeUser())

    app.register_blueprint(presentations_bp, url_prefix="/presentations")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _form(eid="test_uzmani", **over):
    f = {
        "id": eid, "version": 1, "code": "TST", "name": "Test Uzmanı",
        "domain_label": "Test Alanı", "short_description": "kısa",
        "persona": {"system_prompt": "p", "voice_examples": []},
        "bound_content": {"blocks": [], "snapshots": [], "processes": []},
        "briefing_recipe": {"cache_ttl_seconds": 1800, "sections_yaml": ""},
        "access_scope": {"read": [], "edit": []},
        "ui": {"accent_color": "#123456", "glyph": "T"},
    }
    f.update(over)
    return f


def _post(client, form):
    return client.post("/presentations/atolye/uzmanlar/api/create",
                       data=json.dumps({"form": form}),
                       content_type="application/json")


def test_create_persists_and_sets_ownership(client, app):
    r = _post(client, _form())
    assert r.status_code == 201, r.data
    j = r.get_json()
    assert j["ok"] is True and j["id"] == "test_uzmani"
    saved = app.config["EXPERT_STORE"].load("test_uzmani")
    assert saved is not None
    # Boş scope → read ["*"], edit [oluşturanın departmanı].
    assert saved.access_scope["read"] == ["*"]
    assert saved.access_scope["edit"] == ["Treasury"]


def test_created_expert_is_editable_via_save(client):
    _post(client, _form())
    r = client.post("/presentations/atolye/uzmanlar/test_uzmani/api/save",
                    data=json.dumps({"form": _form(short_description="yeni")}),
                    content_type="application/json")
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True


def test_duplicate_id_conflict(client):
    _post(client, _form())
    r = _post(client, _form())
    assert r.status_code == 409
    assert "zaten mevcut" in r.get_json()["errors"][0]


def test_invalid_id_rejected(client):
    r = _post(client, _form(eid="Türkçe Karakterli!"))
    assert r.status_code == 400
    assert "Geçersiz uzman id" in r.get_json()["errors"][0]


def test_missing_required_form_fields_rejected(client):
    r = _post(client, _form(code=""))
    assert r.status_code == 400
    assert "Kod boş olamaz" in r.get_json()["errors"][0]


def test_explicit_scope_preserved(client, app):
    r = _post(client, _form(eid="scoped_uzman",
                            access_scope={"read": ["Treasury"], "edit": ["Risk"]}))
    assert r.status_code == 201
    saved = app.config["EXPERT_STORE"].load("scoped_uzman")
    assert saved.access_scope == {"read": ["Treasury"], "edit": ["Risk"]}

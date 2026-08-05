# -*- coding: utf-8 -*-
"""fi_desk Yönetim ekranı (lookup admin paneli) — Flask test client + dev
DuckDB. Yetki kapısı (IS_ADMIN), banka/kullanıcı/liste CRUD'u ve IS_SELF
tekliği. Fixture test_fi_desk_routes ile aynı desen (LOGIN_DISABLED →
_is_admin dev fallback'i True; 403 senaryoları monkeypatch'le)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
pytest.importorskip("duckdb")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from flask import Flask  # noqa: E402
from flask_login import LoginManager  # noqa: E402
from jinja2 import ChoiceLoader, DictLoader  # noqa: E402

import fi_desk  # noqa: E402
from fi_desk import db  # noqa: E402

_BASE_STUB = ("{% block title %}{% endblock %}{% block head %}{% endblock %}"
              "{% block content %}{% endblock %}{% block scripts %}{% endblock %}")


class _StubDC:  # get_connection YOK → dev DuckDB
    pass


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_dev_db_path", lambda: tmp_path / "fi.duckdb")
    db.reset_dev_store()
    app = Flask(__name__)
    app.config["LOGIN_DISABLED"] = True
    app.config["SECRET_KEY"] = "test"
    lm = LoginManager()
    lm.init_app(app)
    lm.user_loader(lambda uid: None)
    fi_desk.init_app(_StubDC(), schema="IGNORED")
    app.register_blueprint(fi_desk.fi_desk_bp, url_prefix="/fi-desk")
    # records.html vendor asset'leri deposit_panel.static'ten okur — stub kayıt
    from flask import Blueprint
    app.register_blueprint(
        Blueprint("deposit_panel", __name__, static_folder="static"),
        url_prefix="/deposit-panel")
    app.jinja_loader = ChoiceLoader([
        DictLoader({"home/_base_prisma.html": _BASE_STUB}),
        app.jinja_loader,
    ])
    with app.test_client() as c:
        yield c
    db.reset_dev_store()


def _no_admin(monkeypatch):
    from fi_desk import routes_admin
    monkeypatch.setattr(routes_admin, "_is_admin", lambda: False)


# ─────────────────────────────────────────────────────────────────────────
# Yetki kapısı
# ─────────────────────────────────────────────────────────────────────────
def test_admin_page_renders_for_admin(client):
    r = client.get("/fi-desk/admin")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="bk-table"' in html and 'id="us-table"' in html \
        and 'id="ls-table"' in html
    # Tam yenileme uyarısı görünür (job'lar ekran değişikliklerini ezer)
    assert "TAM YENİLEME" in html


def test_admin_forbidden_without_flag(client, monkeypatch):
    _no_admin(monkeypatch)
    assert client.get("/fi-desk/admin").status_code == 403
    assert client.get("/fi-desk/api/admin/banks").status_code == 403
    assert client.post("/fi-desk/api/admin/users",
                       json={"sicil": "A1", "roles": ["ENTRY"]}).status_code == 403
    r = client.delete("/fi-desk/api/admin/lists",
                      json={"list_name": "EXPORTER", "value_cd": "X"})
    assert r.status_code == 403


def test_records_page_shows_admin_button_only_for_admin(client, monkeypatch):
    html = client.get("/fi-desk/records").get_data(as_text=True)
    assert "/fi-desk/admin" in html
    from fi_desk import routes_records
    monkeypatch.setattr(routes_records, "_is_admin", lambda: False)
    html = client.get("/fi-desk/records").get_data(as_text=True)
    assert "/fi-desk/admin" not in html


# ─────────────────────────────────────────────────────────────────────────
# Bankalar
# ─────────────────────────────────────────────────────────────────────────
def test_bank_crud_and_self_uniqueness(client):
    # Ekle
    r = client.post("/fi-desk/api/admin/banks", json={
        "bank_nm": "Yeni Banka", "country": "GERMANY", "region": "Europe",
        "group_company": "Yeni Grup"})
    assert r.get_json()["ok"], r.get_json()
    rows = client.get("/fi-desk/api/admin/banks").get_json()["rows"]
    by = {b["BANK_NM"]: b for b in rows}
    assert by["Yeni Banka"]["COUNTRY"] == "GERMANY"

    # Aynı adla ikinci ekleme → 409
    assert client.post("/fi-desk/api/admin/banks",
                       json={"bank_nm": "Yeni Banka"}).status_code == 409

    # Güncelle + ad değişikliği
    r = client.post("/fi-desk/api/admin/banks", json={
        "orig_bank_nm": "Yeni Banka", "bank_nm": "Yeni Banka AG",
        "country": "GERMANY", "region": "Europe"})
    assert r.get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/banks").get_json()["rows"]
    names = {b["BANK_NM"] for b in rows}
    assert "Yeni Banka AG" in names and "Yeni Banka" not in names

    # IS_SELF tekliği: yeni banka self yapılınca eski self (QNB seed) düşer
    r = client.post("/fi-desk/api/admin/banks", json={
        "orig_bank_nm": "Yeni Banka AG", "bank_nm": "Yeni Banka AG",
        "is_self": True})
    assert r.get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/banks").get_json()["rows"]
    selfs = [b["BANK_NM"] for b in rows if b["IS_SELF"]]
    assert selfs == ["Yeni Banka AG"]

    # Self banka silinemez
    r = client.delete("/fi-desk/api/admin/banks",
                      json={"bank_nm": "Yeni Banka AG"})
    assert r.status_code == 409

    # Self olmayan silinir
    r = client.delete("/fi-desk/api/admin/banks", json={"bank_nm": "QNB"})
    assert r.get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/banks").get_json()["rows"]
    assert "QNB" not in {b["BANK_NM"] for b in rows}


# ─────────────────────────────────────────────────────────────────────────
# Kullanıcı rolleri
# ─────────────────────────────────────────────────────────────────────────
def test_user_roles_rewrite_and_delete(client):
    r = client.post("/fi-desk/api/admin/users",
                    json={"sicil": "a99999", "roles": ["entry", "APPROVER"]})
    assert r.get_json()["ok"], r.get_json()
    rows = client.get("/fi-desk/api/admin/users").get_json()["rows"]
    by = {u["sicil"]: u["roles"] for u in rows}
    assert by["A99999"] == ["APPROVER", "ENTRY"]  # normalize + sıralı

    # Rol daraltma: yalnız ENTRY kalır (satırlar sicil bazında yenilenir)
    client.post("/fi-desk/api/admin/users",
                json={"sicil": "A99999", "roles": ["ENTRY"]})
    rows = client.get("/fi-desk/api/admin/users").get_json()["rows"]
    assert {u["sicil"]: u["roles"] for u in rows}["A99999"] == ["ENTRY"]

    # Geçersiz rol / boş rol reddedilir
    assert client.post("/fi-desk/api/admin/users",
                       json={"sicil": "A1", "roles": ["ADMIN"]}).status_code == 400
    assert client.post("/fi-desk/api/admin/users",
                       json={"sicil": "A1", "roles": []}).status_code == 400

    # Sil → rol sorgusundan düşer
    assert client.delete("/fi-desk/api/admin/users",
                         json={"sicil": "A99999"}).get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/users").get_json()["rows"]
    assert "A99999" not in {u["sicil"] for u in rows}


# ─────────────────────────────────────────────────────────────────────────
# Listeler
# ─────────────────────────────────────────────────────────────────────────
def test_list_crud_default_exporter(client):
    out = client.get("/fi-desk/api/admin/lists").get_json()
    assert out["ok"] and out["name"] == "EXPORTER"
    assert "ADDITIONAL_COST_TYPE" in out["names"]

    # Ekle → bootstrap'ın okuduğu tabloya düşer
    r = client.post("/fi-desk/api/admin/lists", json={
        "list_name": "EXPORTER", "value_cd": "ACME",
        "label": "Acme Makine Sanayi A.Ş."})
    assert r.get_json()["ok"], r.get_json()
    rows = client.get("/fi-desk/api/admin/lists?name=EXPORTER").get_json()["rows"]
    by = {x["VALUE_CD"]: x for x in rows}
    assert by["ACME"]["LABEL"] == "Acme Makine Sanayi A.Ş."

    # Güncelle (kod değişikliği dahil)
    r = client.post("/fi-desk/api/admin/lists", json={
        "list_name": "EXPORTER", "orig_value_cd": "ACME",
        "value_cd": "ACME_AS", "label": "Acme A.Ş."})
    assert r.get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/lists?name=EXPORTER").get_json()["rows"]
    cds = {x["VALUE_CD"] for x in rows}
    assert "ACME_AS" in cds and "ACME" not in cds

    # Mükerrer kod → 409, silme → düşer
    client.post("/fi-desk/api/admin/lists",
                json={"list_name": "EXPORTER", "value_cd": "X1", "label": "X"})
    assert client.post("/fi-desk/api/admin/lists",
                       json={"list_name": "EXPORTER",
                             "value_cd": "X1"}).status_code == 409
    assert client.delete("/fi-desk/api/admin/lists",
                         json={"list_name": "EXPORTER",
                               "value_cd": "X1"}).get_json()["ok"]
    rows = client.get("/fi-desk/api/admin/lists?name=EXPORTER").get_json()["rows"]
    assert "X1" not in {x["VALUE_CD"] for x in rows}


def test_additional_cost_type_editable(client):
    """'Admin olarak tip ekleyebilelim' — cost tipi ekranadan eklenince
    bootstrap'taki ADDITIONAL_COST_TYPE listesine düşer."""
    r = client.post("/fi-desk/api/admin/lists", json={
        "list_name": "ADDITIONAL_COST_TYPE", "value_cd": "YENI_TIP",
        "label": "Yeni Tip"})
    assert r.get_json()["ok"]
    boot = client.get("/fi-desk/api/bootstrap").get_json()
    cds = [x["VALUE_CD"] for x in boot["lookups"]["lists"]["ADDITIONAL_COST_TYPE"]]
    assert "YENI_TIP" in cds


# ─────────────────────────────────────────────────────────────────────────
# jobs/ldap_admins.py — metin sözleşmesi (oracledb import edilmeden)
# ─────────────────────────────────────────────────────────────────────────
def test_ldap_admins_job_contract():
    src = (REPO / "jobs" / "ldap_admins.py").read_text("utf-8")
    assert "import argparse" not in src               # Spyder kuralı
    assert "IS_ADMIN NUMBER(1)" in src
    assert "ORA-01430" in src                         # kolon zaten var → geç
    assert "TRESUARY_LDAP" in src
    assert "ADMINS" in src


def test_app_user_reads_is_admin_flag():
    src = (REPO / "app.py").read_text("utf-8")
    assert "self.is_admin" in src
    assert '"IS_ADMIN" in data.columns' in src        # kolon yokken 0

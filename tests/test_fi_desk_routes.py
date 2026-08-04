# -*- coding: utf-8 -*-
"""fi_desk blueprint route'ları — Flask test client + dev DuckDB backend.

Kabuk (home/_base_prisma.html) parity testlerindeki desenle stub'lanır;
LOGIN_DISABLED ile fake kullanıcı (sicil fallback A16438) çalışır — dev
lookup seed'inde bu sicil ENTRY+APPROVER'dır.
"""
from __future__ import annotations

import json
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
    lm.user_loader(lambda uid: None)  # gerçek app'te load_user; anonim yeter
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


def _valid_payload(**field_overrides):
    fields = {
        "DEAL_STATUS": "BIDDING", "LENDER_BANK": "HSBC",
        "OFFER_DT": "2026-07-01", "CURRENCY": "USD",
        "FUNDING_AMT": "50000000", "USD_EQV": "50000000",
        "VALUE_DT": "2026-07-20", "MATURITY_DT": "2027-07-20",
        "REPAYMENT_SCHEDULE": "BULLET", "COVERAGE_FLG": "NO",
        "RATE_TYPE": "FLOATING", "FLOAT_BASE_RATE": "SOFR_3M",
        "FLOAT_SPREAD_BPS": "185", "TRADE_TXN_AMT": "52000000",
        "TRADE_TXN_CCY": "USD",
        "SHIPMENT_DT": "2026-06-15", "TRADE_PAYMENT_DT": "2026-07-15",
        "IMPORTER": "IMP_SAMPLE_1", "EXPORTER": "EXP_SAMPLE_1",
        "BUSINESS_SEGMENT": "CORPORATE", "REFERENCE_NO": "TL-RT",
        "GOODS_DESC": "Makine", "SUSTAINABILITY_FLG": "NO",
    }
    fields.update(field_overrides)
    return {"deal": {"borrower_bank": "QNB", "product_type": "TRADE_LOAN"},
            "fields": fields, "schedule": []}


def test_entry_page_renders(client):
    r = client.get("/fi-desk/entry")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="fi-endpoints"' in html
    assert 'id="f-product"' in html and 'id="btn-submit"' in html
    endpoints = json.loads(
        html.split('id="fi-endpoints" type="application/json">')[1]
        .split("</script>")[0])
    assert endpoints["bootstrap"].endswith("/fi-desk/api/bootstrap")
    assert endpoints["entries"].endswith("/fi-desk/api/entries")


def test_bootstrap_payload(client):
    body = client.get("/fi-desk/api/bootstrap").get_json()
    assert body["ok"] is True
    assert len(body["matrix"]["products"]) == 14
    assert body["self_bank"] == "QNB"
    assert "ENTRY" in body["roles"]
    assert "CURRENCY" in body["lookups"]["lists"]
    assert any(b["BANK_NM"] == "HSBC" for b in body["lookups"]["banks"])


def test_bootstrap_preserves_field_order(client):
    """Form drill-down akışı matris SIRASINA dayanır — jsonify alfabetik
    sıralayıp bozuyordu (2026-07-31 saha: form alfabetik akıyor, koşullu
    alanlar tetikleyicinin ÖNÜNE düşüyordu)."""
    body = client.get("/fi-desk/api/bootstrap").get_json()
    keys = list(body["matrix"]["fields"])
    assert keys[:5] == ["BORROWER_BANK", "PRODUCT_TYPE", "DEAL_STATUS",
                        "REPORTING_STATUS", "LENDER_BANK"]
    # Koşullu alanlar tetikleyicilerinden SONRA gelmeli
    assert keys.index("RATE_TYPE") < keys.index("FIXED_RATE_BPS")
    assert keys.index("COVERAGE_FLG") < keys.index("COVERAGE_PROVIDER")
    assert keys.index("REPAYMENT_SCHEDULE") < keys.index("COVERAGE_FLG")


def test_create_entry_validation_error(client):
    r = client.post("/fi-desk/api/entries",
                    json=_valid_payload(CURRENCY="", LENDER_BANK=""))
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    fields = {e["field"] for e in body["errors"]}
    assert {"CURRENCY", "LENDER_BANK"} <= fields


def test_create_entry_and_attach_second_offer(client):
    r = client.post("/fi-desk/api/entries", json=_valid_payload())
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body
    deal_id = body["deal_id"]

    # Aynı deal'a ikinci lender teklifi: deal INSERT atlanır, başlık DB'den
    p2 = _valid_payload(LENDER_BANK="Deutsche Bank")
    p2["deal"]["deal_id"] = deal_id
    p2["deal"]["borrower_bank"] = "EZİLMEMELİ"  # DB'deki başlık kazanır
    r2 = client.post("/fi-desk/api/entries", json=p2)
    body2 = r2.get_json()
    assert r2.status_code == 200 and body2["ok"], body2
    assert body2["deal_id"] == deal_id
    assert body2["offer_id"] != body["offer_id"]

    deals = db.query("SELECT COUNT(*) AS N FROM FI_DEALS")[0]["N"]
    offers = db.query("SELECT COUNT(*) AS N FROM FI_OFFERS")[0]["N"]
    events = db.query("SELECT COUNT(*) AS N FROM FI_OFFER_EVENTS")[0]["N"]
    assert (deals, offers, events) == (1, 2, 2)
    pend = db.query("SELECT COUNT(*) AS N FROM FI_OFFER_EVENTS "
                    "WHERE APPROVAL_STATUS = 'PENDING'")[0]["N"]
    assert pend == 2


def test_attach_to_unknown_deal_404(client):
    p = _valid_payload()
    p["deal"]["deal_id"] = "FID-YOK"
    r = client.post("/fi-desk/api/entries", json=p)
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────
# Faz 2 — liste / detay / edit / onay
# ─────────────────────────────────────────────────────────────────────────
def _create(client, **overrides):
    body = client.post("/fi-desk/api/entries",
                       json=_valid_payload(**overrides)).get_json()
    assert body["ok"], body
    return body


def _approve(client, event_id):
    r = client.post(f"/fi-desk/api/events/{event_id}/approval",
                    json={"action": "approve"})
    assert r.get_json()["ok"], r.get_json()
    return r


def test_records_page_renders(client):
    r = client.get("/fi-desk/records")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="fi-grid"' in html and 'id="fi-ov"' in html
    assert "__OID__" in html  # detay/edit URL şablonları gömülü


def test_records_pending_then_current(client):
    ids = _create(client)
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert len(rows) == 1
    assert rows[0]["ROW_STATE"] == "PENDING_NEW"
    assert rows[0]["REPORTING_STATUS"] is None
    assert rows[0]["PRODUCT_LABEL"] == "Trade Loan"
    assert rows[0]["TENOR_DAYS"] == 365

    _approve(client, ids["event_id"])
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows[0]["ROW_STATE"] == "CURRENT"
    assert rows[0]["REPORTING_STATUS"] == "BIDDING"
    assert rows[0]["PENDING_COUNT"] == 0


def test_offer_detail_and_edit_flow(client):
    ids = _create(client)
    _approve(client, ids["event_id"])

    # Edit: spread değişir → EDIT event'i (PENDING), current değişmez
    p = _valid_payload(FLOAT_SPREAD_BPS="200")
    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/events",
                    json={"fields": p["fields"], "schedule": []})
    body = r.get_json()
    assert body["ok"] and body["event_type"] == "EDIT", body

    detail = client.get(f"/fi-desk/api/offers/{ids['offer_id']}").get_json()
    assert detail["ok"]
    assert [e["EVENT_SEQ"] for e in detail["events"]] == [2, 1]
    assert detail["events"][0]["APPROVAL_STATUS"] == "PENDING"
    assert detail["current"]["EVENT_SEQ"] == 1  # onay gelmeden current sabit

    # Liste: current satır + bekleyen güncelleme sayacı
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows[0]["ROW_STATE"] == "CURRENT" and rows[0]["PENDING_COUNT"] == 1

    _approve(client, body["event_id"])
    detail = client.get(f"/fi-desk/api/offers/{ids['offer_id']}").get_json()
    assert detail["current"]["EVENT_SEQ"] == 2
    assert float(detail["current"]["FLOAT_SPREAD_BPS"]) == 200


def test_status_only_edit_classified_as_status_change(client):
    ids = _create(client)
    _approve(client, ids["event_id"])
    p = _valid_payload(DEAL_STATUS="WON")
    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/events",
                    json={"fields": p["fields"], "schedule": []})
    body = r.get_json()
    assert body["ok"] and body["event_type"] == "STATUS_CHANGE", body

    # WON + geçmiş value date → onaylanınca REALIZED (karar #6)
    _approve(client, body["event_id"])
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows[0]["DEAL_STATUS"] == "WON"
    assert rows[0]["REPORTING_STATUS"] == "REALIZED"


def test_reject_keeps_history_and_current(client):
    ids = _create(client)
    _approve(client, ids["event_id"])
    p = _valid_payload(FLOAT_SPREAD_BPS="500")
    body = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/events",
                       json={"fields": p["fields"], "schedule": []}).get_json()
    r = client.post(f"/fi-desk/api/events/{body['event_id']}/approval",
                    json={"action": "reject", "reason": "spread yanlış"})
    assert r.get_json()["ok"]

    detail = client.get(f"/fi-desk/api/offers/{ids['offer_id']}").get_json()
    ev2 = detail["events"][0]
    assert ev2["APPROVAL_STATUS"] == "REJECTED"
    assert ev2["REJECT_REASON"] == "spread yanlış"
    assert detail["current"]["EVENT_SEQ"] == 1  # current etkilenmez

    # Aynı event'e ikinci karar → 409
    r = client.post(f"/fi-desk/api/events/{body['event_id']}/approval",
                    json={"action": "approve"})
    assert r.status_code == 409


# ─────────────────────────────────────────────────────────────────────────
# 2026-08-04 saha turu: eksik-alan boyaması, silme akışı, müşteri araması
# ─────────────────────────────────────────────────────────────────────────
def test_records_missing_deferred_fields_flagged(client):
    """Importer/Exporter/Reference/Goods boş girilebilir; liste satırı
    MISSING_FIELDS ile döner (grid sarı boyar)."""
    ids = _create(client, IMPORTER="", EXPORTER="", REFERENCE_NO="",
                  GOODS_DESC="")
    _approve(client, ids["event_id"])
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows[0]["ROW_STATE"] == "CURRENT"
    assert set(rows[0]["MISSING_FIELDS"]) == {
        "Importer", "Exporter", "Reference No", "Goods / Underlying Trade"}

    # Eksikler edit ile tamamlanınca liste temizlenir
    p = _valid_payload()
    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/events",
                    json={"fields": p["fields"], "schedule": []})
    _approve(client, r.get_json()["event_id"])
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows[0]["MISSING_FIELDS"] == []


def test_delete_flow_requires_approval(client):
    ids = _create(client)
    _approve(client, ids["event_id"])

    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/delete")
    body = r.get_json()
    assert r.status_code == 200 and body["ok"], body

    # Onaylanmadan: satır durur, PENDING_DELETE işaretli; ikinci talep 409
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert len(rows) == 1 and rows[0]["PENDING_DELETE"] is True
    assert client.post(
        f"/fi-desk/api/offers/{ids['offer_id']}/delete").status_code == 409

    # Timeline'da DELETE event'i PENDING görünür
    detail = client.get(f"/fi-desk/api/offers/{ids['offer_id']}").get_json()
    assert detail["events"][0]["EVENT_TYPE"] == "DELETE"
    assert detail["events"][0]["APPROVAL_STATUS"] == "PENDING"

    # Onaylanınca teklif listeden ve current'tan düşer; geçmiş durur
    _approve(client, body["event_id"])
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert rows == []
    assert client.post(
        f"/fi-desk/api/offers/{ids['offer_id']}/delete").status_code == 409
    events = db.query("SELECT COUNT(*) AS N FROM FI_OFFER_EVENTS")[0]["N"]
    assert events == 2  # append-only: hiçbir satır silinmedi


def test_delete_rejected_keeps_offer(client):
    ids = _create(client)
    _approve(client, ids["event_id"])
    body = client.post(
        f"/fi-desk/api/offers/{ids['offer_id']}/delete").get_json()
    r = client.post(f"/fi-desk/api/events/{body['event_id']}/approval",
                    json={"action": "reject", "reason": "yanlış kayıt değil"})
    assert r.get_json()["ok"]
    rows = client.get("/fi-desk/api/records").get_json()["rows"]
    assert len(rows) == 1 and rows[0]["ROW_STATE"] == "CURRENT"
    assert rows[0]["PENDING_DELETE"] is False


def test_delete_requires_entry_role(client, monkeypatch):
    ids = _create(client)
    from fi_desk import routes_records
    monkeypatch.setattr(routes_records, "_user_roles", lambda: {"APPROVER"})
    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/delete")
    assert r.status_code == 403


def test_customer_search_fallback_list(client):
    """Müşteri tablosu konfigüre değil (dev) → FI_LU_LIST IMPORTER
    fallback'i; 3 karakter altı arama sonuç döndürmez."""
    body = client.get("/fi-desk/api/customers?q=Ör").get_json()
    assert body["ok"] and body["rows"] == []

    body = client.get("/fi-desk/api/customers?q=Örnek").get_json()
    assert body["ok"] and body["source"] == "lookup"
    assert body["rows"] == ["Örnek İthalatçı A.Ş."]

    body = client.get("/fi-desk/api/customers?q=Bulunamayan").get_json()
    assert body["ok"] and body["rows"] == []


def test_entry_with_additional_costs_roundtrip(client):
    """Cost map'i event'e yazılır; all-in string'i sunucuda derlenir."""
    ids = _create(client, ADDITIONAL_COSTS='{"MUSTERI_PRIMI": 15}')
    ev = db.query("SELECT * FROM FI_OFFER_EVENTS WHERE EVENT_ID = :e",
                  {"e": ids["event_id"]})[0]
    assert json.loads(ev["ADDITIONAL_COSTS"]) == {"MUSTERI_PRIMI": 15}
    assert float(ev["ALL_IN_RATE_BPS"]) == 200  # 185 + 0 (cov yok) + 15
    assert ev["ALL_IN_RATE_TXT"] == "3M SOFR + 200 bps"


def test_approval_requires_approver_role(client, monkeypatch):
    ids = _create(client)
    from fi_desk import routes_records
    monkeypatch.setattr(routes_records, "_user_roles", lambda: {"ENTRY"})
    r = client.post(f"/fi-desk/api/events/{ids['event_id']}/approval",
                    json={"action": "approve"})
    assert r.status_code == 403


def test_edit_requires_entry_role(client, monkeypatch):
    ids = _create(client)
    from fi_desk import routes_records
    monkeypatch.setattr(routes_records, "_user_roles", lambda: {"APPROVER"})
    r = client.post(f"/fi-desk/api/offers/{ids['offer_id']}/events",
                    json={"fields": {}, "schedule": []})
    assert r.status_code == 403

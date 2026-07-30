# -*- coding: utf-8 -*-
"""FI masası — kayıt listesi / detay / edit / onay uçları (Faz 2).

Sayfa: /fi-desk/records (AG-Grid liste + satır detayı + onay aksiyonları).
Liste satırı = teklif: onaylı current snapshot'ı (V_FI_OFFER_CURRENT) ya da
henüz hiç onaylanmamışsa en son PENDING event ("ONAY BEKLİYOR"). Edit,
entry formunun ?offer=... moduyla yapılır → yeni PENDING event (EDIT ya da
yalnız statü değiştiyse STATUS_CHANGE). Onay/red APPROVER rolüyle event'in
onay kolonlarını günceller (append-only istisnası).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from flask import jsonify, render_template, request, url_for
from flask_login import login_required

from . import (db, service, fi_desk_bp, load_field_matrix, _load_lookups,
               _masa_back_url, _user_roles, _user_sicil)

log = logging.getLogger(__name__)

RECORDS_PROCESS_ID = "uygulamalar.fi_islemler"


def _ser(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return v


def _ser_row(row: dict) -> dict:
    return {k: _ser(v) for k, v in row.items()}


def _endpoints() -> dict:
    return {
        "records": url_for("fi_desk.api_records"),
        "entry_page": url_for("fi_desk.entry"),
        "offer_detail": url_for("fi_desk.api_offer_detail", offer_id="__OID__"),
        "offer_events": url_for("fi_desk.api_offer_events", offer_id="__OID__"),
        "approval": url_for("fi_desk.api_event_approval", event_id=0)
                    .replace("/0/", "/__EID__/"),
    }


@fi_desk_bp.route("/records")
@login_required
def records():
    roles = _user_roles()
    return render_template(
        "fi_desk/records.html",
        can_enter="ENTRY" in roles,
        can_approve="APPROVER" in roles,
        masa_back_url=_masa_back_url(RECORDS_PROCESS_ID),
        endpoints=_endpoints(),
    )


@fi_desk_bp.route("/api/records", methods=["GET"])
@login_required
def api_records():
    """Grid satırları: teklif başına current (onaylı) ya da bekleyen-yeni."""
    try:
        matrix = load_field_matrix()
        labels = {k: v["label"] for k, v in matrix["products"].items()}
        current = {r["OFFER_ID"]: r for r in db.query(
            f"SELECT * FROM {db.qualified('V_FI_OFFER_CURRENT')}")}
        pending = db.query(
            f"SELECT e.*, d.BORROWER_BANK, d.PRODUCT_TYPE, d.DEAL_LABEL "
            f"FROM {db.qualified('FI_OFFER_EVENTS')} e "
            f"JOIN {db.qualified('FI_DEALS')} d ON d.DEAL_ID = e.DEAL_ID "
            "WHERE e.APPROVAL_STATUS = 'PENDING'")

        pending_by_offer: dict[str, list[dict]] = {}
        for ev in pending:
            pending_by_offer.setdefault(ev["OFFER_ID"], []).append(ev)

        rows = []
        for offer_id, cur in current.items():
            row = _ser_row(cur)
            row["ROW_STATE"] = "CURRENT"
            row["PENDING_COUNT"] = len(pending_by_offer.get(offer_id, []))
            row["PRODUCT_LABEL"] = labels.get(row.get("PRODUCT_TYPE"),
                                              row.get("PRODUCT_TYPE"))
            rows.append(row)
        for offer_id, evs in pending_by_offer.items():
            if offer_id in current:
                continue
            latest = max(evs, key=lambda e: e["EVENT_SEQ"])
            row = _ser_row(latest)
            row["ROW_STATE"] = "PENDING_NEW"
            row["PENDING_COUNT"] = len(evs)
            row["REPORTING_STATUS"] = None
            row["PRODUCT_LABEL"] = labels.get(row.get("PRODUCT_TYPE"),
                                              row.get("PRODUCT_TYPE"))
            vd, md = latest.get("VALUE_DT"), latest.get("MATURITY_DT")
            row["TENOR_DAYS"] = (md - vd).days if vd and md else None
            rows.append(row)

        rows.sort(key=lambda r: r.get("EVENT_TS") or "", reverse=True)
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        log.exception("fi_desk records başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


@fi_desk_bp.route("/api/offers/<offer_id>", methods=["GET"])
@login_required
def api_offer_detail(offer_id: str):
    """Detay: deal başlığı + tüm event geçmişi (yeni→eski) + ödeme planları."""
    try:
        events = db.query(
            f"SELECT * FROM {db.qualified('FI_OFFER_EVENTS')} "
            "WHERE OFFER_ID = :o ORDER BY EVENT_SEQ DESC", {"o": offer_id})
        if not events:
            return jsonify({"ok": False,
                            "error": f"Teklif bulunamadı: {offer_id}"}), 404
        deal = db.query(
            f"SELECT * FROM {db.qualified('FI_DEALS')} WHERE DEAL_ID = :d",
            {"d": events[0]["DEAL_ID"]})[0]
        schedules: dict[int, list[dict]] = {}
        for row in db.query(
                f"SELECT * FROM {db.qualified('FI_OFFER_SCHEDULE')} "
                "WHERE OFFER_ID = :o ORDER BY EVENT_ID, SORT_NO",
                {"o": offer_id}):
            schedules.setdefault(int(row["EVENT_ID"]), []).append(_ser_row(row))
        current = db.query(
            f"SELECT * FROM {db.qualified('V_FI_OFFER_CURRENT')} "
            "WHERE OFFER_ID = :o", {"o": offer_id})
        matrix = load_field_matrix()
        return jsonify({
            "ok": True,
            "deal": _ser_row(deal),
            "product_label": matrix["products"]
                .get(deal["PRODUCT_TYPE"], {}).get("label", deal["PRODUCT_TYPE"]),
            "events": [_ser_row(e) for e in events],
            "schedules": {str(k): v for k, v in schedules.items()},
            "current": _ser_row(current[0]) if current else None,
        })
    except Exception as e:
        log.exception("fi_desk offer detail başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


@fi_desk_bp.route("/api/offers/<offer_id>/events", methods=["POST"])
@login_required
def api_offer_events(offer_id: str):
    """Edit: mevcut teklife yeni snapshot event'i (PENDING).

    Ürün/borrower deal'dan gelir (değiştirilemez); yalnız DEAL_STATUS
    değiştiyse event tipi STATUS_CHANGE olur (LOST→BIDDING dahil — karar #6).
    """
    if "ENTRY" not in _user_roles():
        return jsonify({"ok": False,
                        "error": "Veri girişi için ENTRY rolü gerekli"}), 403
    try:
        events = db.query(
            f"SELECT * FROM {db.qualified('FI_OFFER_EVENTS')} "
            "WHERE OFFER_ID = :o ORDER BY EVENT_SEQ DESC", {"o": offer_id})
        if not events:
            return jsonify({"ok": False,
                            "error": f"Teklif bulunamadı: {offer_id}"}), 404
        latest = events[0]
        deal = db.query(
            f"SELECT * FROM {db.qualified('FI_DEALS')} WHERE DEAL_ID = :d",
            {"d": latest["DEAL_ID"]})[0]

        payload = request.get_json(force=True) or {}
        payload["deal"] = {"deal_id": deal["DEAL_ID"],
                           "borrower_bank": deal["BORROWER_BANK"],
                           "product_type": deal["PRODUCT_TYPE"]}
        try:
            clean = service.validate_entry(load_field_matrix(), _load_lookups(),
                                           payload)
        except service.ValidationError as ve:
            return jsonify({"ok": False, "errors": ve.errors}), 400

        event_type = service.classify_edit(latest, clean["fields"])
        next_id = db.query(
            f"SELECT COALESCE(MAX(EVENT_ID), 0) + 1 AS NEXT_ID "
            f"FROM {db.qualified('FI_OFFER_EVENTS')}")[0]["NEXT_ID"]
        statements = service.build_edit_statements(
            clean, offer_id, deal["DEAL_ID"], _user_sicil(), db.qualified,
            int(next_id), int(latest["EVENT_SEQ"]) + 1, event_type)
        db.execute(statements)

        log.info("fi_desk edit: offer=%s event=%s type=%s user=%s",
                 offer_id, next_id, event_type, _user_sicil())
        return jsonify({"ok": True, "warnings": clean["warnings"],
                        "offer_id": offer_id, "event_id": int(next_id),
                        "event_type": event_type})
    except Exception as e:
        log.exception("fi_desk edit başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


@fi_desk_bp.route("/api/events/<int:event_id>/approval", methods=["POST"])
@login_required
def api_event_approval(event_id: int):
    """Onay/red — {action: 'approve'|'reject', reason?}. APPROVER rolü."""
    if "APPROVER" not in _user_roles():
        return jsonify({"ok": False,
                        "error": "Onay için APPROVER rolü gerekli"}), 403
    try:
        body = request.get_json(force=True) or {}
        action = {"approve": "APPROVED", "reject": "REJECTED"}.get(
            (body.get("action") or "").strip().lower())
        if action is None:
            return jsonify({"ok": False,
                            "error": "action 'approve' ya da 'reject' olmalı"}), 400
        rows = db.query(
            f"SELECT APPROVAL_STATUS FROM {db.qualified('FI_OFFER_EVENTS')} "
            "WHERE EVENT_ID = :e", {"e": event_id})
        if not rows:
            return jsonify({"ok": False,
                            "error": f"Event bulunamadı: {event_id}"}), 404
        if rows[0]["APPROVAL_STATUS"] != "PENDING":
            return jsonify({"ok": False,
                            "error": "Event beklemede değil "
                                     f"({rows[0]['APPROVAL_STATUS']})"}), 409
        db.execute([service.build_approval_statement(
            event_id, action, _user_sicil(), db.qualified,
            reason=body.get("reason"))])
        log.info("fi_desk onay: event=%s action=%s user=%s",
                 event_id, action, _user_sicil())
        return jsonify({"ok": True, "event_id": event_id, "status": action})
    except Exception as e:
        log.exception("fi_desk onay başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500

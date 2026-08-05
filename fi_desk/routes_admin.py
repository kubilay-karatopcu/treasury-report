# -*- coding: utf-8 -*-
"""FI masası — YÖNETİM ekranı (lookup tabloları, 2026-08-05).

Karar değişikliği: "lookup'lar uygulamadan editlenmez" (eski §1.4) masa
işleyişinde yavaş kaldı — banka/exporter/rol düzeltmeleri için ofis
script'i beklemek istenmiyor. Artık IS_ADMIN bayraklı kullanıcılar
(TRESUARY_LDAP.IS_ADMIN — jobs/ldap_admins.py) /fi-desk/admin ekranından
şu tabloları yönetir:

  - FI_LU_BANK       banka → ülke/region/group company (+ IS_SELF teklik)
  - FI_LU_USER       sicil → ENTRY/APPROVER rolleri (tam satır yenileme)
  - FI_LU_LIST       liste kayıtları (EXPORTER varsayılan; ADDITIONAL_COST_TYPE,
                     COVERAGE_PROVIDER... — liste seçilerek hepsi düzenlenir)

DİKKAT — ofis job'ları TAM YENİLEME yapar: fi_desk_lookup_import.py
(FI_LU_BANK + COUNTRY + EXPORTER) ve fi_desk_users.py (FI_LU_USER) koşarsa
buradaki değişikliklerin üzerine yazar. Job'lar artık İLK KURULUM /
excel'den toplu yükleme içindir; günlük bakım bu ekrandır (spec §5b).

Silme HARD DELETE'tir: lookup satırı yalnız seçim listesini besler —
geçmiş event'ler değerleri metin olarak taşıdığından tarihçe bozulmaz.
"""
from __future__ import annotations

import logging
from datetime import datetime

from flask import abort, jsonify, render_template, request, url_for
from flask_login import login_required

from . import db, fi_desk_bp, _is_admin, _masa_back_url, _user_sicil

log = logging.getLogger(__name__)

#: FI_LU_LIST'te yönetilebilir listeler — DISTINCT ile birleşir (yeni liste
#: adı DB'de belirirse kendiliğinden seçilebilir olur).
KNOWN_LISTS = ["EXPORTER", "IMPORTER", "ADDITIONAL_COST_TYPE", "CURRENCY",
               "COUNTRY", "REPAYMENT", "COVERAGE_PROVIDER", "BASE_RATE",
               "BUSINESS_SEGMENT", "ESG_TYPE", "ESG_ELIGIBILITY"]

VALID_ROLES = {"ENTRY", "APPROVER"}


def _admin_guard():
    """(hata_cevabı | None) — None → devam et."""
    if _is_admin():
        return None
    return jsonify({"ok": False,
                    "error": "Bu işlem için masa admin yetkisi gerekli "
                             "(LDAP IS_ADMIN)"}), 403


def _s(v, maxlen: int = 256) -> str | None:
    s = str(v or "").strip()
    return s[:maxlen] if s else None


# ─────────────────────────────────────────────────────────────────────────
# Sayfa
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/admin")
@login_required
def admin_page():
    if not _is_admin():
        abort(403)
    return render_template(
        "fi_desk/admin.html",
        masa_back_url=_masa_back_url(),
        records_url=url_for("fi_desk.records"),
        endpoints={
            "banks": url_for("fi_desk.api_admin_banks"),
            "users": url_for("fi_desk.api_admin_users"),
            "lists": url_for("fi_desk.api_admin_lists"),
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# FI_LU_BANK
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/api/admin/banks", methods=["GET", "POST", "DELETE"])
@login_required
def api_admin_banks():
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        if request.method == "GET":
            rows = db.query(
                f"SELECT * FROM {db.qualified('FI_LU_BANK')} "
                "ORDER BY IS_SELF DESC, BANK_NM")
            return jsonify({"ok": True, "rows": rows})

        body = request.get_json(force=True) or {}
        if request.method == "DELETE":
            bank = _s(body.get("bank_nm"))
            if not bank:
                return jsonify({"ok": False, "error": "bank_nm zorunlu"}), 400
            row = db.query(
                f"SELECT IS_SELF FROM {db.qualified('FI_LU_BANK')} "
                "WHERE BANK_NM = :b", {"b": bank})
            if not row:
                return jsonify({"ok": False,
                                "error": f"Banka bulunamadı: {bank}"}), 404
            if row[0].get("IS_SELF"):
                return jsonify({"ok": False,
                                "error": "Self banka (Borrower varsayılanı) "
                                         "silinemez — önce başka bankayı self "
                                         "yapın"}), 409
            db.execute([(f"DELETE FROM {db.qualified('FI_LU_BANK')} "
                         "WHERE BANK_NM = :b", {"b": bank})])
            log.info("fi_desk admin: banka silindi %r user=%s", bank,
                     _user_sicil())
            return jsonify({"ok": True})

        # POST — ekle / güncelle (orig_bank_nm doluysa güncelleme; ad
        # değişebilir). IS_SELF=1 işaretlenirse teklik korunur.
        orig = _s(body.get("orig_bank_nm"))
        bank = _s(body.get("bank_nm"))
        if not bank:
            return jsonify({"ok": False, "error": "Banka adı zorunlu"}), 400
        fields = {
            "bank": bank,
            "country": _s(body.get("country"), 128),
            "region": _s(body.get("region"), 128),
            "grp": _s(body.get("group_company")),
            "grp_c": _s(body.get("group_company_country"), 128),
            "grp_r": _s(body.get("group_company_region"), 128),
            "is_self": 1 if body.get("is_self") else 0,
        }
        exists = {r["BANK_NM"] for r in db.query(
            f"SELECT BANK_NM FROM {db.qualified('FI_LU_BANK')}")}
        if orig and orig not in exists:
            return jsonify({"ok": False,
                            "error": f"Banka bulunamadı: {orig}"}), 404
        if bank in exists and bank != (orig or ""):
            return jsonify({"ok": False,
                            "error": f"Bu adla banka zaten var: {bank}"}), 409

        statements: list[tuple] = []
        if fields["is_self"]:
            statements.append(
                (f"UPDATE {db.qualified('FI_LU_BANK')} SET IS_SELF = 0", {}))
        if orig:
            statements.append((
                f"UPDATE {db.qualified('FI_LU_BANK')} SET BANK_NM = :bank, "
                "COUNTRY = :country, REGION = :region, GROUP_COMPANY = :grp, "
                "GROUP_COMPANY_COUNTRY = :grp_c, GROUP_COMPANY_REGION = :grp_r, "
                "IS_SELF = :is_self, ACTIVE_FLG = 1 WHERE BANK_NM = :orig",
                dict(fields, orig=orig)))
        else:
            statements.append((
                f"INSERT INTO {db.qualified('FI_LU_BANK')} "
                "(BANK_NM, COUNTRY, REGION, GROUP_COMPANY, "
                "GROUP_COMPANY_COUNTRY, GROUP_COMPANY_REGION, IS_SELF, "
                "ACTIVE_FLG, LOADED_AT) VALUES (:bank, :country, :region, "
                ":grp, :grp_c, :grp_r, :is_self, 1, :now)",
                dict(fields, now=datetime.now())))
        db.execute(statements)
        log.info("fi_desk admin: banka kaydedildi %r (orig=%r) user=%s",
                 bank, orig, _user_sicil())
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("fi_desk admin banks başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────
# FI_LU_USER — sicil → roller (satırlar sicil bazında tam yenilenir)
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/api/admin/users", methods=["GET", "POST", "DELETE"])
@login_required
def api_admin_users():
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        if request.method == "GET":
            rows = db.query(
                f"SELECT SICIL, USER_ROLE FROM {db.qualified('FI_LU_USER')} "
                "WHERE ACTIVE_FLG = 1 ORDER BY SICIL, USER_ROLE")
            users: dict[str, list[str]] = {}
            for r in rows:
                users.setdefault(r["SICIL"], []).append(r["USER_ROLE"])
            return jsonify({"ok": True,
                            "rows": [{"sicil": s, "roles": rl}
                                     for s, rl in users.items()]})

        body = request.get_json(force=True) or {}
        sicil = (_s(body.get("sicil"), 16) or "").upper()
        if not sicil:
            return jsonify({"ok": False, "error": "Sicil zorunlu"}), 400

        if request.method == "DELETE":
            db.execute([(f"DELETE FROM {db.qualified('FI_LU_USER')} "
                         "WHERE SICIL = :s", {"s": sicil})])
            log.info("fi_desk admin: kullanıcı silindi %s user=%s",
                     sicil, _user_sicil())
            return jsonify({"ok": True})

        roles = [str(r).strip().upper() for r in (body.get("roles") or [])]
        bad = [r for r in roles if r not in VALID_ROLES]
        if bad:
            return jsonify({"ok": False,
                            "error": f"Geçersiz rol: {', '.join(bad)} "
                                     f"(izinli: {', '.join(sorted(VALID_ROLES))})"}), 400
        if not roles:
            return jsonify({"ok": False,
                            "error": "En az bir rol seçin (kullanıcıyı "
                                     "kaldırmak için Sil kullanın)"}), 400
        statements: list[tuple] = [
            (f"DELETE FROM {db.qualified('FI_LU_USER')} WHERE SICIL = :s",
             {"s": sicil})]
        statements += [(
            f"INSERT INTO {db.qualified('FI_LU_USER')} "
            "(SICIL, USER_ROLE, ACTIVE_FLG, LOADED_AT) "
            "VALUES (:s, :r, 1, :now)",
            {"s": sicil, "r": r, "now": datetime.now()}) for r in sorted(set(roles))]
        db.execute(statements)
        log.info("fi_desk admin: kullanıcı kaydedildi %s roller=%s user=%s",
                 sicil, roles, _user_sicil())
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("fi_desk admin users başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────
# FI_LU_LIST — liste kayıtları (EXPORTER varsayılan, tüm listeler seçilebilir)
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/api/admin/lists", methods=["GET", "POST", "DELETE"])
@login_required
def api_admin_lists():
    guard = _admin_guard()
    if guard is not None:
        return guard
    try:
        if request.method == "GET":
            name = (_s(request.args.get("name"), 32) or "EXPORTER").upper()
            names = {r["LIST_NAME"] for r in db.query(
                f"SELECT DISTINCT LIST_NAME FROM {db.qualified('FI_LU_LIST')}")}
            names |= set(KNOWN_LISTS)
            rows = db.query(
                f"SELECT VALUE_CD, LABEL, PARENT_CD, ATTR1, SORT_NO "
                f"FROM {db.qualified('FI_LU_LIST')} "
                "WHERE LIST_NAME = :n AND ACTIVE_FLG = 1 "
                "ORDER BY SORT_NO, VALUE_CD", {"n": name})
            return jsonify({"ok": True, "name": name,
                            "names": sorted(names), "rows": rows})

        body = request.get_json(force=True) or {}
        name = (_s(body.get("list_name"), 32) or "").upper()
        if not name:
            return jsonify({"ok": False, "error": "list_name zorunlu"}), 400

        if request.method == "DELETE":
            cd = _s(body.get("value_cd"))
            if not cd:
                return jsonify({"ok": False, "error": "value_cd zorunlu"}), 400
            db.execute([(
                f"DELETE FROM {db.qualified('FI_LU_LIST')} "
                "WHERE LIST_NAME = :n AND VALUE_CD = :cd",
                {"n": name, "cd": cd})])
            log.info("fi_desk admin: liste kaydı silindi %s/%r user=%s",
                     name, cd, _user_sicil())
            return jsonify({"ok": True})

        # POST — ekle / güncelle (orig_value_cd doluysa güncelleme)
        orig = _s(body.get("orig_value_cd"))
        cd = _s(body.get("value_cd"))
        if not cd:
            return jsonify({"ok": False, "error": "Değer kodu zorunlu"}), 400
        fields = {
            "n": name, "cd": cd,
            "label": _s(body.get("label")) or cd,
            "parent": _s(body.get("parent_cd")),
            "attr1": _s(body.get("attr1"), 128),
        }
        exists = {r["VALUE_CD"] for r in db.query(
            f"SELECT VALUE_CD FROM {db.qualified('FI_LU_LIST')} "
            "WHERE LIST_NAME = :n", {"n": name})}
        if orig and orig not in exists:
            return jsonify({"ok": False,
                            "error": f"Kayıt bulunamadı: {orig}"}), 404
        if cd in exists and cd != (orig or ""):
            return jsonify({"ok": False,
                            "error": f"Bu kod zaten var: {cd}"}), 409

        if orig:
            statements = [(
                f"UPDATE {db.qualified('FI_LU_LIST')} SET VALUE_CD = :cd, "
                "LABEL = :label, PARENT_CD = :parent, ATTR1 = :attr1, "
                "ACTIVE_FLG = 1 WHERE LIST_NAME = :n AND VALUE_CD = :orig",
                dict(fields, orig=orig))]
        else:
            nxt = db.query(
                f"SELECT COALESCE(MAX(SORT_NO), 0) + 1 AS N "
                f"FROM {db.qualified('FI_LU_LIST')} WHERE LIST_NAME = :n",
                {"n": name})[0]["N"]
            statements = [(
                f"INSERT INTO {db.qualified('FI_LU_LIST')} "
                "(LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1, SORT_NO, "
                "ACTIVE_FLG, LOADED_AT) VALUES (:n, :cd, :label, :parent, "
                ":attr1, :sort_no, 1, :now)",
                dict(fields, sort_no=int(nxt), now=datetime.now()))]
        db.execute(statements)
        log.info("fi_desk admin: liste kaydı yazıldı %s/%r (orig=%r) user=%s",
                 name, cd, orig, _user_sicil())
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("fi_desk admin lists başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500

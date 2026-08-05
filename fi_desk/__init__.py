# -*- coding: utf-8 -*-
"""FI masası veri giriş modülü (Muhabir Bankacılık ve Yapılandırılmış Fonlama).

Faz 1: veri giriş formu + lookup API'leri + ENTRY event yazımı (onay akışı
PENDING ile başlar). Tasarım: docs/FI_DESK_SPEC.md. Oracle şeması:
``jobs/fi_desk_schema.py`` (ofiste tek koşu); alan matrisi tek otorite:
``fi_desk/field_matrix.json``.

Desen deposit_panel ile aynı: blueprint + ``init_app(dc)`` enjeksiyonu,
SELECT'ler taze bağlantıyla, DML tek transaction (``fi_desk/db.py``).
DEV_MODE'da (stub DataClient) db katmanı yerel DuckDB'ye düşer — form
lokalde uçtan uca çalışır.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import (Blueprint, Response, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from . import db, service

log = logging.getLogger(__name__)

_MATRIX_PATH = Path(__file__).resolve().parent / "field_matrix.json"
_matrix_cache: dict | None = None

PROCESS_ID = "uygulamalar.fi_veri_girisi"

fi_desk_bp = Blueprint(
    "fi_desk",
    __name__,
    template_folder="templates",
    static_folder="static",
)


def init_app(dc, schema: str = "A16438", customer_table: str = "",
             customer_name_col: str = "") -> None:
    """app.py çağırır — DataClient enjeksiyonu + hedef Oracle şeması.

    ``customer_table``/``customer_name_col``: Importer typeahead'inin
    arayacağı EDW müşteri tablosu (boşsa FI_LU_LIST IMPORTER fallback'i)."""
    db.init(dc, schema, customer_table=customer_table,
            customer_name_col=customer_name_col)


def load_field_matrix() -> dict:
    """Alan matrisini oku (tek otorite: fi_desk/field_matrix.json)."""
    global _matrix_cache
    if _matrix_cache is None:
        with open(_MATRIX_PATH, encoding="utf-8") as f:
            _matrix_cache = json.load(f)
    return _matrix_cache


# ─────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────
def _login_disabled() -> bool:
    from flask import current_app
    return bool(current_app.config.get("LOGIN_DISABLED"))


def _user_sicil() -> str:
    if _login_disabled():
        return getattr(current_user, "sicil", None) or "A16438"
    return current_user.sicil


def _user_roles() -> set[str] | None:
    """FI_LU_USER'dan roller (ENTRY / APPROVER). LOGIN_DISABLED → hepsi.

    ``None`` = rol sorgusu BAŞARISIZ (DB hatası) — bu, 'rolü yok'tan (boş
    set) FARKLIDIR: sayfalar durumu banner'la gösterir, API'ler 503 döner.
    2026-08-05 saha bulgusu: tek seferlik Oracle hatası sessizce 'rol yok'
    sayılınca Düzenle/Sil butonları açıklamasız kayboluyordu (db.query artık
    taze bağlantıyla bir kez yineliyor; yine de düşerse burası None döner)."""
    if _login_disabled():
        return {"ENTRY", "APPROVER"}
    try:
        rows = db.query(
            f"SELECT USER_ROLE FROM {db.qualified('FI_LU_USER')} "
            "WHERE SICIL = :sicil AND ACTIVE_FLG = 1",
            {"sicil": _user_sicil()},
        )
        return {r["USER_ROLE"] for r in rows}
    except Exception:
        log.exception("fi_desk rol sorgusu başarısız")
        return None


_ROLE_ERROR_MSG = ("Rol bilgisi alınamadı (veritabanı hatası) — lütfen "
                   "sayfayı yenileyip tekrar deneyin")


def _role_guard(required: str, roles_fn=None):
    """API rol kapısı: (hata_cevabı | None). None → devam et.

    ``roles_fn``: çağıran modülün ``_user_roles``'u — call-site'tan geçirilir
    ki testlerin modül bazlı monkeypatch'leri etkisini korusun."""
    roles = (roles_fn or _user_roles)()
    if roles is None:
        return jsonify({"ok": False, "error": _ROLE_ERROR_MSG}), 503
    if required not in roles:
        return jsonify({"ok": False,
                        "error": f"Bu işlem için {required} rolü gerekli"}), 403
    return None


def _masa_back_url(process_id: str = PROCESS_ID):
    """R10 — 'Masaya dön' hedefi (deposit_panel deseni; prisma_home import edilmez)."""
    from flask import current_app
    provider = current_app.config.get("FOLDER_MENU_PROVIDER")
    if provider is None:
        return None
    try:
        department = getattr(current_user, "department", None) or ""
        menu = provider(process_id, department)
        return (menu or {}).get("expert_url")
    except Exception:
        log.warning("fi_desk masa_back_url çözülemedi", exc_info=True)
        return None


def _load_lookups() -> dict:
    """FI_LU_LIST + FI_LU_BANK → service.validate_entry'nin beklediği yapı."""
    lists: dict[str, list[dict]] = {}
    for row in db.query(
            f"SELECT LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1, SORT_NO "
            f"FROM {db.qualified('FI_LU_LIST')} WHERE ACTIVE_FLG = 1 "
            "ORDER BY LIST_NAME, SORT_NO"):
        lists.setdefault(row["LIST_NAME"], []).append(row)
    banks = db.query(
        f"SELECT BANK_NM, COUNTRY, REGION, GROUP_COMPANY, "
        f"GROUP_COMPANY_COUNTRY, GROUP_COMPANY_REGION, IS_SELF "
        f"FROM {db.qualified('FI_LU_BANK')} WHERE ACTIVE_FLG = 1 "
        "ORDER BY IS_SELF DESC, BANK_NM")
    return {"lists": lists, "banks": banks}


# ─────────────────────────────────────────────────────────────────────────
# Sayfalar
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/")
@login_required
def index():
    return redirect(url_for("fi_desk.entry"))


@fi_desk_bp.route("/entry")
@login_required
def entry():
    roles = _user_roles()
    role_error = roles is None
    roles = roles or set()
    return render_template(
        "fi_desk/entry.html",
        can_enter="ENTRY" in roles,
        role_error=role_error,
        # Silme butonu sunucuda çizilir: yalnız edit modunda (?offer=...)
        edit_offer=(request.args.get("offer") or "").strip() or None,
        masa_back_url=_masa_back_url(),
        endpoints={
            "bootstrap": url_for("fi_desk.api_bootstrap"),
            "entries": url_for("fi_desk.api_create_entry"),
            "records_page": url_for("fi_desk.records"),
            "customers": url_for("fi_desk.api_customers"),
            # Edit modu (?offer=FIO-...): __OID__ JS'te teklif id'siyle değişir
            "offer_detail": url_for("fi_desk.api_offer_detail",
                                    offer_id="__OID__"),
            "offer_events": url_for("fi_desk.api_offer_events",
                                    offer_id="__OID__"),
            "offer_delete": url_for("fi_desk.api_offer_delete",
                                    offer_id="__OID__"),
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────
@fi_desk_bp.route("/api/bootstrap", methods=["GET"])
@login_required
def api_bootstrap():
    """Formun tek seferde ihtiyaç duyduğu her şey: matris + lookup + roller."""
    try:
        lookups = _load_lookups()
        self_bank = next((b["BANK_NM"] for b in lookups["banks"]
                          if b.get("IS_SELF")), None)
        payload = {
            "ok": True,
            "matrix": load_field_matrix(),
            "lookups": lookups,
            "roles": sorted(_user_roles()),
            "self_bank": self_bank,
        }
        # jsonify DEĞİL: Flask jsonify key'leri alfabetik sıralar ve matristeki
        # alan SIRASI kaybolur — form alanları drill-down akış sırasında
        # (lender → offer → koşullu alanlar tetikleyicinin ARKASINDA) çizilmeli
        # (2026-07-31 saha bulgusu: form alfabetik akıyordu).
        return Response(json.dumps(payload, ensure_ascii=False, default=str),
                        mimetype="application/json")
    except Exception as e:
        log.exception("fi_desk bootstrap başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


@fi_desk_bp.route("/api/customers", methods=["GET"])
@login_required
def api_customers():
    """Importer typeahead — ?q= en az 3 karakter, en çok 30 sonuç.

    Kaynak konfigüre edilmiş EDW müşteri tablosudur (init_app customer_table;
    UPPER-önek LIKE + ROWNUM erken kesme — 32M satırda contains araması
    yapılmaz, docs/FI_DESK_SPEC.md §8). Konfigürasyon yoksa/dev'de
    FI_LU_LIST IMPORTER listesine düşülür."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 3:
        return jsonify({"ok": True, "rows": [],
                        "note": "en az 3 karakter girin"})
    try:
        rows = db.customer_search(q, limit=30)
        return jsonify({"ok": True, "rows": rows,
                        "source": db.customer_source()})
    except Exception as e:
        log.exception("fi_desk müşteri araması başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


@fi_desk_bp.route("/api/entries", methods=["POST"])
@login_required
def api_create_entry():
    """Yeni giriş: deal (+varsa mevcut deal'a teklif) + offer + ENTRY event.

    Event PENDING doğar; onaycı ekranı (Faz 2) APPROVED/REJECTED yapar.
    """
    guard = _role_guard("ENTRY", _user_roles)
    if guard is not None:
        return guard
    try:
        payload = request.get_json(force=True) or {}
        matrix = load_field_matrix()
        lookups = _load_lookups()

        deal_in = payload.get("deal") or {}
        deal_id = (deal_in.get("deal_id") or "").strip() or None
        deal_exists = False
        if deal_id:
            rows = db.query(
                f"SELECT DEAL_ID, BORROWER_BANK, PRODUCT_TYPE "
                f"FROM {db.qualified('FI_DEALS')} WHERE DEAL_ID = :d",
                {"d": deal_id})
            if not rows:
                return jsonify({"ok": False,
                                "error": f"Deal bulunamadı: {deal_id}"}), 404
            # Mevcut deal'a teklif: başlık DB'den gelir, payload ezemez.
            deal_exists = True
            deal_in = dict(deal_in,
                           borrower_bank=rows[0]["BORROWER_BANK"],
                           product_type=rows[0]["PRODUCT_TYPE"])
            payload = dict(payload, deal=deal_in)

        try:
            clean = service.validate_entry(matrix, lookups, payload)
        except service.ValidationError as ve:
            return jsonify({"ok": False, "errors": ve.errors}), 400

        next_id = db.query(
            f"SELECT COALESCE(MAX(EVENT_ID), 0) + 1 AS NEXT_ID "
            f"FROM {db.qualified('FI_OFFER_EVENTS')}")[0]["NEXT_ID"]
        statements, ids = service.build_entry_statements(
            clean, _user_sicil(), db.qualified, int(next_id),
            deal_exists=deal_exists)
        db.execute(statements)

        log.info("fi_desk giriş: deal=%s offer=%s event=%s user=%s",
                 ids["deal_id"], ids["offer_id"], ids["event_id"], _user_sicil())
        return jsonify({"ok": True, "warnings": clean["warnings"], **ids})
    except Exception as e:
        log.exception("fi_desk giriş başarısız")
        return jsonify({"ok": False, "error": str(e)}), 500


# Faz 2 route'ları aynı blueprint'e kaydolur (import yan etkisi).
from . import routes_records  # noqa: E402,F401

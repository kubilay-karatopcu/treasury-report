# -*- coding: utf-8 -*-
"""FI masası veri girişi — matris tabanlı doğrulama + event üretimi.

Tek otorite ``fi_desk/field_matrix.json``: hangi alan hangi üründe zorunlu
(R) / opsiyonel (O) / yok (-), koşullu kurallar (required_if) ve otomatik
doldurmalar (auto). Bu modül saf Python'dur (Flask/pandas yok) — testler
doğrudan çağırır.

Akış: ``validate_entry`` payload'ı doğrular ve normalize eder →
``build_entry_statements`` deal/offer/event (+ ödeme planı) INSERT'lerini
bind'li olarak üretir → route ``db.execute`` ile tek transaction'da koşar.
Event PENDING doğar (onay akışı, docs/FI_DESK_SPEC.md §5).
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

# FI_OFFER_EVENTS'in veri kolonları — jobs/fi_desk_schema.py DDL'i ile ve
# field_matrix.json storage=event alanlarıyla senkron (test doğrular).
EVENT_DATA_COLS = [
    "DEAL_STATUS", "LENDER_BANK", "LENDER_COUNTRY", "LENDER_REGION",
    "GROUP_COMPANY", "GROUP_COMPANY_COUNTRY", "GROUP_COMPANY_REGION",
    "OFFER_DT", "CURRENCY", "FUNDING_AMT", "USD_EQV", "VALUE_DT",
    "MATURITY_DT", "REPAYMENT_SCHEDULE", "COVERAGE_FLG", "COVERAGE_PROVIDER",
    "RATE_TYPE", "FIXED_RATE_BPS", "FLOAT_BASE_RATE", "FIXING_DT",
    "FLOAT_SPREAD_BPS",
    "COVERAGE_RATE_BPS", "FEE", "ADDITIONAL_COSTS", "ALL_IN_RATE_BPS",
    "ALL_IN_RATE_TXT",
    "ALL_IN_FIXED_USD_RATE", "TRADE_TXN_AMT", "TRADE_TXN_CCY", "SHIPMENT_DT",
    "TRADE_PAYMENT_DT", "IMPORTER", "EXPORTER", "BUSINESS_SEGMENT",
    "REFERENCE_NO", "GOODS_DESC", "SUSTAINABILITY_FLG", "ESG_TYPE",
    "ESG_ELIGIBILITY", "NOTES",
]

DATE_FIELDS = {"OFFER_DT", "VALUE_DT", "MATURITY_DT", "SHIPMENT_DT",
               "TRADE_PAYMENT_DT", "FIXING_DT"}
NUMBER_FIELDS = {"FUNDING_AMT", "USD_EQV", "FIXED_RATE_BPS",
                 "FLOAT_SPREAD_BPS", "COVERAGE_RATE_BPS", "FEE",
                 "ALL_IN_RATE_BPS",
                 "ALL_IN_FIXED_USD_RATE", "TRADE_TXN_AMT"}

#: Matris kodu "D": girişte opsiyonel, sonradan tamamlanması beklenen alan
#: (Importer/Exporter/Reference/Goods — 2026-08-04 saha kararı). İşlem
#: listesi eksik-D satırları sarı boyar (missing_deferred).
DEFERRED_CODE = "D"


class ValidationError(Exception):
    """Alan bazlı doğrulama hataları — ``errors``: [{field, message}]."""

    def __init__(self, errors: list[dict]):
        super().__init__(f"{len(errors)} doğrulama hatası")
        self.errors = errors


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _parse_date(value: Any, field: str, errors: list[dict]) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    errors.append({"field": field,
                   "message": f"Tarih formatı geçersiz: {s!r} (YYYY-MM-DD bekleniyor)"})
    return None


def _parse_number(value: Any, field: str, errors: list[dict]) -> float | None:
    """Esnek sayı: '1,5' ondalık virgül; '50,000,000' binlik ayraç;
    '5e6' bilimsel gösterim — hepsi kabul (2026-07-31 saha isteği)."""
    s = str(value).strip()
    if s.count(",") > 1 or ("," in s and "." in s):
        s = s.replace(",", "")     # binlik ayraç
    else:
        s = s.replace(",", ".")    # ondalık virgül
    try:
        return float(s)
    except (TypeError, ValueError):
        errors.append({"field": field, "message": f"Sayı bekleniyor: {value!r}"})
        return None


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_costs(raw: Any, allowed: dict[str, str],
                 errors: list[dict]) -> str | None:
    """Additional cost kalemleri → JSON map string'i ('{"TIP": bps}').

    Girdi esnek: JSON string, {TIP: bps} sözlüğü ya da [{type, bps}] listesi
    (form hidden input JSON string yollar). Tipler ADDITIONAL_COST_TYPE
    listesine karşı doğrulanır; aynı tip iki kez giremez. Boş set → None.
    Kayıt sort_keys ile stabildir (classify_edit karşılaştırması şaşmasın).
    """
    field = "ADDITIONAL_COSTS"
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            errors.append({"field": field,
                           "message": f"Ek maliyet formatı geçersiz: {raw!r}"})
            return None
    if isinstance(data, list):
        pairs = [((d or {}).get("type") or (d or {}).get("code"),
                  (d or {}).get("bps", (d or {}).get("cost")))
                 for d in data]
    elif isinstance(data, dict):
        pairs = list(data.items())
    else:
        errors.append({"field": field,
                       "message": "Ek maliyetler {tip: bps} yapısında olmalı"})
        return None

    out: dict[str, float] = {}
    for cd, bps in pairs:
        cd = str(cd or "").strip()
        if not cd and _blank(bps):
            continue  # tamamen boş satır — yok say
        if not cd:
            errors.append({"field": field, "message": "Maliyet tipi seçilmedi"})
            continue
        if allowed and cd not in allowed:
            errors.append({"field": field,
                           "message": f"Bilinmeyen maliyet tipi: {cd!r}"})
            continue
        if cd in out:
            errors.append({"field": field,
                           "message": f"Aynı maliyet tipi iki kez girildi: "
                                      f"{allowed.get(cd, cd)}"})
            continue
        if _blank(bps):
            errors.append({"field": field,
                           "message": f"{allowed.get(cd, cd)}: bps değeri boş"})
            continue
        n = _parse_number(bps, field, errors)
        if n is not None:
            out[cd] = n
    return json.dumps(out, ensure_ascii=False, sort_keys=True) if out else None


def costs_total(additional_costs: Any) -> float:
    """ADDITIONAL_COSTS JSON map'inin bps toplamı (boş/None → 0)."""
    if _blank(additional_costs):
        return 0.0
    data = additional_costs
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return 0.0
    if not isinstance(data, dict):
        return 0.0
    return float(sum(v for v in data.values() if isinstance(v, (int, float))))


def missing_deferred(matrix: dict, product: str, row: dict) -> list[str]:
    """Üründe 'D' (sonradan zorunlu) işaretli olup boş kalan alan etiketleri.

    İşlem listesi bu listeyle satırı sarı boyar ve kullanıcıya eksikleri
    hatırlatır (row anahtarları event kolon adlarıdır)."""
    prod = matrix["products"].get(product)
    if not prod:
        return []
    return [matrix["fields"][key]["label"]
            for key, code in prod["fields"].items()
            if code == DEFERRED_CODE and key in matrix["fields"]
            and _blank(row.get(key))]


def _cond_map(matrix: dict) -> dict[str, tuple[str, str]]:
    """required_if kuralları: alan → (tetikleyen alan, beklenen değer)."""
    return {r["field"]: (r["required_if"]["field"], r["required_if"]["equals"])
            for r in matrix["rules"] if "required_if" in r}


def validate_entry(matrix: dict, lookups: dict, payload: dict) -> dict:
    """Payload'ı doğrula + normalize et + otomatik alanları hesapla.

    lookups: ``{"lists": {LIST_NAME: [{VALUE_CD, LABEL, PARENT_CD, ATTR1}]},
    "banks": [{BANK_NM, COUNTRY, REGION, GROUP_COMPANY, ...}]}`` (db katmanı
    ya da test verir). Dönen: ``{"deal": {...}, "fields": {...},
    "schedule": [...], "warnings": [...]}``. Hata varsa ValidationError.
    """
    errors: list[dict] = []
    warnings: list[str] = []

    deal_in = payload.get("deal") or {}
    fields_in = dict(payload.get("fields") or {})
    schedule_in = payload.get("schedule") or []

    banks = {b["BANK_NM"]: b for b in lookups.get("banks", [])}
    lists = lookups.get("lists", {})

    # ── Deal başlığı ────────────────────────────────────────────────────
    product = (deal_in.get("product_type") or "").strip()
    if product not in matrix["products"]:
        raise ValidationError([{"field": "PRODUCT_TYPE",
                                "message": f"Bilinmeyen ürün: {product!r}"}])
    prod_fields: dict[str, str] = matrix["products"][product]["fields"]

    borrower = (deal_in.get("borrower_bank") or "").strip()
    if not borrower:
        errors.append({"field": "BORROWER_BANK", "message": "Borrower Bank zorunlu"})
    elif banks and borrower not in banks:
        errors.append({"field": "BORROWER_BANK",
                       "message": f"Banka listede yok: {borrower!r} (lookup dışı "
                                  "banka dış süreçle eklenir)"})

    deal_id = (deal_in.get("deal_id") or "").strip() or None
    deal = {"deal_id": deal_id, "borrower_bank": borrower,
            "product_type": product,
            "deal_label": (deal_in.get("deal_label") or "").strip() or None}

    # ── Alan doğrulama ──────────────────────────────────────────────────
    conds = _cond_map(matrix)
    values: dict[str, Any] = {}
    required_missing: list[str] = []  # zorunlu boşlar — OTOMATİKLERDEN SONRA
    # kontrol edilir (banka seçilince dolan LENDER_COUNTRY gibi alanlar
    # otomatik hesap şansını almadan hata üretmesin)

    def cond_met(f: str) -> bool:
        c = conds.get(f)
        if c is None:
            return True
        parent, expected = c
        return str(fields_in.get(parent) or "").strip() == expected

    for key, meta in matrix["fields"].items():
        if meta["storage"] not in ("event",):
            continue
        req = prod_fields.get(key, "-")
        raw = fields_in.get(key)

        if req == "-":
            if not _blank(raw):
                warnings.append(f"{key}: bu üründe yok, yok sayıldı")
            values[key] = None
            continue
        if not cond_met(key):
            # Koşul sağlanmadı → alan devre dışı (dolu geldiyse temizlenir).
            if not _blank(raw):
                warnings.append(f"{key}: koşulu sağlanmadı, temizlendi")
            values[key] = None
            continue
        if _blank(raw):
            values[key] = None
            if req == "R":
                required_missing.append(key)
            continue

        # Tip normalize
        if meta["input"] == "cost_list":
            allowed = {r["VALUE_CD"]: r.get("LABEL") or r["VALUE_CD"]
                       for r in lists.get(meta.get("list") or "", [])}
            values[key] = _parse_costs(raw, allowed, errors)
            continue
        if key in DATE_FIELDS:
            values[key] = _parse_date(raw, key, errors)
        elif key in NUMBER_FIELDS:
            values[key] = _parse_number(raw, key, errors)
        else:
            values[key] = str(raw).strip()

        # Enum / liste doğrulama
        opts = meta.get("options")
        if opts and values[key] is not None and values[key] not in opts:
            errors.append({"field": key,
                           "message": f"Geçersiz değer: {values[key]!r} "
                                      f"(beklenen: {', '.join(opts)})"})
        list_name = meta.get("list")
        if list_name and values[key] is not None:
            valid = {r["VALUE_CD"] for r in lists.get(list_name, [])}
            if valid and values[key] not in valid:
                errors.append({"field": key,
                               "message": f"{meta['label']}: listede yok "
                                          f"({values[key]!r})"})
        if meta["input"] == "lookup_bank" and banks and values[key] is not None \
                and values[key] not in banks:
            errors.append({"field": key,
                           "message": f"Banka listede yok: {values[key]!r}"})

    # ── Otomatik alanlar (sunucu otoritedir) ────────────────────────────
    lender = banks.get(values.get("LENDER_BANK") or "")
    if lender is not None:
        if _blank(values.get("LENDER_COUNTRY")) and prod_fields.get("LENDER_COUNTRY") != "-":
            values["LENDER_COUNTRY"] = lender.get("COUNTRY")
        for fk, bk in (("GROUP_COMPANY", "GROUP_COMPANY"),
                       ("GROUP_COMPANY_COUNTRY", "GROUP_COMPANY_COUNTRY"),
                       ("GROUP_COMPANY_REGION", "GROUP_COMPANY_REGION")):
            if prod_fields.get(fk) != "-" and _blank(values.get(fk)):
                values[fk] = lender.get(bk)
    if _blank(values.get("LENDER_REGION")) and prod_fields.get("LENDER_REGION") != "-":
        country = values.get("LENDER_COUNTRY")
        row = next((r for r in lists.get("COUNTRY", [])
                    if r["VALUE_CD"] == country), None)
        if row is not None:
            values["LENDER_REGION"] = row.get("ATTR1")
    if values.get("COVERAGE_FLG") == "NO" and _blank(values.get("COVERAGE_RATE_BPS")) \
            and prod_fields.get("COVERAGE_RATE_BPS") != "-":
        values["COVERAGE_RATE_BPS"] = 0.0
    # Fixing date varsayılanı: value date - 2 takvim günü (elle ezilebilir)
    if _blank(values.get("FIXING_DT")) and cond_met("FIXING_DT") \
            and prod_fields.get("FIXING_DT") in ("R", "O") \
            and values.get("VALUE_DT") is not None:
        values["FIXING_DT"] = values["VALUE_DT"] - timedelta(days=2)
    if _blank(values.get("ALL_IN_RATE_BPS")) and prod_fields.get("ALL_IN_RATE_BPS") == "R":
        base = values.get("FIXED_RATE_BPS") if values.get("RATE_TYPE") == "FIXED" \
            else values.get("FLOAT_SPREAD_BPS")
        if base is not None:
            values["ALL_IN_RATE_BPS"] = (float(base)
                                         + float(values.get("COVERAGE_RATE_BPS") or 0)
                                         + costs_total(values.get("ADDITIONAL_COSTS")))
    # All-in string gösterimi (2026-08-04): FLOATING → "{base etiketi} +
    # {all-in} bps", FIXED → "{all-in} bps (Fixed)". Sunucu otoritedir —
    # formdan gelen değer (readonly alan) daima yeniden hesaplanır.
    if prod_fields.get("ALL_IN_RATE_TXT", "-") != "-":
        allin = values.get("ALL_IN_RATE_BPS")
        if allin is None:
            values["ALL_IN_RATE_TXT"] = None
        else:
            allin_s = f"{float(allin):g}"
            if values.get("RATE_TYPE") == "FLOATING":
                base = values.get("FLOAT_BASE_RATE")
                row = next((r for r in lists.get("BASE_RATE", [])
                            if r["VALUE_CD"] == base), None)
                base_lbl = (row or {}).get("LABEL") or base
                values["ALL_IN_RATE_TXT"] = (f"{base_lbl} + {allin_s} bps"
                                             if base_lbl else f"{allin_s} bps")
            else:
                values["ALL_IN_RATE_TXT"] = f"{allin_s} bps (Fixed)"

    # Otomatik hesap sonrası hâlâ boş kalan zorunlular (auto dahil)
    for key in required_missing:
        if not _blank(values.get(key)):
            continue
        meta = matrix["fields"][key]
        msg = (f"{meta['label']} hesaplanamadı (lookup eşleşmesi yok)"
               if meta["input"] == "auto" else f"{meta['label']} zorunlu")
        errors.append({"field": key, "message": msg})

    # ── Çapraz kontroller ───────────────────────────────────────────────
    vd, md = values.get("VALUE_DT"), values.get("MATURITY_DT")
    if vd is not None and md is not None and md < vd:
        errors.append({"field": "MATURITY_DT",
                       "message": "Final Maturity, Value Date'ten önce olamaz"})

    schedule: list[dict] = []
    if values.get("REPAYMENT_SCHEDULE") == "AMORTIZED" \
            and prod_fields.get("PAYMENT_PLAN") in ("R", "O"):
        if not schedule_in:
            errors.append({"field": "PAYMENT_PLAN",
                           "message": "Amortized için ödeme planı zorunlu"})
        for i, row in enumerate(schedule_in, start=1):
            pay = row.get("pay_dt")
            if _blank(pay):
                errors.append({"field": "PAYMENT_PLAN",
                               "message": f"Plan satırı {i}: tarih zorunlu"})
                continue
            pay_dt = _parse_date(pay, "PAYMENT_PLAN", errors)
            principal = row.get("principal_amt")
            if _blank(principal):
                errors.append({"field": "PAYMENT_PLAN",
                               "message": f"Plan satırı {i}: anapara zorunlu"})
                continue
            principal = _parse_number(principal, "PAYMENT_PLAN", errors)
            interest = None if _blank(row.get("interest_amt")) \
                else _parse_number(row.get("interest_amt"), "PAYMENT_PLAN", errors)
            if pay_dt is not None and principal is not None:
                schedule.append({"pay_dt": pay_dt, "principal_amt": principal,
                                 "interest_amt": interest})
    elif schedule_in:
        warnings.append("Ödeme planı yalnız Amortized'da saklanır, yok sayıldı")

    if errors:
        raise ValidationError(errors)
    return {"deal": deal, "fields": values, "schedule": schedule,
            "warnings": warnings}


def _event_insert(qualify, event_id: int, offer_id: str, deal_id: str,
                  seq: int, event_type: str, now: datetime, user_id: str,
                  fields: dict) -> tuple:
    data_binds = ", ".join(f":{c.lower()}" for c in EVENT_DATA_COLS)
    params = {"event_id": event_id, "offer_id": offer_id, "deal_id": deal_id,
              "seq": seq, "etype": event_type, "now": now, "user_id": user_id}
    params.update({c.lower(): fields.get(c) for c in EVENT_DATA_COLS})
    return (
        f"INSERT INTO {qualify('FI_OFFER_EVENTS')} "
        f"(EVENT_ID, OFFER_ID, DEAL_ID, EVENT_SEQ, EVENT_TYPE, EVENT_TS, "
        f"EVENT_USER, APPROVAL_STATUS, {', '.join(EVENT_DATA_COLS)}) "
        f"VALUES (:event_id, :offer_id, :deal_id, :seq, :etype, :now, "
        f":user_id, 'PENDING', {data_binds})",
        params,
    )


def _schedule_inserts(qualify, event_id: int, offer_id: str,
                      schedule: list[dict]) -> list[tuple]:
    return [(
        f"INSERT INTO {qualify('FI_OFFER_SCHEDULE')} "
        "(EVENT_ID, OFFER_ID, SORT_NO, PAY_DT, PRINCIPAL_AMT, INTEREST_AMT) "
        "VALUES (:event_id, :offer_id, :sort_no, :pay_dt, :principal, :interest)",
        {"event_id": event_id, "offer_id": offer_id, "sort_no": i,
         "pay_dt": row["pay_dt"], "principal": row["principal_amt"],
         "interest": row["interest_amt"]},
    ) for i, row in enumerate(schedule, start=1)]


def build_entry_statements(clean: dict, user_id: str, qualify,
                           next_event_id: int, now: datetime | None = None,
                           deal_exists: bool = False) -> tuple[list[tuple], dict]:
    """Doğrulanmış giriş için INSERT ifadeleri (tek transaction'lık liste).

    ``qualify``: tablo adını şemayla niteleyen fonksiyon (db.qualified).
    ``deal_exists``: True → mevcut deal'a yeni teklif (deal INSERT atlanır).
    Dönen: (statements, {"deal_id", "offer_id", "event_id"}).
    """
    now = now or datetime.now()
    deal = clean["deal"]
    deal_id = deal["deal_id"] or _new_id("FID")
    offer_id = _new_id("FIO")
    statements: list[tuple] = []

    if not deal_exists:
        statements.append((
            f"INSERT INTO {qualify('FI_DEALS')} "
            "(DEAL_ID, BORROWER_BANK, PRODUCT_TYPE, DEAL_LABEL, CREATED_TS, CREATED_BY) "
            "VALUES (:deal_id, :borrower, :product, :label, :now, :user_id)",
            {"deal_id": deal_id, "borrower": deal["borrower_bank"],
             "product": deal["product_type"], "label": deal["deal_label"],
             "now": now, "user_id": user_id},
        ))
    statements.append((
        f"INSERT INTO {qualify('FI_OFFERS')} "
        "(OFFER_ID, DEAL_ID, CREATED_TS, CREATED_BY) "
        "VALUES (:offer_id, :deal_id, :now, :user_id)",
        {"offer_id": offer_id, "deal_id": deal_id, "now": now,
         "user_id": user_id},
    ))
    statements.append(_event_insert(
        qualify, next_event_id, offer_id, deal_id, 1, "ENTRY", now, user_id,
        {c: clean["fields"].get(c) for c in EVENT_DATA_COLS}))
    statements.extend(_schedule_inserts(qualify, next_event_id, offer_id,
                                        clean["schedule"]))

    return statements, {"deal_id": deal_id, "offer_id": offer_id,
                        "event_id": next_event_id}


def classify_edit(prev_fields: dict, new_fields: dict) -> str:
    """Önceki event snapshot'ına göre event tipi: yalnız DEAL_STATUS
    değiştiyse STATUS_CHANGE, başka alan da değiştiyse EDIT."""
    changed = [c for c in EVENT_DATA_COLS
               if _norm_cmp(prev_fields.get(c)) != _norm_cmp(new_fields.get(c))]
    if changed and set(changed) <= {"DEAL_STATUS"}:
        return "STATUS_CHANGE"
    return "EDIT"


def _norm_cmp(v: Any):
    """Karşılaştırma normalizasyonu: DB'den dönen tarih/sayı tipleri ile
    yeni parse edilmiş değerler aynı düzleme iner."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):
        return round(float(v), 6)
    s = str(v).strip()
    return s or None


def build_edit_statements(clean: dict, offer_id: str, deal_id: str,
                          user_id: str, qualify, next_event_id: int,
                          next_seq: int, event_type: str,
                          now: datetime | None = None) -> list[tuple]:
    """Mevcut teklife yeni snapshot event'i (EDIT / STATUS_CHANGE) —
    PENDING doğar, onaylanana dek current'ı değiştirmez."""
    now = now or datetime.now()
    statements = [_event_insert(
        qualify, next_event_id, offer_id, deal_id, next_seq, event_type,
        now, user_id, {c: clean["fields"].get(c) for c in EVENT_DATA_COLS})]
    statements.extend(_schedule_inserts(qualify, next_event_id, offer_id,
                                        clean["schedule"]))
    return statements


def build_delete_statements(prev_fields: dict, offer_id: str, deal_id: str,
                            user_id: str, qualify, next_event_id: int,
                            next_seq: int,
                            now: datetime | None = None) -> list[tuple]:
    """Silme talebi — append-only bozulmaz: son snapshot'ın kopyası
    EVENT_TYPE='DELETE' ile PENDING event olarak yazılır; onaylanınca teklif
    current ilişkisinden düşer (CURRENT_SELECT NOT EXISTS filtresi), geçmiş
    events tablosunda durur. Red edilirse hiçbir şey değişmez."""
    now = now or datetime.now()
    return [_event_insert(
        qualify, next_event_id, offer_id, deal_id, next_seq, "DELETE",
        now, user_id, {c: prev_fields.get(c) for c in EVENT_DATA_COLS})]


def build_approval_statement(event_id: int, action: str, user_id: str,
                             qualify, reason: str | None = None,
                             now: datetime | None = None) -> tuple:
    """Onay/red — event satırındaki TEK izinli mutasyon (append-only istisnası).
    WHERE PENDING koşulu ikinci kez onaylamayı sessizce etkisiz kılar."""
    if action not in ("APPROVED", "REJECTED"):
        raise ValueError(f"Geçersiz onay aksiyonu: {action!r}")
    now = now or datetime.now()
    return (
        f"UPDATE {qualify('FI_OFFER_EVENTS')} SET APPROVAL_STATUS = :status, "
        "APPROVED_BY = :user_id, APPROVED_TS = :now, REJECT_REASON = :reason "
        "WHERE EVENT_ID = :event_id AND APPROVAL_STATUS = 'PENDING'",
        {"status": action, "user_id": user_id, "now": now,
         "reason": (reason or "").strip() or None, "event_id": event_id},
    )

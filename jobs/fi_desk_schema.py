# -*- coding: utf-8 -*-
"""fi_desk_schema.py — FI masası veri giriş modülünün Oracle şemasını kurar.

OFİSTE tek koşu (Spyder → Run/F5; argparse YOK, davranış aşağıdaki KONFİG
sabitlerinden). Ne yapar:

  1. TABLOLAR — yoksa CREATE eder (varsa dokunmaz; DROP_EXISTING=True ise
     önce DROP). Tablolar:

       FI_DEALS           üst kimlik: borçlanma ihtiyacı (borrower + ürün).
                          Aynı deal'a birden fazla lender'dan teklif bağlanır.
       FI_OFFERS          teklif thread'i: deal × lender süreci. Kimlik çapası;
                          içerik event'lerdedir.
       FI_OFFER_EVENTS    APPEND-ONLY tam-snapshot satırları. Her giriş / edit /
                          statü değişikliği yeni satırdır (EVENT_SEQ artar);
                          UPDATE/DELETE YAPILMAZ. Onay akışı: her event
                          PENDING doğar, onaycı APPROVED/REJECTED yapar
                          (yalnız APPROVAL_* kolonları güncellenir).
       FI_OFFER_SCHEDULE  Amortized işlemlerde ödeme planı (event'e bağlı
                          snapshot: PAY_DT, anapara, faiz).
       FI_LU_BANK         banka → ülke/region/group company eşlemesi.
       FI_LU_LIST         genel kod listeleri (LIST_NAME ile ayrışır:
                          CURRENCY, COUNTRY, REPAYMENT, COVERAGE_PROVIDER,
                          BASE_RATE, BUSINESS_SEGMENT, ESG_TYPE,
                          ESG_ELIGIBILITY, IMPORTER, EXPORTER).
       FI_LU_USER         rol tanımı: sicil → ENTRY (veri girici) /
                          APPROVER (onaycı).

     LOOKUP TABLOLARI UYGULAMADAN EDİTLENMEZ — tek yazar bu script (ve
     ileride mapping excellerini yükleyecek kardeş script'ler). Uygulama
     yalnız okur.

  2. VIEW — V_FI_OFFER_CURRENT: teklif başına EN SON ONAYLI event + deal
     başlığı + türetilen kolonlar (REPORTING_STATUS, TENOR_DAYS).
     Reporting status SORGU ANINDA hesaplanır; WON bir işlem value date'i
     geçince kendiliğinden REALIZED görünür (cron gerekmez).

  3. GRANT — GRANT_TO kullanıcılarına tablolar+view üzerinde SELECT
     (uygulama servis kullanıcısı farklıysa buraya ekle).

  4. SEED — SEED_LOOKUPS: lookup tablolarını örnek değerlerle DOLDURUR
     (DELETE + INSERT; gerçek mapping excelleri gelene kadar YER TUTUCU).
     SEED_TEST_DATA: işlem tabloları TAMAMEN BOŞSA örnek deal/teklif/event
     seti ekler (form ve liste ekranı geliştirmesi için).
     RESEED_TEST_DATA=True: işlem tablolarını BOŞALTIP yeniden ekler —
     yalnız test ortamında kullan.

Kolon adlarında Oracle rezerve kelimesi yok (MODE/TYPE/DATE kullanılmadı).
DDL sözleşmesi: tip parantezlerinde virgül YOK (NUMBER(18,2) yazma) —
kolon listesi parantez-virgül ayrıştırmasıyla çıkarılır (deposits_pipeline
ile aynı sözleşme) ve tests/test_fi_desk_matrix.py bu dosyayı metin olarak
ayrıştırıp field_matrix.json ile senkron doğrular.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ─────────────────────────────────────────────────────────────────────────
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ─────────────────────────────────────────────────────────────────────────
SCHEMA: str | None = None          # None → bağlantı kullanıcısının şeması
GRANT_TO = "A63837"                # SELECT verilecek kullanıcı(lar); "" → yok
DROP_EXISTING = False              # True → tabloları DROP edip yeniden kurar
SEED_LOOKUPS = True                # lookup tablolarını örnek setle doldur
SEED_TEST_DATA = True              # işlem tabloları boşsa örnek işlemler ekle
RESEED_TEST_DATA = False           # True → işlem tablolarını BOŞALTIP yeniden
                                   # ekler (yalnız test ortamı!)

# ─────────────────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────────────────
DDL = {
    "FI_DEALS": """CREATE TABLE {t} (
        DEAL_ID VARCHAR2(24), BORROWER_BANK VARCHAR2(128),
        PRODUCT_TYPE VARCHAR2(32), DEAL_LABEL VARCHAR2(256),
        CREATED_TS DATE, CREATED_BY VARCHAR2(16))""",
    "FI_OFFERS": """CREATE TABLE {t} (
        OFFER_ID VARCHAR2(24), DEAL_ID VARCHAR2(24),
        CREATED_TS DATE, CREATED_BY VARCHAR2(16))""",
    "FI_OFFER_EVENTS": """CREATE TABLE {t} (
        EVENT_ID NUMBER, OFFER_ID VARCHAR2(24), DEAL_ID VARCHAR2(24),
        EVENT_SEQ NUMBER, EVENT_TYPE VARCHAR2(16),
        EVENT_TS DATE, EVENT_USER VARCHAR2(16),
        APPROVAL_STATUS VARCHAR2(16), APPROVED_BY VARCHAR2(16),
        APPROVED_TS DATE, REJECT_REASON VARCHAR2(512),
        DEAL_STATUS VARCHAR2(16),
        LENDER_BANK VARCHAR2(128), LENDER_COUNTRY VARCHAR2(64),
        LENDER_REGION VARCHAR2(64), GROUP_COMPANY VARCHAR2(128),
        GROUP_COMPANY_COUNTRY VARCHAR2(64), GROUP_COMPANY_REGION VARCHAR2(64),
        OFFER_DT DATE, CURRENCY VARCHAR2(8),
        FUNDING_AMT NUMBER, USD_EQV NUMBER,
        VALUE_DT DATE, MATURITY_DT DATE,
        REPAYMENT_SCHEDULE VARCHAR2(16),
        COVERAGE_FLG VARCHAR2(4), COVERAGE_PROVIDER VARCHAR2(64),
        RATE_TYPE VARCHAR2(16), FIXED_RATE_BPS NUMBER,
        FLOAT_BASE_RATE VARCHAR2(32), FLOAT_SPREAD_BPS NUMBER,
        COVERAGE_RATE_BPS NUMBER, FEE NUMBER, ADDITIONAL_FEE_COST NUMBER,
        ALL_IN_RATE_BPS NUMBER, ALL_IN_FIXED_USD_RATE NUMBER,
        TRADE_TXN_AMT NUMBER, SHIPMENT_DT DATE, TRADE_PAYMENT_DT DATE,
        IMPORTER VARCHAR2(128), EXPORTER VARCHAR2(128),
        BUSINESS_SEGMENT VARCHAR2(32), REFERENCE_NO VARCHAR2(64),
        GOODS_DESC VARCHAR2(512),
        SUSTAINABILITY_FLG VARCHAR2(4), ESG_TYPE VARCHAR2(16),
        ESG_ELIGIBILITY VARCHAR2(32), NOTES VARCHAR2(2000))""",
    "FI_OFFER_SCHEDULE": """CREATE TABLE {t} (
        EVENT_ID NUMBER, OFFER_ID VARCHAR2(24), SORT_NO NUMBER,
        PAY_DT DATE, PRINCIPAL_AMT NUMBER, INTEREST_AMT NUMBER)""",
    "FI_LU_BANK": """CREATE TABLE {t} (
        BANK_NM VARCHAR2(128), COUNTRY VARCHAR2(64), REGION VARCHAR2(64),
        GROUP_COMPANY VARCHAR2(128), GROUP_COMPANY_COUNTRY VARCHAR2(64),
        GROUP_COMPANY_REGION VARCHAR2(64),
        IS_SELF NUMBER(1), ACTIVE_FLG NUMBER(1), LOADED_AT DATE)""",
    "FI_LU_LIST": """CREATE TABLE {t} (
        LIST_NAME VARCHAR2(32), VALUE_CD VARCHAR2(64), LABEL VARCHAR2(128),
        PARENT_CD VARCHAR2(64), ATTR1 VARCHAR2(64), SORT_NO NUMBER,
        ACTIVE_FLG NUMBER(1), LOADED_AT DATE)""",
    "FI_LU_USER": """CREATE TABLE {t} (
        SICIL VARCHAR2(16), USER_ROLE VARCHAR2(16),
        ACTIVE_FLG NUMBER(1), LOADED_AT DATE)""",
}

# Bütünlük uygulama tarafında; burada yalnız tekillik indeksleri (idempotent).
INDEXES = [
    ("FI_DEALS_PK", "CREATE UNIQUE INDEX {s}.FI_DEALS_PK ON {s}.FI_DEALS (DEAL_ID)"),
    ("FI_OFFERS_PK", "CREATE UNIQUE INDEX {s}.FI_OFFERS_PK ON {s}.FI_OFFERS (OFFER_ID)"),
    ("FI_EVENTS_PK", "CREATE UNIQUE INDEX {s}.FI_EVENTS_PK ON {s}.FI_OFFER_EVENTS (EVENT_ID)"),
    ("FI_EVENTS_SEQ_UX",
     "CREATE UNIQUE INDEX {s}.FI_EVENTS_SEQ_UX ON {s}.FI_OFFER_EVENTS (OFFER_ID, EVENT_SEQ)"),
    ("FI_LU_LIST_UX",
     "CREATE UNIQUE INDEX {s}.FI_LU_LIST_UX ON {s}.FI_LU_LIST (LIST_NAME, VALUE_CD)"),
    ("FI_LU_BANK_UX", "CREATE UNIQUE INDEX {s}.FI_LU_BANK_UX ON {s}.FI_LU_BANK (BANK_NM)"),
]

# Teklif başına en son ONAYLI event + deal başlığı + türetilen kolonlar.
# REPORTING_STATUS kuralı (soru-cevap turu, 2026-07-30):
#   WON  → value date geçtiyse REALIZED, geçmediyse PENDING
#   LOST → UNREALIZED   BIDDING → BIDDING
#   Deal Status taşımayan ürünler (Fiduciary, Eurobond, Sub Bonds) →
#   value date'e göre REALIZED/PENDING.
#
# CURRENT_SELECT tek kaynaktır: view yaratılabilirse gövdesi olur; CREATE
# VIEW yetkisi yoksa (ORA-01031 — EDW kişisel şemalarında görüldü) uygulama
# (fi_desk/db.current_relation) ve dashboard blokları AYNI SQL'i inline
# alt-sorgu olarak kullanır — davranış iki durumda da birebir. {s} ŞEMA
# ÖNEKİDİR ve nokta İÇERİR ("A63837PY." gibi; dev DuckDB'de boş string).
# Taşınabilirlik: tarih farkı CAST(... AS DATE) ile alınır (Oracle'da no-op,
# DuckDB'de TIMESTAMP→DATE; iki motorda da sonuç gün sayısıdır) ve
# TRUNC(SYSDATE) oracle_duck çevirmeninin kapsamındadır.
CURRENT_SELECT = """SELECT d.BORROWER_BANK, d.PRODUCT_TYPE, d.DEAL_LABEL,
       e.*,
       CASE
         WHEN e.DEAL_STATUS = 'LOST' THEN 'UNREALIZED'
         WHEN e.DEAL_STATUS = 'BIDDING' THEN 'BIDDING'
         WHEN e.VALUE_DT IS NULL THEN 'PENDING'
         WHEN e.VALUE_DT <= TRUNC(SYSDATE) THEN 'REALIZED'
         ELSE 'PENDING'
       END AS REPORTING_STATUS,
       CAST(e.MATURITY_DT AS DATE) - CAST(e.VALUE_DT AS DATE) AS TENOR_DAYS
FROM (
    SELECT ev.*
    FROM {s}FI_OFFER_EVENTS ev
    WHERE ev.APPROVAL_STATUS = 'APPROVED'
      AND ev.EVENT_SEQ = (
          SELECT MAX(e2.EVENT_SEQ) FROM {s}FI_OFFER_EVENTS e2
          WHERE e2.OFFER_ID = ev.OFFER_ID
            AND e2.APPROVAL_STATUS = 'APPROVED')
) e
JOIN {s}FI_DEALS d ON d.DEAL_ID = e.DEAL_ID"""

VIEW_SQL = ("CREATE OR REPLACE VIEW {s}V_FI_OFFER_CURRENT AS\n"
            + CURRENT_SELECT)

# ─────────────────────────────────────────────────────────────────────────
# SEED — lookup örnek seti (YER TUTUCU: gerçek mapping excelleri gelince
# kardeş yükleme script'i bu tabloları tam setle yeniler)
# ─────────────────────────────────────────────────────────────────────────
SEED_LISTS: list[tuple[str, str, str, str | None, str | None]] = [
    # (LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1)
    ("CURRENCY", "USD", "USD", None, None),
    ("CURRENCY", "EUR", "EUR", None, None),
    ("CURRENCY", "TRY", "TRY", None, None),
    ("CURRENCY", "GBP", "GBP", None, None),
    ("CURRENCY", "CNY", "CNY", None, None),
    ("REPAYMENT", "BULLET", "Bullet", None, None),
    ("REPAYMENT", "AMORTIZED", "Amortized", None, None),
    ("COVERAGE_PROVIDER", "QNB", "QNB", None, None),
    ("COVERAGE_PROVIDER", "EBRD", "EBRD", None, None),
    ("COVERAGE_PROVIDER", "IFC", "IFC", None, None),
    ("COVERAGE_PROVIDER", "ADB", "ADB", None, None),
    ("COVERAGE_PROVIDER", "ECA", "ECA", None, None),
    ("COVERAGE_PROVIDER", "OTHER", "Other", None, None),
    ("BASE_RATE", "SOFR_3M", "3M SOFR", None, None),
    ("BASE_RATE", "SOFR_6M", "6M SOFR", None, None),
    ("BASE_RATE", "SOFR_12M", "12M SOFR", None, None),
    ("BASE_RATE", "EURIBOR_3M", "3M EURIBOR", None, None),
    ("BASE_RATE", "EURIBOR_6M", "6M EURIBOR", None, None),
    ("BUSINESS_SEGMENT", "COMMERCIAL", "Commercial", None, None),
    ("BUSINESS_SEGMENT", "CORPORATE", "Corporate", None, None),
    ("BUSINESS_SEGMENT", "BIG_COMMERCIAL", "Big Commercial", None, None),
    ("BUSINESS_SEGMENT", "SME", "SME", None, None),
    ("BUSINESS_SEGMENT", "PROJECT_FINANCE", "Project Finance", None, None),
    ("BUSINESS_SEGMENT", "RETAIL", "Retail", None, None),
    ("ESG_TYPE", "UOP", "UoP", None, None),
    ("ESG_TYPE", "SLL", "SLL", None, None),
    ("ESG_TYPE", "TRANSITION", "Transition", None, None),
    ("ESG_ELIGIBILITY", "GREEN", "Green", "UOP", None),
    ("ESG_ELIGIBILITY", "SOCIAL", "Social", "UOP", None),
    ("ESG_ELIGIBILITY", "BLUE", "Blue", "UOP", None),
    ("ESG_ELIGIBILITY", "UOP_OTHER", "Other", "UOP", None),
    ("ESG_ELIGIBILITY", "SLL", "SLL", "SLL", None),
    ("ESG_ELIGIBILITY", "TRANSITION", "Transition", "TRANSITION", None),
    # COUNTRY: ATTR1 = region (Lender Region otomatik buradan gelir)
    ("COUNTRY", "TURKIYE", "Türkiye", None, "Türkiye"),
    ("COUNTRY", "GERMANY", "Germany", None, "Europe"),
    ("COUNTRY", "UK", "United Kingdom", None, "Europe"),
    ("COUNTRY", "FRANCE", "France", None, "Europe"),
    ("COUNTRY", "USA", "United States", None, "North America"),
    ("COUNTRY", "UAE", "United Arab Emirates", None, "Middle East"),
    ("COUNTRY", "QATAR", "Qatar", None, "Middle East"),
    ("COUNTRY", "CHINA", "China", None, "Asia"),
    ("COUNTRY", "JAPAN", "Japan", None, "Asia"),
    # Importer/Exporter: "yeni eklemeler dashboard'da" — dış süreç ekler
    ("IMPORTER", "IMP_SAMPLE_1", "Örnek İthalatçı A.Ş.", None, None),
    ("EXPORTER", "EXP_SAMPLE_1", "Örnek İhracatçı GmbH", None, None),
]

SEED_BANKS: list[tuple[str, str, str, str | None, str | None, str | None, int]] = [
    # (BANK_NM, COUNTRY, REGION, GROUP_COMPANY, GRP_COUNTRY, GRP_REGION, IS_SELF)
    ("QNB", "TURKIYE", "Türkiye", "QNB Group", "QATAR", "Middle East", 1),
    ("Akbank", "TURKIYE", "Türkiye", None, None, None, 0),
    ("Deutsche Bank", "GERMANY", "Europe", "Deutsche Bank AG", "GERMANY", "Europe", 0),
    ("HSBC", "UK", "Europe", "HSBC Holdings", "UK", "Europe", 0),
    ("Standard Chartered", "UK", "Europe", "Standard Chartered plc", "UK", "Europe", 0),
    ("Emirates NBD", "UAE", "Middle East", "Emirates NBD Group", "UAE", "Middle East", 0),
    ("ICBC", "CHINA", "Asia", "ICBC Group", "CHINA", "Asia", 0),
    ("Citibank", "USA", "North America", "Citigroup", "USA", "North America", 0),
]

SEED_USERS: list[tuple[str, str]] = [
    # (SICIL, USER_ROLE) — ENTRY: veri girici, APPROVER: onaycı.
    # YER TUTUCU: masa listesi gelince dış süreçle yenilenir.
    ("A16438", "ENTRY"),
    ("A16438", "APPROVER"),
]


# ─────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────
def _as_list(value: str | Sequence[str] | None) -> list[str]:
    """String-güvenli liste çevirici: "ABC" → ["ABC"] (list("ABC") tuzağı yok)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    return [str(v).strip() for v in value if str(v).strip()]


def _table_exists(con, schema: str, table: str) -> bool:
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM all_tables WHERE owner = :o AND table_name = :t",
            {"o": schema.upper(), "t": table.upper()},
        )
        return cur.fetchone()[0] > 0
    finally:
        cur.close()


def _exec(con, sql: str, ok_codes: tuple[str, ...] = ()) -> bool:
    """DDL çalıştır; ok_codes içindeki ORA hataları (zaten var/yok) yutulur."""
    cur = con.cursor()
    try:
        cur.execute(sql)
        return True
    except Exception as exc:
        if any(code in str(exc) for code in ok_codes):
            return False
        raise
    finally:
        cur.close()


def _row_count(con, qualified: str) -> int:
    cur = con.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {qualified}")
        return cur.fetchone()[0]
    finally:
        cur.close()


# ─────────────────────────────────────────────────────────────────────────
# Adımlar
# ─────────────────────────────────────────────────────────────────────────
def create_tables(con, schema: str) -> None:
    for name, ddl in DDL.items():
        qualified = f"{schema}.{name}"
        if DROP_EXISTING and _table_exists(con, schema, name):
            print(f"   DROP TABLE {qualified}")
            _exec(con, f"DROP TABLE {qualified}")
        if _table_exists(con, schema, name):
            print(f"   = {qualified} zaten var, dokunulmadı")
            continue
        print(f"   CREATE TABLE {qualified}")
        _exec(con, ddl.format(t=qualified))
    for _, idx_sql in INDEXES:
        # ORA-00955: ad zaten var (idempotent), ORA-01408: kolon listesi indeksli
        _exec(con, idx_sql.format(s=schema), ok_codes=("ORA-00955", "ORA-01408"))
    # View OPSİYONELDİR: EDW kişisel şemalarında CREATE VIEW yetkisi
    # olmayabiliyor (ORA-01031 — 2026-07-30'da yaşandı). Yetki yoksa uyarıp
    # devam ederiz; uygulama ve dashboard aynı CURRENT_SELECT'i inline
    # kullanır, davranış değişmez. Yetki alınırsa script yeniden koşulur.
    if _exec(con, VIEW_SQL.format(s=f"{schema}."), ok_codes=("ORA-01031",)):
        print("   ✓ V_FI_OFFER_CURRENT (CREATE OR REPLACE)")
    else:
        print("   ! V_FI_OFFER_CURRENT ATLANDI — CREATE VIEW yetkisi yok "
              "(ORA-01031). Sorun değil: uygulama ve dashboard current "
              "sorgusunu inline koşar. İstersen DBA'dan CREATE VIEW yetkisi "
              "alıp script'i yeniden koşabilirsin (ad-hoc SQL için kolaylık).")
    con.commit()


def grant_all(con, schema: str, grantees: list[str]) -> None:
    if not grantees:
        return
    objects = list(DDL) + ["V_FI_OFFER_CURRENT"]
    cur = con.cursor()
    try:
        for obj in objects:
            for grantee in grantees:
                try:
                    cur.execute(f"GRANT SELECT ON {schema}.{obj} TO {grantee}")
                    print(f"   ✓ GRANT SELECT ON {schema}.{obj} TO {grantee}")
                except Exception as exc:
                    print(f"   ✗ grant başarısız ({schema}.{obj} → {grantee}): {exc}")
        con.commit()
    finally:
        cur.close()


def seed_lookups(con, schema: str) -> None:
    now = datetime.now()
    cur = con.cursor()
    try:
        cur.execute(f"DELETE FROM {schema}.FI_LU_LIST")
        cur.executemany(
            f"INSERT INTO {schema}.FI_LU_LIST "
            "(LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1, SORT_NO, ACTIVE_FLG, LOADED_AT) "
            "VALUES (:1, :2, :3, :4, :5, :6, 1, :7)",
            [(ln, cd, lbl, par, at1, i, now)
             for i, (ln, cd, lbl, par, at1) in enumerate(SEED_LISTS)],
        )
        cur.execute(f"DELETE FROM {schema}.FI_LU_BANK")
        cur.executemany(
            f"INSERT INTO {schema}.FI_LU_BANK "
            "(BANK_NM, COUNTRY, REGION, GROUP_COMPANY, GROUP_COMPANY_COUNTRY, "
            "GROUP_COMPANY_REGION, IS_SELF, ACTIVE_FLG, LOADED_AT) "
            "VALUES (:1, :2, :3, :4, :5, :6, :7, 1, :8)",
            [(b, c, r, g, gc, gr, s, now) for b, c, r, g, gc, gr, s in SEED_BANKS],
        )
        cur.execute(f"DELETE FROM {schema}.FI_LU_USER")
        cur.executemany(
            f"INSERT INTO {schema}.FI_LU_USER (SICIL, USER_ROLE, ACTIVE_FLG, LOADED_AT) "
            "VALUES (:1, :2, 1, :3)",
            [(s, r, now) for s, r in SEED_USERS],
        )
        con.commit()
        print(f"   ✓ lookup seed: {len(SEED_LISTS)} liste satırı, "
              f"{len(SEED_BANKS)} banka, {len(SEED_USERS)} kullanıcı-rol")
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()


def _event_defaults() -> dict:
    """FI_OFFER_EVENTS'in tüm veri kolonları None — seed satırları üzerine yazar."""
    cols = ["DEAL_STATUS", "LENDER_BANK", "LENDER_COUNTRY", "LENDER_REGION",
            "GROUP_COMPANY", "GROUP_COMPANY_COUNTRY", "GROUP_COMPANY_REGION",
            "OFFER_DT", "CURRENCY", "FUNDING_AMT", "USD_EQV", "VALUE_DT",
            "MATURITY_DT", "REPAYMENT_SCHEDULE", "COVERAGE_FLG",
            "COVERAGE_PROVIDER", "RATE_TYPE", "FIXED_RATE_BPS",
            "FLOAT_BASE_RATE", "FLOAT_SPREAD_BPS", "COVERAGE_RATE_BPS", "FEE",
            "ADDITIONAL_FEE_COST", "ALL_IN_RATE_BPS", "ALL_IN_FIXED_USD_RATE",
            "TRADE_TXN_AMT", "SHIPMENT_DT", "TRADE_PAYMENT_DT", "IMPORTER",
            "EXPORTER", "BUSINESS_SEGMENT", "REFERENCE_NO", "GOODS_DESC",
            "SUSTAINABILITY_FLG", "ESG_TYPE", "ESG_ELIGIBILITY", "NOTES"]
    return {c: None for c in cols}


def seed_test_data(con, schema: str) -> None:
    """Örnek işlem seti — form/liste geliştirmesi için temsili senaryolar:

      FID-TEST-001  Trade Loan, 2 teklif: HSBC (WON, onaylı, 3 event'lik
                    geçmiş) + Deutsche Bank (LOST, onaylı)
      FID-TEST-002  Bilateral, Emirates NBD (BIDDING, ONAY BEKLİYOR)
      FID-TEST-003  Eurobond (statüsüz ürün), Citibank, Amortized + ödeme planı
    """
    qual_ev = f"{schema}.FI_OFFER_EVENTS"
    existing = _row_count(con, qual_ev)
    if existing and not RESEED_TEST_DATA:
        print(f"   = FI_OFFER_EVENTS'te {existing} satır var — test seed atlandı "
              "(yeniden eklemek için RESEED_TEST_DATA=True)")
        return

    cur = con.cursor()
    now = datetime.now()
    d = lambda days: now + timedelta(days=days)  # noqa: E731
    try:
        if RESEED_TEST_DATA:
            for t in ("FI_OFFER_SCHEDULE", "FI_OFFER_EVENTS", "FI_OFFERS", "FI_DEALS"):
                cur.execute(f"DELETE FROM {schema}.{t}")

        deals = [
            ("FID-TEST-001", "QNB", "TRADE_LOAN", "Test — Trade Loan çok teklifli"),
            ("FID-TEST-002", "QNB", "BILATERAL_LOAN", "Test — Bilateral onay bekliyor"),
            ("FID-TEST-003", "QNB", "EUROBOND", "Test — Eurobond amortized"),
        ]
        cur.executemany(
            f"INSERT INTO {schema}.FI_DEALS "
            "(DEAL_ID, BORROWER_BANK, PRODUCT_TYPE, DEAL_LABEL, CREATED_TS, CREATED_BY) "
            "VALUES (:1, :2, :3, :4, :5, 'A16438')",
            [(i, b, p, l, d(-30)) for i, b, p, l in deals],
        )

        offers = [
            ("FIO-TEST-001A", "FID-TEST-001"),
            ("FIO-TEST-001B", "FID-TEST-001"),
            ("FIO-TEST-002A", "FID-TEST-002"),
            ("FIO-TEST-003A", "FID-TEST-003"),
        ]
        cur.executemany(
            f"INSERT INTO {schema}.FI_OFFERS (OFFER_ID, DEAL_ID, CREATED_TS, CREATED_BY) "
            "VALUES (:1, :2, :3, 'A16438')",
            [(o, dl, d(-30)) for o, dl in offers],
        )

        base = _event_defaults()
        loan_hsbc = dict(base, DEAL_STATUS="BIDDING", LENDER_BANK="HSBC",
                         LENDER_COUNTRY="UK", LENDER_REGION="Europe",
                         GROUP_COMPANY="HSBC Holdings", GROUP_COMPANY_COUNTRY="UK",
                         GROUP_COMPANY_REGION="Europe", OFFER_DT=d(-30),
                         CURRENCY="USD", FUNDING_AMT=50_000_000, USD_EQV=50_000_000,
                         VALUE_DT=d(-10), MATURITY_DT=d(355),
                         REPAYMENT_SCHEDULE="BULLET", COVERAGE_FLG="YES",
                         COVERAGE_PROVIDER="ECA", RATE_TYPE="FLOATING",
                         FLOAT_BASE_RATE="SOFR_3M", FLOAT_SPREAD_BPS=185,
                         COVERAGE_RATE_BPS=40, ALL_IN_RATE_BPS=225,
                         TRADE_TXN_AMT=52_000_000, SHIPMENT_DT=d(-20),
                         TRADE_PAYMENT_DT=d(-5), IMPORTER="IMP_SAMPLE_1",
                         EXPORTER="EXP_SAMPLE_1", BUSINESS_SEGMENT="CORPORATE",
                         REFERENCE_NO="TL-2026-0001", GOODS_DESC="Makine ekipmanı",
                         SUSTAINABILITY_FLG="NO")
        loan_db = dict(loan_hsbc, LENDER_BANK="Deutsche Bank",
                       LENDER_COUNTRY="GERMANY", GROUP_COMPANY="Deutsche Bank AG",
                       GROUP_COMPANY_COUNTRY="GERMANY", FLOAT_SPREAD_BPS=210,
                       ALL_IN_RATE_BPS=250)
        bilat = dict(base, DEAL_STATUS="BIDDING", LENDER_BANK="Emirates NBD",
                     LENDER_COUNTRY="UAE", LENDER_REGION="Middle East",
                     GROUP_COMPANY="Emirates NBD Group",
                     GROUP_COMPANY_COUNTRY="UAE", GROUP_COMPANY_REGION="Middle East",
                     OFFER_DT=d(-2), CURRENCY="EUR", FUNDING_AMT=25_000_000,
                     USD_EQV=27_000_000, VALUE_DT=d(20), MATURITY_DT=d(750),
                     REPAYMENT_SCHEDULE="BULLET", COVERAGE_FLG="NO",
                     RATE_TYPE="FIXED", FIXED_RATE_BPS=310, COVERAGE_RATE_BPS=0,
                     ALL_IN_RATE_BPS=310, SUSTAINABILITY_FLG="YES",
                     ESG_TYPE="UOP", ESG_ELIGIBILITY="GREEN")
        bond = dict(base, LENDER_BANK="Citibank", OFFER_DT=d(-90),
                    CURRENCY="USD", FUNDING_AMT=100_000_000, USD_EQV=100_000_000,
                    VALUE_DT=d(-60), MATURITY_DT=d(1765),
                    REPAYMENT_SCHEDULE="AMORTIZED", RATE_TYPE="FIXED",
                    FIXED_RATE_BPS=725, ALL_IN_RATE_BPS=725,
                    ALL_IN_FIXED_USD_RATE=7.45, SUSTAINABILITY_FLG="NO")

        # (EVENT_ID, OFFER_ID, DEAL_ID, SEQ, TYPE, TS, APPROVAL, APPR_BY, APPR_TS, data)
        events = [
            (1, "FIO-TEST-001A", "FID-TEST-001", 1, "ENTRY", d(-30),
             "APPROVED", "A16438", d(-29), loan_hsbc),
            (2, "FIO-TEST-001A", "FID-TEST-001", 2, "EDIT", d(-25),
             "APPROVED", "A16438", d(-24),
             dict(loan_hsbc, FLOAT_SPREAD_BPS=175, ALL_IN_RATE_BPS=215)),
            (3, "FIO-TEST-001A", "FID-TEST-001", 3, "STATUS_CHANGE", d(-12),
             "APPROVED", "A16438", d(-11),
             dict(loan_hsbc, DEAL_STATUS="WON", FLOAT_SPREAD_BPS=175,
                  ALL_IN_RATE_BPS=215)),
            (4, "FIO-TEST-001B", "FID-TEST-001", 1, "ENTRY", d(-30),
             "APPROVED", "A16438", d(-29), loan_db),
            (5, "FIO-TEST-001B", "FID-TEST-001", 2, "STATUS_CHANGE", d(-12),
             "APPROVED", "A16438", d(-11), dict(loan_db, DEAL_STATUS="LOST")),
            (6, "FIO-TEST-002A", "FID-TEST-002", 1, "ENTRY", d(-2),
             "PENDING", None, None, bilat),
            (7, "FIO-TEST-003A", "FID-TEST-003", 1, "ENTRY", d(-90),
             "APPROVED", "A16438", d(-89), bond),
        ]
        data_cols = list(_event_defaults())
        meta_cols = ["EVENT_ID", "OFFER_ID", "DEAL_ID", "EVENT_SEQ", "EVENT_TYPE",
                     "EVENT_TS", "EVENT_USER", "APPROVAL_STATUS", "APPROVED_BY",
                     "APPROVED_TS"]
        all_cols = meta_cols + data_cols
        binds = ", ".join(f":{i + 1}" for i in range(len(all_cols)))
        rows = []
        for eid, oid, did, seq, etype, ets, appr, by, appr_ts, data in events:
            meta = [eid, oid, did, seq, etype, ets, "A16438", appr, by, appr_ts]
            rows.append(tuple(meta + [data[c] for c in data_cols]))
        cur.executemany(
            f"INSERT INTO {qual_ev} ({', '.join(all_cols)}) VALUES ({binds})", rows)

        # Eurobond ödeme planı (event 7'ye bağlı): 5 yıllık yıllık itfa
        sched = [(7, "FIO-TEST-003A", i + 1, d(-60 + 365 * (i + 1)),
                  20_000_000, 7_250_000 - i * 1_450_000) for i in range(5)]
        cur.executemany(
            f"INSERT INTO {schema}.FI_OFFER_SCHEDULE "
            "(EVENT_ID, OFFER_ID, SORT_NO, PAY_DT, PRINCIPAL_AMT, INTEREST_AMT) "
            "VALUES (:1, :2, :3, :4, :5, :6)", sched)

        con.commit()
        print(f"   ✓ test seed: {len(deals)} deal, {len(offers)} teklif, "
              f"{len(events)} event, {len(sched)} plan satırı")
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()


def main() -> int:
    from DataClient import DataClient
    dc = DataClient()
    con = dc.get_connection()
    try:
        schema = (SCHEMA or con.username).upper()
        print(f"Bağlantı OK — hedef şema: {schema}")

        print("── Tablolar / indeksler / view")
        create_tables(con, schema)

        grantees = _as_list(GRANT_TO)
        if grantees:
            print("── Grant")
            grant_all(con, schema, grantees)

        if SEED_LOOKUPS:
            print("── Lookup seed (YER TUTUCU — mapping excelleri gelince yenilenir)")
            seed_lookups(con, schema)

        if SEED_TEST_DATA:
            print("── Test verisi")
            seed_test_data(con, schema)

        print("Bitti.")
        return 0
    finally:
        dc.drop_connection(con)


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""ldap_admins.py — TRESUARY_LDAP'a IS_ADMIN kolonu ekler ve adminleri işaretler.

OFİSTE koşulur (Spyder → Run/F5; argparse YOK, davranış KONFİG
sabitlerinden). Masa admin ekranları (ilk örnek: FI masası /fi-desk/admin
lookup yönetimi) bu bayrağa bakar: ``User.is_admin`` login/load_user'da
LDAP satırından okunur (kolon yokken herkes 0 — ekranlar kapalı kalır).

Ne yapar:
  1. ``ALTER TABLE {LDAP_TABLE} ADD (IS_ADMIN NUMBER(1))`` — kolon zaten
     varsa (ORA-01430) sessizce geçer.
  2. TÜM satırlarda IS_ADMIN=0'a çeker, sonra KONFİG'teki ``ADMINS``
     sicillerini 1 yapar (tam yenileme — liste neyse admin odur).

Not: ``load_user`` 5 dk kullanıcı cache'i tutar — bayrak değişikliği en geç
5 dk içinde (ya da yeniden login'de) etkiye geçer.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ─────────────────────────────────────────────────────────────────────────
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ─────────────────────────────────────────────────────────────────────────
#: Uygulamanın login sorgularının okuduğu LDAP tablosu
#: (queries/LDAPBySicil.sql ile aynı adres olmalı).
LDAP_TABLE = "A63837.TRESUARY_LDAP"

#: Admin bayrağı verilecek siciller (TAM LİSTE — burada olmayan herkes 0'a
#: çekilir; admin almak = listeden çıkarıp yeniden koşmak).
ADMINS: tuple[str, ...] = (
    "A63837",
    "A16438",
)
# ─────────────────────────────────────────────────────────────────────────


def _exec(cur, sql: str, params: dict | None = None,
          ok_codes: tuple[str, ...] = ()) -> bool:
    try:
        cur.execute(sql, params or {})
        return True
    except Exception as exc:
        if any(code in str(exc) for code in ok_codes):
            return False
        raise


def main() -> int:
    from DataClient import DataClient

    admins = [str(s).strip().upper() for s in ADMINS if str(s).strip()]
    if not admins:
        raise SystemExit("ADMINS boş — en az bir sicil gerekli "
                         "(tam yenileme herkesi 0'a çekerdi).")

    dc = DataClient()
    con = dc.get_connection()
    try:
        cur = con.cursor()
        try:
            if _exec(cur, f"ALTER TABLE {LDAP_TABLE} ADD (IS_ADMIN NUMBER(1))",
                     ok_codes=("ORA-01430",)):
                print(f"✓ {LDAP_TABLE}.IS_ADMIN kolonu eklendi")
            else:
                print(f"= {LDAP_TABLE}.IS_ADMIN zaten var")

            cur.execute(f"UPDATE {LDAP_TABLE} SET IS_ADMIN = 0")
            print(f"✓ tüm satırlar IS_ADMIN=0 ({cur.rowcount} satır)")

            binds = ", ".join(f":s{i}" for i in range(len(admins)))
            params = {f"s{i}": s for i, s in enumerate(admins)}
            cur.execute(
                f"UPDATE {LDAP_TABLE} SET IS_ADMIN = 1 "
                f"WHERE UPPER(SICIL) IN ({binds})", params)
            print(f"✓ admin işaretlendi: {cur.rowcount} satır "
                  f"(istenen {len(admins)}: {', '.join(admins)})")
            if cur.rowcount != len(admins):
                print("! UYARI: bazı siciller LDAP tablosunda bulunamadı — "
                      "listeyi kontrol et.")
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            cur.close()
        print("Bitti — bayrak en geç 5 dk'lık kullanıcı cache'i dolunca "
              "(ya da yeniden login'de) etkiye geçer.")
        return 0
    finally:
        dc.drop_connection(con)


if __name__ == "__main__":
    raise SystemExit(main())

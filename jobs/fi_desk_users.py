# -*- coding: utf-8 -*-
"""fi_desk_users.py — FI masası kullanıcı rollerini (FI_LU_USER) yükler.

OFİSTE koşulur (Spyder → Run/F5; argparse YOK, davranış KONFİG
sabitlerinden). FI_LU_USER uygulamadan EDİTLENMEZ (lookup kararı,
docs/FI_DESK_SPEC.md §1.4) — tek yazar bu script'tir.

Roller:
  ENTRY     veri girişi yapabilir (yeni kayıt + düzenleme + statü değişikliği;
            hepsi PENDING event olarak onaycıya düşer)
  APPROVER  bekleyen event'leri onaylar/reddeder (İşlemler ekranındaki
            Onayla/Reddet butonları yalnız bu rolde görünür)

Bir sicil iki rolü birden taşıyabilir. Ekran erişimi ayrıdır (uzmanın
access_scope/applications departman yetkisi — seed_fi_expert.py); rolü
olmayan kullanıcı ekranı görebilir ama form salt-okunur kalır.

Koşu davranışı: tablo TAMAMEN silinip aşağıdaki USERS ile yeniden yazılır
(tam yenileme — liste neyse yetki odur). Rol almak = listeden çıkarıp
yeniden koşmak.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ─────────────────────────────────────────────────────────────────────────
# KONFİG — Spyder'da args çalışmadığı için buradan düzenle (argparse yok).
# ─────────────────────────────────────────────────────────────────────────
SCHEMA: str | None = None   # None → bağlantı kullanıcısının şeması
                            # (uygulama da varsayılanda oradan okur)

#: sicil → roller. Rol: "ENTRY" ve/veya "APPROVER".
USERS: dict[str, tuple[str, ...]] = {
    "A63837": ("ENTRY", "APPROVER"),
    "A16438": ("ENTRY", "APPROVER"),
    # "A12345": ("ENTRY",),          # örnek: yalnız veri girici
    # "A67890": ("APPROVER",),       # örnek: yalnız onaycı
}
# ─────────────────────────────────────────────────────────────────────────

VALID_ROLES = {"ENTRY", "APPROVER"}


def main() -> int:
    from DataClient import DataClient

    rows: list[tuple[str, str]] = []
    for sicil, roles in USERS.items():
        s = str(sicil).strip().upper()
        for role in roles:
            r = str(role).strip().upper()
            if r not in VALID_ROLES:
                raise SystemExit(f"Geçersiz rol: {sicil} → {role!r} "
                                 f"(izinli: {sorted(VALID_ROLES)})")
            rows.append((s, r))
    if not rows:
        raise SystemExit("USERS boş — tabloyu boşaltmak istiyorsan bilinçli "
                         "olarak en az bir kullanıcı bırak (kimse giriş "
                         "yapamaz hale gelir).")

    dc = DataClient()
    con = dc.get_connection()
    try:
        schema = (SCHEMA or con.username).upper()
        qualified = f"{schema}.FI_LU_USER"
        now = datetime.now()
        cur = con.cursor()
        try:
            cur.execute(f"DELETE FROM {qualified}")
            cur.executemany(
                f"INSERT INTO {qualified} (SICIL, USER_ROLE, ACTIVE_FLG, LOADED_AT) "
                "VALUES (:1, :2, 1, :3)",
                [(s, r, now) for s, r in rows])
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            cur.close()

        print(f"✓ {qualified} yenilendi — {len(USERS)} kullanıcı, {len(rows)} rol:")
        for sicil, roles in USERS.items():
            print(f"   {sicil}: {', '.join(roles)}")
        return 0
    finally:
        dc.drop_connection(con)


if __name__ == "__main__":
    raise SystemExit(main())

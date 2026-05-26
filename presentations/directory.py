"""User directory lookups (LDAP-backed in PROD, fake list in DEV).

Used by the dashboard publish UI:
- search_users(q): returns up to 20 matches by name/sicil prefix
- list_dept_members(dept): returns all members of a department

Both shapes: list[dict] with keys {sicil, name, department}.
"""
from __future__ import annotations

import logging

from flask import current_app

log = logging.getLogger(__name__)


# ── DEV fake directory (used only when DEV_MODE flag is set on app.config) ───

_DEV_FAKE_USERS = [
    # Owner first — current dev user's department
    {"sicil": "A00000", "name": "Dev User",          "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI"},
    {"sicil": "A11111", "name": "Ayşe Yılmaz",       "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI"},
    {"sicil": "A22222", "name": "Mehmet Demir",      "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI"},
    {"sicil": "A33333", "name": "Zeynep Kaya",       "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI"},
    {"sicil": "A44444", "name": "Hakan Şahin",       "department": "FİNANSAL YAPAY ZEKA UYGULAMALARI"},
    # Other departments
    {"sicil": "B10001", "name": "Selin Aksoy",       "department": "HAZİNE PAZARLAMA"},
    {"sicil": "B10002", "name": "Tolga Yıldız",      "department": "HAZİNE PAZARLAMA"},
    {"sicil": "B10003", "name": "Esra Çelik",        "department": "HAZİNE PAZARLAMA"},
    {"sicil": "C20001", "name": "Burak Aydın",       "department": "RİSK YÖNETİMİ"},
    {"sicil": "C20002", "name": "Deniz Öztürk",      "department": "RİSK YÖNETİMİ"},
    {"sicil": "D30001", "name": "Cem Polat",         "department": "BÜTÇE VE KONTROL"},
    {"sicil": "D30002", "name": "Pınar Doğan",       "department": "BÜTÇE VE KONTROL"},
    {"sicil": "E40001", "name": "Murat Aslan",       "department": "GENEL MUHASEBE"},
    {"sicil": "E40002", "name": "Berna Erdem",       "department": "GENEL MUHASEBE"},
]


def _is_dev() -> bool:
    return bool(current_app.config.get("LOGIN_DISABLED")) or \
           bool(current_app.config.get("DEV_MODE"))


def search_users(q: str, limit: int = 20) -> list[dict]:
    """Name or sicil substring match. Used by search box autocomplete."""
    q = (q or "").strip()
    if not q or len(q) < 2:
        return []

    if _is_dev():
        ql = q.lower()
        out = [u for u in _DEV_FAKE_USERS
               if ql in u["name"].lower() or ql in u["sicil"].lower()]
        return out[:limit]

    # PROD: LDAP arama
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return []
    sql = (
        "SELECT SICIL, NAME, DEPARTMENT FROM A63837.TRESUARY_LDAP "
        "WHERE UPPER(NAME) LIKE '%' || UPPER(:q) || '%' "
        "   OR UPPER(SICIL) LIKE UPPER(:q) || '%' "
        "FETCH FIRST :lim ROWS ONLY"
    )
    try:
        df = dc.get_data(dataset="ldap_search", query=sql,
                         query_params={"q": q, "lim": limit})
    except Exception as exc:
        log.warning("search_users LDAP query failed: %s", exc)
        return []
    return [
        {"sicil": str(r["SICIL"]), "name": str(r["NAME"]),
         "department": str(r.get("DEPARTMENT") or "")}
        for _, r in df.iterrows()
    ]


def list_dept_members(dept: str) -> list[dict]:
    """All members of a given department."""
    dept = (dept or "").strip()
    if not dept:
        return []

    if _is_dev():
        return [u for u in _DEV_FAKE_USERS if u["department"] == dept]

    dc = current_app.config.get("DATA_CLIENT")
    if dc is None:
        return []
    sql = (
        "SELECT SICIL, NAME, DEPARTMENT FROM A63837.TRESUARY_LDAP "
        "WHERE DEPARTMENT = :dept ORDER BY NAME"
    )
    try:
        df = dc.get_data(dataset="ldap_dept", query=sql, query_params={"dept": dept})
    except Exception as exc:
        log.warning("list_dept_members LDAP query failed: %s", exc)
        return []
    return [
        {"sicil": str(r["SICIL"]), "name": str(r["NAME"]),
         "department": str(r.get("DEPARTMENT") or "")}
        for _, r in df.iterrows()
    ]

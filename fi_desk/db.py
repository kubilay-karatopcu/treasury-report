# -*- coding: utf-8 -*-
"""FI masası veri erişim katmanı — iki backend, tek arayüz.

- PROD/OFİS: DataClient.get_connection() üzerinden Oracle. SELECT'ler her
  çağrıda taze gider (S3 cache yok — onay/rol verisi bayat olamaz), DML
  deposit_panel'in `_execute_dml` deseniyle tek transaction'da koşar.
- DEV (DEV_MODE stub'ında ``get_connection`` yok): yerel DuckDB dosyası.
  Şema `jobs/fi_desk_schema.py`'daki DDL/VIEW/seed sabitlerinden çevrilerek
  kurulur (tek otorite orası; burada kopya DDL YOK). Böylece form + API
  lokalde uçtan uca çalışır ve testler gerçek SQL yolunu kullanır.

SQL lehçesi sözleşmesi: bu modüldeki çalışma-zamanı SQL'leri her iki motorda
da geçen alt kümede yazılır — COALESCE (NVL değil), :name bind (DuckDB'ye
$name'e çevrilir), tarih değerleri Python datetime bind'i (TO_DATE yok).
"""
from __future__ import annotations

import importlib.util
import logging
import re
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# init_app() doldurur (deposit_panel deseni).
_dc = None
_schema: str = ""
_resolved_schema: str | None = None  # boş şema → bağlantı kullanıcısından çözülür

# Importer typeahead'i için müşteri tablosu (2026-08-04): EDW müşteri
# tablosu ~32M satır — arama UPPER-önek LIKE + erken kesme (ROWNUM) ile
# yapılır; hız için tabloda UPPER({name_col}) fonksiyon indeksi beklenir.
# Konfigüre edilmemişse (ya da dev'de) FI_LU_LIST IMPORTER listesine düşülür.
_customer_table: str = ""
_customer_name_col: str = ""

# DEV backend durumu
_duck = None
_duck_lock = threading.RLock()


def init(dc, schema: str, customer_table: str = "",
         customer_name_col: str = "") -> None:
    global _dc, _schema, _resolved_schema, _customer_table, _customer_name_col
    _dc = dc
    _schema = (schema or "").strip().rstrip(".")
    _resolved_schema = None
    _customer_table = (customer_table or "").strip()
    _customer_name_col = (customer_name_col or "").strip() or "FULL_NM"


def is_dev() -> bool:
    """Oracle bağlantısı verebilen gerçek DataClient yoksa dev backend'deyiz."""
    return not hasattr(_dc, "get_connection")


def _prod_schema() -> str:
    """Hedef Oracle şeması. FI_DESK_SCHEMA boşsa BAĞLANTI KULLANICISININ
    şeması kullanılır — jobs/fi_desk_schema.py tabloları varsayılanda oraya
    yarattığı için doğru adres budur (2026-07-31: sabit 'A16438' varsayılanı
    farklı kullanıcıda ORA-00942 üretti). İlk kullanımda bir kez çözülür."""
    global _resolved_schema
    if _schema:
        return _schema
    if _resolved_schema is None:
        con, pooled = _acquire()
        try:
            _resolved_schema = (con.username or "").upper()
        finally:
            _release(con, pooled)
        log.info("fi_desk şeması bağlantı kullanıcısından çözüldü: %s",
                 _resolved_schema)
    return _resolved_schema


def qualified(table: str) -> str:
    """Tablo adını şema ile niteler; dev DuckDB'de şema yok, ad çıplak kalır."""
    if is_dev():
        return table
    return f"{_prod_schema()}.{table}"


def current_relation() -> str:
    """Teklif başına en-son-onaylı snapshot ilişkisi — INLINE alt-sorgu.

    V_FI_OFFER_CURRENT view'ına bilerek bağlanılmaz: EDW kişisel şemasında
    CREATE VIEW yetkisi olmayabiliyor (ORA-01031). Tek kaynak
    jobs/fi_desk_schema.py::CURRENT_SELECT; view varsa da yoksa da uygulama
    aynı SQL'i koşar. FROM {current_relation()} biçiminde kullanılır."""
    defs = _get_schema_defs()
    if is_dev():
        return "(" + _oracle_view_to_duckdb(defs.CURRENT_SELECT) + ")"
    return "(" + defs.CURRENT_SELECT.format(s=f"{_prod_schema()}.") + ")"


# ─────────────────────────────────────────────────────────────────────────
# jobs/fi_desk_schema.py sabitlerini yükle (DDL tek otorite orada; jobs
# paketi değil → importlib ile dosyadan). oracledb import'u main() içinde
# olduğundan modül yüklemesi güvenlidir.
# ─────────────────────────────────────────────────────────────────────────
_schema_defs = None


def _load_schema_module():
    path = _REPO_ROOT / "jobs" / "fi_desk_schema.py"
    spec = importlib.util.spec_from_file_location("fi_desk_schema_defs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_schema_defs():
    global _schema_defs
    if _schema_defs is None:
        _schema_defs = _load_schema_module()
    return _schema_defs


def _oracle_ddl_to_duckdb(sql: str) -> str:
    s = sql
    s = re.sub(r"VARCHAR2\((\d+)\)", r"VARCHAR", s)
    s = re.sub(r"\bNUMBER\(1\)", "INTEGER", s)
    s = re.sub(r"\bNUMBER\b", "DOUBLE", s)
    s = re.sub(r"\bDATE\b", "TIMESTAMP", s)
    return s


def _oracle_view_to_duckdb(sql: str) -> str:
    """CURRENT_SELECT/VIEW_SQL zaten taşınabilir yazılır (CAST'li tarih
    farkı); DuckDB için yalnız SYSDATE çevirisi kalır (oracle_duck ile aynı
    kurallar — prod duck_cache yolu da aynı çeviriyi yapar)."""
    s = sql.format(s="")
    s = re.sub(r"\bTRUNC\s*\(\s*SYSDATE\s*\)", "CURRENT_DATE", s, flags=re.I)
    s = re.sub(r"\bSYSDATE\b", "CURRENT_TIMESTAMP", s, flags=re.I)
    return s


def _dev_db_path() -> Path:
    return Path(tempfile.gettempdir()) / "fi_desk_dev.duckdb"


def _get_duck():
    """Dev DuckDB bağlantısı — ilk çağrıda şema + lookup seed kurulur."""
    global _duck
    with _duck_lock:
        if _duck is not None:
            return _duck
        import duckdb

        conn = duckdb.connect(str(_dev_db_path()))
        defs = _get_schema_defs()
        existing = {r[0].upper() for r in conn.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
        for name, ddl in defs.DDL.items():
            if name.upper() in existing:
                continue
            conn.execute(_oracle_ddl_to_duckdb(ddl.format(t=name)))
        # Sonradan eklenen kolonlar — var olan dev db dosyaları için
        # (Oracle tarafındaki add_columns'ın karşılığı).
        for table, col, typ in getattr(defs, "COLUMN_ADDITIONS", []):
            duck_typ = _oracle_ddl_to_duckdb(typ)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
                         f"{col} {duck_typ}")
        conn.execute(_oracle_view_to_duckdb(defs.VIEW_SQL))
        if not conn.execute("SELECT COUNT(*) FROM FI_LU_LIST").fetchone()[0]:
            _dev_seed_lookups(conn, defs)
        _duck = conn
        log.info("fi_desk dev DuckDB hazır: %s", _dev_db_path())
        return _duck


def _dev_seed_lookups(conn, defs) -> None:
    from datetime import datetime

    now = datetime.now()
    conn.executemany(
        "INSERT INTO FI_LU_LIST (LIST_NAME, VALUE_CD, LABEL, PARENT_CD, ATTR1,"
        " SORT_NO, ACTIVE_FLG, LOADED_AT) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
        [(ln, cd, lbl, par, at1, i, now)
         for i, (ln, cd, lbl, par, at1) in enumerate(defs.SEED_LISTS)])
    conn.executemany(
        "INSERT INTO FI_LU_BANK (BANK_NM, COUNTRY, REGION, GROUP_COMPANY,"
        " GROUP_COMPANY_COUNTRY, GROUP_COMPANY_REGION, IS_SELF, ACTIVE_FLG,"
        " LOADED_AT) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
        [(b, c, r, g, gc, gr, s, now) for b, c, r, g, gc, gr, s in defs.SEED_BANKS])
    conn.executemany(
        "INSERT INTO FI_LU_USER (SICIL, USER_ROLE, ACTIVE_FLG, LOADED_AT)"
        " VALUES (?, ?, 1, ?)",
        [(s, r, now) for s, r in defs.SEED_USERS])


def reset_dev_store() -> None:
    """Testler için: dev DuckDB'yi kapat ve dosyayı sil."""
    global _duck
    with _duck_lock:
        if _duck is not None:
            _duck.close()
            _duck = None
        p = _dev_db_path()
        if p.exists():
            p.unlink()


_BIND_RE = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b")


def _to_duck_binds(sql: str) -> str:
    return _BIND_RE.sub(r"$\1", sql)


# ─────────────────────────────────────────────────────────────────────────
# Tek arayüz
# ─────────────────────────────────────────────────────────────────────────
def _acquire():
    """Oracle bağlantısı al — HAVUZDAN. get_connection() her çağrıda sıfırdan
    TCP+auth turu yapar (~1-2 sn); istek başına 3-5 sorgu = 7-8 sn'lik onay/
    giriş süreleri (2026-07-31 saha bulgusu). DataClient.get_connection_from_pool
    kendi kendini kurar ve thread-safe'tir; havuz yoksa/patlarsa düz bağlantıya
    düşülür (davranış aynı, yalnız yavaş)."""
    if hasattr(_dc, "get_connection_from_pool"):
        try:
            return _dc.get_connection_from_pool(), True
        except Exception:
            log.warning("fi_desk: bağlantı havuzu kullanılamadı, düz bağlantı",
                        exc_info=True)
    return _dc.get_connection(), False


def _release(con, pooled: bool) -> None:
    if pooled:
        _dc.drop_connection_from_pool(con)
    else:
        _dc.drop_connection(con)


def query(sql: str, params: dict | None = None) -> list[dict]:
    """SELECT — satırlar dict listesi olarak (kolon adları büyük harf)."""
    if is_dev():
        with _duck_lock:
            cur = _get_duck().execute(_to_duck_binds(sql), params or {})
            cols = [d[0].upper() for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    con, pooled = _acquire()
    try:
        cur = con.cursor()
        try:
            cur.execute(sql, params or {})
            cols = [d[0].upper() for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()
    finally:
        _release(con, pooled)


def customer_source() -> str:
    """'table' → EDW müşteri tablosu; 'lookup' → FI_LU_LIST IMPORTER fallback."""
    return "table" if (_customer_table and not is_dev()) else "lookup"


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def customer_search(q: str, limit: int = 30) -> list[str]:
    """Importer typeahead — isim ÖNEK araması (case-insensitive), erken kesme.

    32M satırlık müşteri tablosunda '%q%' (contains) araması B-tree indeks
    kullanamaz (full scan); UPPER-önek LIKE ise UPPER({col}) fonksiyon
    indeksiyle range-scan olur ve ROWNUM tavanı taramayı erken keser.
    Mükerrer isimler Python'da ayıklanır (DISTINCT+ROWNUM etkileşimine
    girmemek için aday satır tavanı limit×4 alınır)."""
    pattern = _escape_like(q.strip().upper()) + "%"
    if customer_source() == "table":
        sql = (f"SELECT {_customer_name_col} AS NAME FROM {_customer_table} "  # noqa: S608
               f"WHERE UPPER({_customer_name_col}) LIKE :q ESCAPE '\\' "
               "AND ROWNUM <= :lim")
        rows = query(sql, {"q": pattern, "lim": limit * 4})
    else:
        sql = (f"SELECT LABEL AS NAME FROM {qualified('FI_LU_LIST')} "
               "WHERE LIST_NAME = 'IMPORTER' AND ACTIVE_FLG = 1 "
               "AND UPPER(LABEL) LIKE :q ESCAPE '\\' ORDER BY SORT_NO")
        rows = query(sql, {"q": pattern})
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        name = (r.get("NAME") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def execute(statements: list[tuple[str, dict | None]]) -> None:
    """DML — tek transaction, hata olursa rollback (deposit_panel deseni)."""
    if is_dev():
        with _duck_lock:
            conn = _get_duck()
            conn.execute("BEGIN TRANSACTION")
            try:
                for sql, params in statements:
                    conn.execute(_to_duck_binds(sql), params or {})
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return

    con, pooled = _acquire()
    try:
        cur = con.cursor()
        try:
            for sql, params in statements:
                cur.execute(sql, params or {})
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            cur.close()
    finally:
        _release(con, pooled)

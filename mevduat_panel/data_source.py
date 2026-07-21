"""Veri erişim katmanı — kaynak `engine/db_source.py`'nin DataClient uyarlaması.

Kaynak: NIM_calculation engine/db_source.py. `load_dataframe(name, params)`
imzası birebir korunur; engine portları değişiklik olmadan çalışır.

İki yol (kaynaktaki ENV dispatch'inin uyarlaması):

- **PROD:** DataClient havuzundan Oracle (`queries/*.sql`, Oracle lehçesi).
- **DEV:** DataClient `edw_query_to_pandas` sunmuyorsa (DEV stub) kaynağın
  sentetik SQLite'ı kullanılır: `data/dev.db` + `queries/dev/*.sql` aynaları
  (kaynak repodan birebir; 2026-07-21 kullanıcı kararıyla A1'in "dev.db yok"
  kararı revize edildi — lokal geliştirme dev.db ile). Bind stili her iki
  yolda named (`:NAME`); sqlite3 da oracledb da dict bind kabul eder.

SQL dosyaları `mevduat_panel/queries/*.sql` — kaynak reponun `queries/prod/`
kopyası, `A16438.` şema prefix'i repo konvansiyonuyla (queries/deposits/)
tutarlı olarak aynen korunur. weekly_rollings* DD/MM/YYYY string bind alır
(prod SQL içindeki TO_DATE ile uyumlu; dev aynası aynı imzayı bekler).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
from flask import current_app

QUERIES_DIR = Path(__file__).parent / "queries"
DEV_QUERIES_DIR = QUERIES_DIR / "dev"
DEV_DB_PATH = Path(__file__).parent / "data" / "dev.db"

#: Geçerli sorgu adları — path traversal ve yazım hatasına karşı kapı.
QUERY_NAMES = frozenset(p.stem for p in QUERIES_DIR.glob("*.sql"))


def _sql(name: str, dev: bool = False) -> str:
    if name not in QUERY_NAMES:
        raise KeyError(f"Bilinmeyen sorgu: {name!r} (mevcut: {sorted(QUERY_NAMES)})")
    base = DEV_QUERIES_DIR if dev else QUERIES_DIR
    return (base / f"{name}.sql").read_text(encoding="utf-8")


def is_dev() -> bool:
    """DEV yolu aktif mi? — DataClient `edw_query_to_pandas` sunmuyorsa
    sorgular dev.db'ye gider (bkz. load_dataframe). Tarih bind formatı gibi
    lehçe-bağımlı kararlar için engine'ler bunu sorar (weekly._to_bind)."""
    dc = current_app.config.get("DATA_CLIENT")
    return (dc is None or not hasattr(dc, "edw_query_to_pandas")) and DEV_DB_PATH.exists()


def _load_sqlite(name: str, params: dict | None) -> pd.DataFrame:
    """Kaynak `_load_sqlite` birebir: dev.db + SQLite lehçesi aynaları."""
    with sqlite3.connect(DEV_DB_PATH) as conn:
        return pd.read_sql_query(_sql(name, dev=True), conn, params=params or None)


def load_dataframe(name: str, params: dict | None = None) -> pd.DataFrame:
    """`mevduat_panel/queries/{name}.sql`'i DataClient havuzu üzerinden koşar.

    Kaynak imza korunur; çağıran engine'ler cache'lemeyi kendileri yapar
    (kaynak davranış: process-lifetime cache, veri güncellemesi = restart).
    """
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None or not hasattr(dc, "edw_query_to_pandas"):
        if DEV_DB_PATH.exists():
            return _load_sqlite(name, params)
        raise RuntimeError(
            "mevduat_panel prod DataClient gerektirir ve data/dev.db bulunamadı "
            "(DEV yolu için kaynak reponun dev.db'si mevduat_panel/data/ altına "
            "konmalı; testler load_dataframe'i monkeypatch'ler)."
        )
    con = dc.get_connection_from_pool()
    try:
        return dc.edw_query_to_pandas(con, _sql(name), params=dict(params or {}))
    finally:
        try:
            dc.drop_connection_from_pool(con)
        except Exception:  # havuz kapanmış olabilir — sorgu sonucu yine geçerli
            current_app.logger.exception("mevduat_panel: havuz bağlantısı bırakılamadı")

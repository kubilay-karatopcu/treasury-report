"""Veri erişim katmanı — kaynak `engine/db_source.py`'nin DataClient uyarlaması.

Kaynak: NIM_calculation engine/db_source.py. `load_dataframe(name, params)`
imzası birebir korunur; engine portları değişiklik olmadan çalışır.

Kaynak repodaki DEVELOPMENT/PRODUCTION_EXC yolları TAŞINMADI (kullanıcı
kararı: dev.db yok, doğrudan prod). Tek yol: DataClient havuzundan Oracle.
Testler `load_dataframe`'i monkeypatch'leyip sentetik DataFrame verir.

SQL dosyaları `mevduat_panel/queries/*.sql` — kaynak reponun `queries/prod/`
kopyası, `A16438.` şema prefix'i repo konvansiyonuyla (queries/deposits/)
tutarlı olarak aynen korunur. Bind stili named (`:NAME`), weekly_rollings*
DD/MM/YYYY string bind alır (SQL içindeki TO_DATE ile uyumlu).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from flask import current_app

QUERIES_DIR = Path(__file__).parent / "queries"

#: Geçerli sorgu adları — path traversal ve yazım hatasına karşı kapı.
QUERY_NAMES = frozenset(p.stem for p in QUERIES_DIR.glob("*.sql"))


def _sql(name: str) -> str:
    if name not in QUERY_NAMES:
        raise KeyError(f"Bilinmeyen sorgu: {name!r} (mevcut: {sorted(QUERY_NAMES)})")
    return (QUERIES_DIR / f"{name}.sql").read_text(encoding="utf-8")


def load_dataframe(name: str, params: dict | None = None) -> pd.DataFrame:
    """`mevduat_panel/queries/{name}.sql`'i DataClient havuzu üzerinden koşar.

    Kaynak imza korunur; çağıran engine'ler cache'lemeyi kendileri yapar
    (kaynak davranış: process-lifetime cache, veri güncellemesi = restart).
    """
    dc = current_app.config.get("DATA_CLIENT")
    if dc is None or not hasattr(dc, "edw_query_to_pandas"):
        raise RuntimeError(
            "mevduat_panel prod DataClient gerektirir (DEV stub'ında Oracle yolu yok; "
            "testler load_dataframe'i monkeypatch'ler)."
        )
    con = dc.get_connection_from_pool()
    try:
        return dc.edw_query_to_pandas(con, _sql(name), params=dict(params or {}))
    finally:
        try:
            dc.drop_connection_from_pool(con)
        except Exception:  # havuz kapanmış olabilir — sorgu sonucu yine geçerli
            current_app.logger.exception("mevduat_panel: havuz bağlantısı bırakılamadı")

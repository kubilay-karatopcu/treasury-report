"""fake_db.py — Lokal (ofis dışı) geliştirme için sahte tablo deposu.

`python app.py` DEV_MODE'da (ofis dışında, Oracle/S3 yokken) bu modülü
kullanır. Tablolar `dev_data/table_docs/<SCHEMA>/<TABLE>.yaml` katalog
tanımından ve eşleşen `dev_data/sample_data/<TABLE>.csv` mock verisinden
üretilir. Yeni bir tablo eklemek için: bir table_doc YAML + aynı adlı bir
CSV koy, yeniden başlat.

DATE / TIMESTAMP tipli kolonlar table_doc'a bakılarak gerçek tarihe
parse edilir; böylece DuckDB'de ``WHERE CREATE_DT >= DATE '2026-06-01'``
gibi tarih filtreleri string karşılaştırmasına düşmeden çalışır.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_DOCS_DIR = _ROOT / "dev_data" / "table_docs"
_DATA_DIR = _ROOT / "dev_data" / "sample_data"

# table_id ("EDW.MYU_DAILY_RES") → DataFrame. Lazily doldurulur.
_CACHE: dict[str, pd.DataFrame] = {}
# table_id → (csv_path, date_columns) — ilk taramada kurulur.
_REGISTRY: dict[str, tuple[Path, list[str]]] = {}
_SCANNED = False


def _date_columns(doc: dict) -> list[str]:
    cols = doc.get("columns") or {}
    out = []
    for name, meta in cols.items():
        t = str((meta or {}).get("type", "")).upper()
        if t.startswith("DATE") or t.startswith("TIMESTAMP"):
            out.append(name)
    return out


def _scan() -> None:
    """dev_data/table_docs altındaki her YAML için eşleşen CSV'yi kaydet."""
    global _SCANNED
    if _SCANNED:
        return
    _REGISTRY.clear()
    if _DOCS_DIR.exists():
        for yaml_path in sorted(_DOCS_DIR.rglob("*.yaml")):
            try:
                doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            except Exception:
                log.warning("fake_db: bozuk table_doc atlandı: %s", yaml_path, exc_info=True)
                continue
            schema = doc.get("schema") or yaml_path.parent.name
            table = doc.get("table") or yaml_path.stem
            csv_path = _DATA_DIR / f"{table}.csv"
            if not csv_path.exists():
                log.warning("fake_db: %s.%s için CSV yok (%s), atlanıyor.",
                            schema, table, csv_path.name)
                continue
            _REGISTRY[f"{schema}.{table}"] = (csv_path, _date_columns(doc))
    _SCANNED = True
    log.info("fake_db: %d tablo hazır — %s", len(_REGISTRY), ", ".join(sorted(_REGISTRY)))


def known_tables() -> list[str]:
    """Kayıtlı tüm tablo id'leri (ör. ['EDW.MYU_DAILY_RES', ...])."""
    _scan()
    return sorted(_REGISTRY)


def get(table_id: str) -> pd.DataFrame | None:
    """Bir tablonun DataFrame'i. Bilinmeyen id için None."""
    _scan()
    if table_id not in _REGISTRY:
        return None
    if table_id not in _CACHE:
        csv_path, date_cols = _REGISTRY[table_id]
        df = pd.read_csv(csv_path)
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        _CACHE[table_id] = df
    return _CACHE[table_id].copy()

"""fake_db.py — Lokal (ofis dışı) geliştirme için sahte tablo deposu.

`python app.py` DEV_MODE'da (ofis dışında, Oracle/S3 yokken) bu modülü
kullanır. Tablolar `dev_data/table_docs/<SCHEMA>/<TABLE>.yaml` katalog
tanımından ve eşleşen `dev_data/sample_data/<TABLE>.csv` mock verisinden
üretilir. Yeni bir tablo eklemek için: bir table_doc YAML + aynı adlı bir
CSV koy — kayıt defteri dosya mtime'larını izler (2 sn aralıkla), restart
gerekmez. TABLE_DOC_STORE'un 60 sn'lik cache'iyle kalıcı-cache sapması
yaşamamak için hem YAML seti hem CSV içerikleri değişiklikte tazelenir.

DATE / TIMESTAMP tipli kolonlar table_doc'a bakılarak gerçek tarihe
parse edilir; böylece DuckDB'de ``WHERE CREATE_DT >= DATE '2026-06-01'``
gibi tarih filtreleri string karşılaştırmasına düşmeden çalışır.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import yaml

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_DOCS_DIR = _ROOT / "dev_data" / "table_docs"
_DATA_DIR = _ROOT / "dev_data" / "sample_data"

# table_id ("EDW.MYU_DAILY_RES") → (csv_mtime, DataFrame). Lazily doldurulur;
# CSV değişince (mtime) satır tekrar okunur.
_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
# table_id → (csv_path, date_columns) — taramada kurulur.
_REGISTRY: dict[str, tuple[Path, list[str]]] = {}
# YAML setinin imzası (path, mtime, size) — değişince rescan.
_DOCS_SIG: tuple | None = None
_LAST_CHECK = 0.0
_CHECK_INTERVAL_S = 2.0


def _date_columns(doc: dict) -> list[str]:
    cols = doc.get("columns") or {}
    out = []
    for name, meta in cols.items():
        t = str((meta or {}).get("type", "")).upper()
        if t.startswith("DATE") or t.startswith("TIMESTAMP"):
            out.append(name)
    return out


def _docs_signature() -> tuple:
    """YAML setinin (path, mtime, size) imzası — değişiklik tespiti için."""
    if not _DOCS_DIR.exists():
        return ()
    sig = []
    for p in sorted(_DOCS_DIR.rglob("*.yaml")):
        try:
            st = p.stat()
            sig.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sig)


def _scan() -> None:
    """dev_data/table_docs altındaki her YAML için eşleşen CSV'yi kaydet.

    Her çağrıda (2 sn aralıkla) YAML imzasını kontrol eder; değişmişse
    registry + DataFrame cache tazelenir. Böylece dev'de YAML/CSV düzenlemek
    restart gerektirmez ve TABLE_DOC_STORE ile aynı dosyaları gören iki
    okuyucu birbirinden sapmaz.
    """
    global _DOCS_SIG, _LAST_CHECK
    now = time.monotonic()
    if _DOCS_SIG is not None and (now - _LAST_CHECK) < _CHECK_INTERVAL_S:
        return
    _LAST_CHECK = now

    sig = _docs_signature()
    if sig == _DOCS_SIG:
        return
    first_scan = _DOCS_SIG is None
    _DOCS_SIG = sig

    _REGISTRY.clear()
    _CACHE.clear()  # date_columns değişmiş olabilir — temiz başla
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
    log.log(logging.INFO if first_scan else logging.DEBUG,
            "fake_db: %d tablo hazır — %s", len(_REGISTRY), ", ".join(sorted(_REGISTRY)))


def known_tables() -> list[str]:
    """Kayıtlı tüm tablo id'leri (ör. ['EDW.MYU_DAILY_RES', ...])."""
    _scan()
    return sorted(_REGISTRY)


def get(table_id: str) -> pd.DataFrame | None:
    """Bir tablonun DataFrame'i. Bilinmeyen id için None. CSV mtime'ı
    değişmişse cache'i atlayıp dosyayı yeniden okur."""
    _scan()
    if table_id not in _REGISTRY:
        return None
    csv_path, date_cols = _REGISTRY[table_id]
    try:
        mtime = csv_path.stat().st_mtime
    except OSError:
        _CACHE.pop(table_id, None)
        return None
    cached = _CACHE.get(table_id)
    if cached is None or cached[0] != mtime:
        df = pd.read_csv(csv_path)
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        _CACHE[table_id] = (mtime, df)
    return _CACHE[table_id][1].copy()

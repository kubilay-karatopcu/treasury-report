"""Faz P — sandbox veri aktarımı (ebeveyn ⇄ alt-process).

Tek format, iki taraf: :mod:`executor` (ebeveyn) ve :mod:`_runner` (alt-process)
bunu kullanır. Gereksinimler ve tasarım:

- **pyarrow/fastparquet GEREKTİRMEZ** — saf stdlib json. (Ofiste alt-process'te
  parquet engine yoktu → "no parquet engine" hatası.)
- **Kod ÇALIŞTIRMAZ** (pickle aksine) — sandbox→ebeveyn okuma güvenli.
- **dtype korur** — int/float/bool/string JSON Table Schema (``orient='table'``)
  ile; datetime kolonları ayrı ele alınır.
- **ns-taşması güvenli** — pandas ``datetime64[ns]`` yıl ~2262'de taşar; finansta
  "max/sonsuz" tarih sentinel'leri (2400, 9999-12-31) yaygın. Datetime kolonları
  numpy ``datetime64[us]`` üzerinden ISO string olarak yazılır/okunur (numpy'da
  böyle bir aralık limiti yok), Timestamp'e (ns-bağlı) hiç çevrilmez.
"""
from __future__ import annotations

import json
from io import StringIO


def write_table(df, path) -> None:
    import pandas as pd

    d = df.copy() if df is not None else pd.DataFrame()
    # Anlamlı (RangeIndex olmayan) index'i kolona çevir → groupby keys kaybolmaz
    # + table orient'in tekrarlı-index hatası önlenir.
    if not isinstance(d.index, pd.RangeIndex):
        d = d.reset_index()

    dt_cols: dict[str, str] = {}
    for c in list(d.columns):
        if pd.api.types.is_datetime64_any_dtype(d[c]):
            dt_cols[str(c)] = "us"
            # numpy us ISO string'i: yıl 2262 üstü tarihleri de güvenle yazar.
            arr = d[c].to_numpy(dtype="datetime64[us]").astype(str)
            d[c] = [None if s == "NaT" else s for s in arr]

    payload = {"_dt_cols": dt_cols, "table": d.to_json(orient="table", index=False)}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def read_table(path):
    import numpy as np
    import pandas as pd

    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    df = pd.read_json(StringIO(payload["table"]), orient="table")
    for c in (payload.get("_dt_cols") or {}):
        if c in df.columns:
            vals = df[c].tolist()
            arr = np.array(
                [np.datetime64("NaT") if v is None else np.datetime64(v, "us") for v in vals],
                dtype="datetime64[us]",
            )
            df[c] = pd.Series(arr, index=df.index)
    return df

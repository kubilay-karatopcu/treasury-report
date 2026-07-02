"""Faz P — sandbox veri aktarımı (ebeveyn ⇄ alt-process).

Tek format, iki taraf: :mod:`executor` (ebeveyn) ve :mod:`_runner` (alt-process)
bunu kullanır. Gereksinimler ve tasarım:

- **pyarrow/fastparquet GEREKTİRMEZ** — saf stdlib json. (Ofiste alt-process'te
  parquet engine yoktu → "no parquet engine" hatası.)
- **Kod ÇALIŞTIRMAZ** (pickle aksine) — sandbox→ebeveyn okuma güvenli.
- **dtype korur** — int/float/bool/string JSON Table Schema (``orient='table'``)
  ile; datetime kolonları ayrı ele alınır.
- **Decimal → float64** — Oracle NUMBER kolonları pandas'a ``decimal.Decimal``
  (object dtype) gelir. JSON Table Schema'da Decimal tipi YOK → object kolonlar
  "string" olarak yazılıp sandbox'a ``str`` dtype olarak dönerdi; o zaman sayısal
  işlemler sessizce kırılır (``.round()`` str'de no-op, ``np.average`` TypeError).
  Bu yüzden TAMAMI Decimal olan kolonlar float64'e çevrilir. Gerçek string
  kolonlara (kod/ID — ör. "00123") DOKUNULMAZ; string string kalır.
- **ns-taşması güvenli** — pandas ``datetime64[ns]`` yıl ~2262'de taşar; finansta
  "max/sonsuz" tarih sentinel'leri (2400, 9999-12-31) yaygın. Datetime kolonları
  numpy ``datetime64[us]`` üzerinden ISO string olarak yazılır/okunur (numpy'da
  böyle bir aralık limiti yok), Timestamp'e (ns-bağlı) hiç çevrilmez.
"""
from __future__ import annotations

import json
from io import StringIO
import decimal


def write_table(df, path) -> None:
    import pandas as pd

    d = df.copy() if df is not None else pd.DataFrame()
    # Anlamlı (RangeIndex olmayan) index'i kolona çevir → groupby keys kaybolmaz
    # + table orient'in tekrarlı-index hatası önlenir. İSTİSNA: adsız düz
    # integer index (sort_values / filtre / dropna artığı — pandas contiguous
    # olmayınca RangeIndex'i Int64Index'e düşürür) KONUMSALDIR, veri taşımaz;
    # kolona çevirmek downstream'e hayalet bir "index" kolonu sızdırır. Adlı /
    # Multi / datetime index'ler anlamlı kabul edilip korunur.
    if not isinstance(d.index, pd.RangeIndex):
        if (d.index.name is None and d.index.nlevels == 1
                and pd.api.types.is_integer_dtype(d.index.dtype)):
            d = d.reset_index(drop=True)
        else:
            d = d.reset_index()

    # Kolon etiketlerini string'e çevir. JSON Table Schema (orient='table') şema
    # ADINI etiketin tipiyle yazar (int etiket → şema "name": 0) ama veri
    # satırlarını DAİMA JSON string anahtarla yazar ("0"). pandas 3.0'da okuma
    # bunları eşleştiremez: int-adlı int kolon IntCastingNaNError ile ÇÖKER
    # (sonuç sessizce atılır), int-adlı float kolon ise SESSİZCE NaN'a düşer.
    # `value_counts().to_frame().T`, crosstab, pivot integer-seviyeli kolon vb.
    # çok yaygın → serileştirmeden önce etiketleri str'le (executor zaten
    # downstream'de [str(c) ...] yapıyor, tip kaybı yok).
    d.columns = [str(c) for c in d.columns]

    # Tekrarlı kolon etiketleri (concat axis=1, merge suffix çakışması, manuel
    # kurgu): JSON Table Schema veri satırlarını kolon ADIYLA anahtarlar → aynı
    # adlı kolonlar JSON nesnesinde çakışıp veri kaybeder ("a":1,"a":2 → 2) ve
    # okuma kolonları patlatır. Serileştirmeden ÖNCE benzersizleştir; okuyunca
    # orijinal (tekrarlı olabilen) etiketleri _orig_cols'tan geri yükle.
    orig_cols = list(d.columns)
    seen: dict[str, int] = {}
    uniq_cols: list[str] = []
    for c in orig_cols:
        if c in seen:
            seen[c] += 1
            uniq_cols.append(f"{c}.__dup{seen[c]}")
        else:
            seen[c] = 0
            uniq_cols.append(c)
    d.columns = uniq_cols

    # Oracle NUMBER → Decimal (object). JSON Table Schema'da Decimal tipi yok →
    # object kolonlar str'ye ezilir, sandbox'ta sayısal işlemler sessizce kırılır.
    # SADECE tamamı-Decimal değil, İÇİNDE bir tane bile Decimal olan object
    # kolonu da numerify et: fillna(skaler)/skaler-atama Decimal'i int/float ile
    # karıştırınca infer_dtype "mixed"/"mixed-integer" döner ("decimal" değil),
    # kolon str'ye ezilirdi. Gerçek string kod kolonunda ("00123") hiç Decimal
    # eleman yok → dokunulmaz. Pozisyonla dolaş: tekrarlı etikette d[c] DataFrame
    # döner, .dtype patlardı (çıktı sessizce kaybolurdu).
    for i in range(d.shape[1]):
        s = d.iloc[:, i]
        if s.dtype == object:
            inferred = pd.api.types.infer_dtype(s, skipna=True)
            has_decimal = inferred == "decimal" or any(
                isinstance(v, decimal.Decimal) for v in s
            )
            if has_decimal:
                d.isetitem(i, pd.to_numeric(s, errors="coerce"))

    # _dt_cols anahtarı POZİSYON indeksi (str), değeri {"unit", "tz"} sözlüğü:
    # tekrarlı kolon adında ad ile eşleştirme belirsiz olurdu; pozisyon güvenli.
    dt_cols: dict[str, dict] = {}
    for i in range(d.shape[1]):
        s = d.iloc[:, i]
        dtype = s.dtype
        if isinstance(dtype, pd.DatetimeTZDtype):
            # tz-farkında kolon: eskiden to_numpy('us') tz'yi sessizce düşürür,
            # değer UTC wall-clock NAIVE dönerdi (Europe/Istanbul +03 → saat 3
            # kayar, gün sınırı geçebilir). tz'yi kaydet, UTC ISO yaz, okuyunca
            # yeniden localize et → instant + tz korunur.
            dt_cols[str(i)] = {"unit": "us", "tz": str(dtype.tz)}
            utc = s.dt.tz_convert("UTC").dt.tz_localize(None)
            arr = utc.to_numpy(dtype="datetime64[us]").astype(str)
            d.isetitem(i, [None if x == "NaT" else x for x in arr])
        elif pd.api.types.is_datetime64_any_dtype(s):
            dt_cols[str(i)] = {"unit": "us", "tz": None}
            # numpy us ISO string'i: yıl 2262 üstü tarihleri de güvenle yazar.
            arr = s.to_numpy(dtype="datetime64[us]").astype(str)
            d.isetitem(i, [None if x == "NaT" else x for x in arr])

    payload = {
        "_dt_cols": dt_cols,
        "_orig_cols": orig_cols,
        "table": d.to_json(orient="table", index=False),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def read_table(path):
    import numpy as np
    import pandas as pd

    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    df = pd.read_json(StringIO(payload["table"]), orient="table")
    for key, meta in (payload.get("_dt_cols") or {}).items():
        # Yeni format: anahtar pozisyon indeksi (str), meta {"unit","tz"} dict.
        # Eski format: anahtar kolon ADI, meta "us" string → geriye dönük tolerans.
        try:
            i = int(key)
        except (TypeError, ValueError):
            if key not in df.columns:
                continue
            i = list(df.columns).index(key)
        if i >= df.shape[1]:
            continue
        tz = meta.get("tz") if isinstance(meta, dict) else None
        vals = df.iloc[:, i].tolist()
        # to_json null'ları okuyunca None DEĞİL nan (float) dönebilir; "NaT"
        # string'i de mümkün. Hepsini NaT'e indirge (yoksa np.datetime64 patlar).
        arr = np.array(
            [
                np.datetime64("NaT")
                if (v is None or v == "NaT" or (isinstance(v, float) and v != v))
                else np.datetime64(v, "us")
                for v in vals
            ],
            dtype="datetime64[us]",
        )
        ser = pd.Series(arr, index=df.index)
        if tz:
            # UTC ISO yazılmıştı; instant'ı koruyarak orijinal tz'ye geri al.
            ser = ser.dt.tz_localize("UTC").dt.tz_convert(tz)
        df.isetitem(i, ser)
    # Orijinal (tekrarlı olabilen) kolon etiketlerini geri yükle.
    orig_cols = payload.get("_orig_cols")
    if orig_cols is not None and len(orig_cols) == df.shape[1]:
        df.columns = orig_cols
    return df

"""Oracle → DuckDB lehçe çevirisi — oturum tablo önbelleği için.

Importer'ın ürettiği blok SQL'leri Oracle lehçesindedir (NVL, FROM DUAL,
ROWNUM, REGEXP_SUBSTR, TO_CHAR, RATIO_TO_REPORT). Küçük PRISMA_* tabloları
oturumun DuckDB'sine bir kez çekildiğinde aynı SQL'lerin lokalde koşması
için mekanik çeviri gerekir. Bu modül TEK kaynaktır: apply-filters'ın
tablo-önbelleği yolu da, DuckDB tabanlı regresyon testleri de bunu kullanır
— yani çevirinin tüm dashboard SQL'lerinde doğruluğu test edilmiştir.

Kapsam bilinçli olarak importer'ın kullandığı yapılarla sınırlıdır; çeviri
sonrası DuckDB hatası olursa çağıran Oracle'a düşer (davranış bozulmaz,
yalnız hızlanma kaybolur).
"""
from __future__ import annotations

import re

__all__ = ["oracle_sql_to_duckdb", "find_oracle_table_refs"]


# FROM/JOIN sonrası şema-nitelikli tablo referansları (CTE adları noktasızdır,
# kolon nitelemeleri f.MONTH gibi FROM/JOIN pozisyonunda geçmez).
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$#]*\.[A-Za-z_][\w$#]*)", re.IGNORECASE)


def find_oracle_table_refs(sql: str) -> list[str]:
    """SQL'deki şema-nitelikli taban tablo adları (UPPER, tekilleştirilmiş)."""
    seen: list[str] = []
    for m in _TABLE_REF_RE.finditer(sql or ""):
        t = m.group(1).upper()
        if t not in seen:
            seen.append(t)
    return seen


def _split_top_level_args(argstr: str) -> list[str]:
    """Parantez derinliğini sayarak üst-düzey virgüllerden böl (string
    literal'lerin içindeki virgül/parantezleri atlayarak)."""
    args, depth, start, i, n = [], 0, 0, 0, len(argstr)
    in_str = False
    while i < n:
        c = argstr[i]
        if in_str:
            if c == "'":
                if i + 1 < n and argstr[i + 1] == "'":
                    i += 1          # '' kaçışı
                else:
                    in_str = False
        elif c == "'":
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(argstr[start:i])
            start = i + 1
        i += 1
    args.append(argstr[start:])
    return [a.strip() for a in args]


def _rewrite_calls(sql: str, fname: str, transform) -> str:
    """``fname(...)`` çağrılarını dengeli parantezle bulup ``transform(args,
    tail)`` çıktısıyla değiştirir. transform(args, sql, end_idx) →
    (replacement, new_end) ya da None (dokunma)."""
    out = []
    i, n = 0, len(sql)
    upper = sql.upper()
    fn = fname.upper()
    while i < n:
        j = upper.find(fn + "(", i)
        if j < 0:
            out.append(sql[i:])
            break
        # kelime sınırı: önceki karakter tanımlayıcı olmasın
        if j > 0 and (sql[j - 1].isalnum() or sql[j - 1] in "_$#."):
            out.append(sql[i:j + len(fn)])
            i = j + len(fn)
            continue
        # dengeli kapanışı bul
        depth, k, in_str = 0, j + len(fn), False
        end = -1
        while k < n:
            c = sql[k]
            if in_str:
                if c == "'":
                    if k + 1 < n and sql[k + 1] == "'":
                        k += 1
                    else:
                        in_str = False
            elif c == "'":
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            k += 1
        if end < 0:                      # bozuk SQL — dokunma
            out.append(sql[i:])
            break
        inner = sql[j + len(fn) + 1:end]
        args = _split_top_level_args(inner)
        res = transform(args, sql, end)
        if res is None:
            out.append(sql[i:end + 1])
            i = end + 1
        else:
            replacement, new_end = res
            out.append(sql[i:j])
            out.append(replacement)
            i = new_end + 1
    return "".join(out)


_ROWNUM_RE = re.compile(r"\)\s*WHERE\s+ROWNUM\s*<=\s*(\d+)", re.IGNORECASE)
_NVL_RE = re.compile(r"\bNVL\b", re.IGNORECASE)
_FROM_DUAL_RE = re.compile(r"\s+FROM\s+DUAL\b", re.IGNORECASE)

# Oracle tarih format maskesi → strftime (yalnız kullandığımız parçalar).
_TOCHAR_FMT = [("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"),
               ("HH24", "%H"), ("MI", "%M"), ("SS", "%S")]


def _tochar(args, sql, end):
    if len(args) == 1:
        return (f"CAST({args[0]} AS VARCHAR)", end)
    if len(args) == 2:
        m = re.fullmatch(r"'([^']*)'", args[1])
        if not m:
            return None
        fmt = m.group(1)
        for ora, duck_ in _TOCHAR_FMT:
            fmt = fmt.replace(ora, duck_)
        return (f"strftime({args[0]}, '{fmt}')", end)
    return None


def _tonumber(args, sql, end):
    if len(args) != 1:
        return None
    return (f"TRY_CAST({args[0]} AS DOUBLE)", end)


def _regexp_substr(args, sql, end):
    # 2-arg: REGEXP_SUBSTR(x, 'pat') → regexp_extract(x, 'pat')
    if len(args) == 2:
        return (f"regexp_extract({args[0]}, {args[1]})", end)
    # 6-arg: (x, 'pat', pos, occ, 'i', grp) → regexp_extract(x, '(?i)pat', grp)
    if len(args) == 6:
        pat = args[1]
        flags = args[4].strip().strip("'").lower()
        if "i" in flags and pat.startswith("'"):
            pat = "'(?i)" + pat[1:]
        return (f"regexp_extract({args[0]}, {pat}, {args[5]})", end)
    return None


def _ratio_to_report(args, sql, end):
    # RATIO_TO_REPORT(e) OVER (w) → (e) / SUM(e) OVER (w)
    if len(args) != 1:
        return None
    m = re.match(r"\s*OVER\s*\(", sql[end + 1:], re.IGNORECASE)
    if not m:
        return None
    # OVER penceresinin dengeli kapanışı
    k = end + 1 + m.end()
    depth, in_str = 1, False
    n = len(sql)
    while k < n and depth > 0:
        c = sql[k]
        if in_str:
            if c == "'":
                in_str = False
        elif c == "'":
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        k += 1
    window = sql[end + 1 + m.end():k - 1]
    e = args[0]
    return (f"({e}) / SUM({e}) OVER ({window})", k - 1)


def oracle_sql_to_duckdb(sql: str) -> str:
    """Importer SQL'lerinin kullandığı Oracle yapılarını DuckDB'ye çevir."""
    out = _NVL_RE.sub("COALESCE", sql or "")
    out = _FROM_DUAL_RE.sub("", out)
    out = _ROWNUM_RE.sub(r") LIMIT \1", out)
    out = _rewrite_calls(out, "RATIO_TO_REPORT", _ratio_to_report)
    out = _rewrite_calls(out, "TO_CHAR", _tochar)
    out = _rewrite_calls(out, "TO_NUMBER", _tonumber)
    out = _rewrite_calls(out, "REGEXP_SUBSTR", _regexp_substr)
    return out

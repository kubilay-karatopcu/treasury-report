"""Alt-process giriş noktası — bir Python transform script'ini KISITLI bir
namespace'te çalıştırır. Asla doğrudan import edilmez; yalnız
:func:`presentations.python_runtime.executor.run_python_transform` tarafından
``python -m presentations.python_runtime._runner …`` olarak çağrılır.

argv: ``<code_path> <input_parquet> <output_parquet> <error_json>``

Başarılıysa output parquet'i yazıp 0 ile çıkar. Ele alınan bir hata varsa
error JSON'ı yazıp 3 ile çıkar. rlimit/timeout ile öldürülürse parent süreç
returncode'dan anlar (bu dosya çalışmaz).

NOT: rlimit'ler parent'taki ``preexec_fn`` ile zaten kuruldu — burada yeniden
kurmuyoruz. Bu modül kısıtlı builtins + import-allowlist'i UYGULAR (derinlemesine
savunma; statik validator zaten parent'ta çalıştı).
"""
from __future__ import annotations

import builtins as _builtins
import json
import sys
import traceback


def _fail(err_path: str, message: str, detail: str = "") -> None:
    try:
        with open(err_path, "w", encoding="utf-8") as fh:
            json.dump({"error": message, "detail": detail}, fh)
    except Exception:
        pass
    sys.exit(3)


# İçe aktarmaya izinli kök modüller (validator ile aynı liste — import burada
# import edilmesin diye sabiti elle tutuyoruz; tek kaynak validator ama runtime
# bağımlılığını minimumda tutmak için kopyalandı, ikisi de testle senkron).
_ALLOWED_IMPORT_ROOTS = {
    "pandas", "numpy", "math", "datetime", "re", "statistics",
    "json", "collections", "itertools", "functools", "decimal", "random",
}

# Kullanıcı koduna açılan güvenli builtin alt kümesi. eval/exec/open/__import__
# /getattr vb. KASITLI olarak yok.
_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash",
    "hex", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
    "max", "min", "next", "object", "oct", "ord", "pow", "print", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "type", "zip", "True", "False", "None",
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "ZeroDivisionError", "ArithmeticError", "RuntimeError", "StopIteration",
)


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level and level > 0:
        raise ImportError("göreli import yasak")
    root = name.split(".")[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"import yasak: {name!r}")
    return __import__(name, globals, locals, fromlist, level)


def _build_safe_builtins() -> dict:
    safe = {n: getattr(_builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(_builtins, n)}
    safe["__import__"] = _safe_import
    return safe


def main() -> None:
    if len(sys.argv) != 5:
        sys.stderr.write("usage: _runner <code> <in_parquet> <out_parquet> <err_json>\n")
        sys.exit(2)
    code_path, in_path, out_path, err_path = sys.argv[1:5]

    import pandas as pd

    try:
        with open(code_path, "r", encoding="utf-8") as fh:
            code = fh.read()
    except Exception as exc:  # pragma: no cover - parent yazıyor
        _fail(err_path, f"Script okunamadı: {exc}")

    # Derinlemesine savunma: parent'ta geçmiş olsa da yeniden denetle.
    try:
        from presentations.python_runtime.validator import validate_python
        v = validate_python(code)
        if not v.ok:
            _fail(err_path, "Script reddedildi: " + "; ".join(v.errors))
    except ImportError:
        # Paket subprocess'te import edilemiyorsa (PYTHONPATH eksik) — yine de
        # kısıtlı builtins koruması devrede; statik denetimi parent yapmıştı.
        pass

    try:
        input_node_df = pd.read_parquet(in_path)
    except Exception as exc:
        _fail(err_path, f"Giriş verisi okunamadı: {exc}")

    namespace: dict = {
        "__builtins__": _build_safe_builtins(),
        "input_node_df": input_node_df,
        "pd": pd,
    }
    try:
        import numpy as np
        namespace["np"] = np
    except Exception:  # pragma: no cover
        pass

    try:
        exec(compile(code, "<user_script>", "exec"), namespace, namespace)
    except Exception as exc:
        tb = traceback.format_exc(limit=6)
        # Kullanıcıya dosya yollarını sızdırmamak için <user_script> dışını ele.
        _fail(err_path, f"{type(exc).__name__}: {exc}", detail=tb)

    if "output_node_df" not in namespace:
        _fail(err_path, "Script sonunda 'output_node_df' tanımlı değil.")

    out = namespace["output_node_df"]
    if not isinstance(out, pd.DataFrame):
        _fail(
            err_path,
            f"'output_node_df' bir DataFrame olmalı (bulunan tip: {type(out).__name__}).",
        )

    try:
        out.to_parquet(out_path, index=False)
    except Exception as exc:
        _fail(err_path, f"Çıktı yazılamadı: {exc}")

    sys.exit(0)


if __name__ == "__main__":
    main()

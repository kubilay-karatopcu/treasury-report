"""Faz P — Python transform runtime.

Bir ``kind: "python"`` node'unun kullanıcı/LLM tarafından yazılmış script'ini
GÜVENLİ biçimde çalıştırmak için iki katman:

- :mod:`presentations.python_runtime.validator` — statik AST whitelist. Import /
  eval / exec / open / dunder erişimi gibi kaçış vektörlerini KOD ÇALIŞMADAN
  reddeder. ``sql.validator.validate_sql``'in Python muadili.
- :mod:`presentations.python_runtime.executor` — ayrı bir alt-process'te, CPU +
  bellek rlimit'i ve duvar-saati timeout'u ile çalıştırır. Girişi ``input_node_df``
  olarak verir, çıkışta ``output_node_df`` (DataFrame) bekler.

Sözleşme (Faz P): script ``input_node_df`` adlı bir pandas DataFrame görür ve
sonunda ``output_node_df`` adlı bir pandas DataFrame üretmek zorundadır. Başka
hiçbir node'un verisi enjekte edilmez — izolasyon yapısaldır.
"""
from presentations.python_runtime.validator import (
    PythonValidationResult,
    validate_python,
    ALLOWED_IMPORT_ROOTS,
    INPUT_DF_NAME,
    OUTPUT_DF_NAME,
)
from presentations.python_runtime.executor import (
    PythonRunResult,
    run_python_transform,
)

__all__ = [
    "PythonValidationResult",
    "validate_python",
    "ALLOWED_IMPORT_ROOTS",
    "INPUT_DF_NAME",
    "OUTPUT_DF_NAME",
    "PythonRunResult",
    "run_python_transform",
]

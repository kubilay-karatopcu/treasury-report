"""Statik AST whitelist — Faz P (``sql.validator`` muadili).

Bir Python transform script'ini ÇALIŞTIRMADAN denetler. Amaç, servis hesabıyla
çalışacak keyfi kodun en bariz kaçış vektörlerini kapatmaktır. Bu katman tek
başına yeterli DEĞİLDİR — :mod:`presentations.python_runtime.executor` ayrıca
subprocess + rlimit + kısıtlı builtins ile derinlemesine savunma uygular. İkisi
birlikte v0 güvenlik modelini oluşturur (karar: AST whitelist + subprocess +
rlimit).

Kurallar:

1. Kod ``ast.parse`` ile ayrıştırılabilmeli (syntax error → red).
2. ``import`` / ``from … import`` yalnız :data:`ALLOWED_IMPORT_ROOTS` kök
   modüllerinden olabilir. ``from x import *`` her durumda yasak.
3. Yasaklı isimlere çağrı/erişim yok: ``eval``, ``exec``, ``compile``, ``open``,
   ``__import__``, ``input``, ``globals``, ``locals``, ``vars``, ``getattr``,
   ``setattr``, ``delattr``, ``memoryview``, ``breakpoint``, ``help``, ``exit``,
   ``quit``.
4. Dunder attribute/isim erişimi yok (``__class__``, ``__globals__``,
   ``__subclasses__``, ``__builtins__`` … klasik sandbox kaçışı).
5. ``with`` içinde dosya açma vb. zaten (2)+(3) ile kapalı; ek bir kural yok.

Tasarım gereği SQL validator'ı gibi: veritabanı/dosya erişmez, saf statik.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Çalışma anında script'in göreceği / üreteceği DataFrame adları (sözleşme).
INPUT_DF_NAME = "input_node_df"
OUTPUT_DF_NAME = "output_node_df"

# İçe aktarmaya izin verilen kök modüller. socket / os / sys / subprocess /
# pathlib / importlib gibi her şey KASITLI olarak dışarıda — bu, kullanıcı
# kodundan ağ/dosya/process erişimini büyük ölçüde kapatır.
ALLOWED_IMPORT_ROOTS = frozenset({
    "pandas", "numpy", "math", "datetime", "re", "statistics",
    "json", "collections", "itertools", "functools", "decimal", "random",
})

# Erişimi/çağrısı yasak builtin isimleri.
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "__import__", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
    "breakpoint", "help", "exit", "quit", "copyright", "credits", "license",
})


@dataclass
class PythonValidationResult:
    """Tek bir script denetim turunun sonucu (``ValidationResult`` muadili)."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


class _Auditor(ast.NodeVisitor):
    """AST'yi gezip yasak yapıları toplar."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    # ── importlar ────────────────────────────────────────────────────────
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                self.errors.append(
                    f"import yasak: '{alias.name}' (izinli kökler: "
                    f"{', '.join(sorted(ALLOWED_IMPORT_ROOTS))})"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if node.level and node.level > 0:
            self.errors.append("göreli import (from . import …) yasak")
        elif root not in ALLOWED_IMPORT_ROOTS:
            self.errors.append(
                f"import yasak: 'from {node.module}' (izinli kökler: "
                f"{', '.join(sorted(ALLOWED_IMPORT_ROOTS))})"
            )
        for alias in node.names:
            if alias.name == "*":
                self.errors.append("'from … import *' yasak")
        self.generic_visit(node)

    # ── isim erişimi ─────────────────────────────────────────────────────
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self.errors.append(f"yasak isim kullanıldı: '{node.id}'")
        elif _is_dunder(node.id):
            self.errors.append(f"dunder isim erişimi yasak: '{node.id}'")
        self.generic_visit(node)

    # ── attribute erişimi (().__class__... kaçışı) ───────────────────────
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if _is_dunder(node.attr):
            self.errors.append(f"dunder attribute erişimi yasak: '.{node.attr}'")
        self.generic_visit(node)


def validate_python(code: str) -> PythonValidationResult:
    """``code``'u Faz P whitelist'ine göre denetle.

    Yalnız statik denetim yapar; kodu çalıştırmaz, içe aktarmaz. ``output_node_df``
    isminin script gövdesinde GEÇİP geçmediğini de kontrol eder (yoksa çalıştırma
    her hâlükârda kırılacağı için erkenden uyarı/hata verir).
    """
    if not isinstance(code, str) or not code.strip():
        return PythonValidationResult(ok=False, errors=["Script boş."])

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return PythonValidationResult(
            ok=False, errors=[f"Söz dizimi hatası: {exc.msg} (satır {exc.lineno})"]
        )

    auditor = _Auditor()
    auditor.visit(tree)

    errors = list(auditor.errors)
    warnings = list(auditor.warnings)

    # output_node_df bir yere ATANIYOR mu? (Tespit gevşek: isim hedef olarak
    # geçiyorsa yeterli — çalışma anında executor kesin kontrolü yapar.)
    assigns_output = any(
        isinstance(n, ast.Name) and n.id == OUTPUT_DF_NAME and isinstance(n.ctx, ast.Store)
        for n in ast.walk(tree)
    )
    if not assigns_output:
        errors.append(
            f"Script sonunda '{OUTPUT_DF_NAME}' adında bir DataFrame üretilmeli "
            f"(ör. `{OUTPUT_DF_NAME} = {INPUT_DF_NAME}.head(10)`)."
        )

    return PythonValidationResult(ok=not errors, errors=errors, warnings=warnings)

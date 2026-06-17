"""Subprocess sandbox — Faz P.

Bir Python transform script'ini ayrı bir alt-process'te çalıştırır:

- giriş DataFrame'i geçici bir parquet'e yazılır, script ``input_node_df`` olarak
  görür;
- alt-process CPU rlimit'i (``RLIMIT_CPU``) + adres-uzayı rlimit'i (``RLIMIT_AS``)
  ile başlatılır (``preexec_fn`` — POSIX);
- duvar-saati timeout'u :func:`subprocess.run` ile uygulanır;
- script ``output_node_df`` üretirse parquet'ten okunup DataFrame döner.

Karar gereği (AST whitelist + subprocess + rlimit): bu, in-process exec'in
kaçış/kaynak-tüketim risklerini sınırlar. Ağ izolasyonu best-effort'tur — import
allowlist'i ``socket``/``urllib`` vb. dışarıda bıraktığı için kullanıcı kodundan
ağ erişimi pratikte kapalıdır.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Bu modül: .../presentations/python_runtime/executor.py
# Paket kökü (presentations'ın EBEVEYNİ) subprocess'in PYTHONPATH'ine eklenir ki
# `python -m presentations.python_runtime._runner` import edilebilsin.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

DEFAULT_CPU_SECONDS = 30
DEFAULT_MEM_MB = 2048
DEFAULT_WALL_TIMEOUT = 60
_MAX_STDOUT_CHARS = 20_000


@dataclass
class PythonRunResult:
    """Bir transform çalıştırmasının sonucu."""

    ok: bool
    df: Any | None = None          # pandas.DataFrame | None (import'u çağırana bırak)
    error: str | None = None
    detail: str | None = None      # traceback / ek bağlam (UI'da gizli/expandable)
    stdout: str = ""
    row_count: int | None = None
    columns: list[str] | None = None


def _preexec_limits(cpu_seconds: int, mem_bytes: int):
    """Alt-process fork'undan SONRA, exec'ten ÖNCE çalışır (yalnız POSIX)."""
    def _apply() -> None:  # pragma: no cover - alt-process içinde çalışır
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        if mem_bytes > 0:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except (ValueError, OSError):
                # Bazı ortamlarda RLIMIT_AS düşürülemiyor — CPU + wall timeout
                # yine de koruyor.
                pass
    return _apply


def run_python_transform(
    code: str,
    input_df: Any,
    *,
    cpu_seconds: int = DEFAULT_CPU_SECONDS,
    mem_mb: int = DEFAULT_MEM_MB,
    wall_timeout: int = DEFAULT_WALL_TIMEOUT,
) -> PythonRunResult:
    """``code``'u ``input_df`` üzerinde sandbox'ta çalıştır.

    Statik denetim ÇAĞIRANIN sorumluluğu değildir — burada da yapılır (runner
    içinde) ama hızlı geri-bildirim için önce parent'ta da denetlenir. Dönen
    :class:`PythonRunResult` UI'ya/preview'a doğrudan servis edilebilir.
    """
    import pandas as pd  # parent süreçte zaten yüklü

    from presentations.python_runtime.validator import validate_python

    v = validate_python(code)
    if not v.ok:
        return PythonRunResult(ok=False, error="; ".join(v.errors))

    posix = os.name == "posix"
    mem_bytes = int(mem_mb) * 1024 * 1024 if mem_mb else 0

    with tempfile.TemporaryDirectory(prefix="pyrt_") as tmp:
        tmp_path = Path(tmp)
        code_path = tmp_path / "script.py"
        in_path = tmp_path / "in.parquet"
        out_path = tmp_path / "out.parquet"
        err_path = tmp_path / "err.json"

        code_path.write_text(code, encoding="utf-8")
        try:
            (input_df if input_df is not None else pd.DataFrame()).to_parquet(
                in_path, index=False
            )
        except Exception as exc:
            return PythonRunResult(ok=False, error=f"Giriş verisi hazırlanamadı: {exc}")

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = _REPO_ROOT + (os.pathsep + existing if existing else "")

        cmd = [
            sys.executable, "-m", "presentations.python_runtime._runner",
            str(code_path), str(in_path), str(out_path), str(err_path),
        ]
        preexec = _preexec_limits(cpu_seconds, mem_bytes) if posix else None

        try:
            proc = subprocess.run(
                cmd, env=env, capture_output=True, text=True,
                timeout=wall_timeout, preexec_fn=preexec,
            )
        except subprocess.TimeoutExpired:
            return PythonRunResult(
                ok=False,
                error=f"Script zaman aşımına uğradı ({wall_timeout}s).",
            )

        stdout = (proc.stdout or "")[:_MAX_STDOUT_CHARS]

        if proc.returncode == 0 and out_path.exists():
            try:
                df = pd.read_parquet(out_path)
            except Exception as exc:
                return PythonRunResult(
                    ok=False, error=f"Çıktı okunamadı: {exc}", stdout=stdout
                )
            return PythonRunResult(
                ok=True, df=df, stdout=stdout,
                row_count=int(len(df)), columns=[str(c) for c in df.columns],
            )

        # Ele alınan hata — runner err.json yazdı.
        if err_path.exists():
            try:
                payload = json.loads(err_path.read_text(encoding="utf-8"))
                return PythonRunResult(
                    ok=False, error=payload.get("error", "Bilinmeyen hata"),
                    detail=payload.get("detail") or None, stdout=stdout,
                )
            except Exception:
                pass

        # rlimit/sinyalle öldürüldü ya da beklenmedik çıkış.
        rc = proc.returncode
        if rc is not None and rc < 0:
            sig = -rc
            if sig in (24,):  # SIGXCPU
                msg = f"Script CPU limitini aştı ({cpu_seconds}s)."
            elif sig in (9,):  # SIGKILL — genelde bellek limiti / OOM
                msg = "Script öldürüldü (muhtemelen bellek limiti aşıldı)."
            else:
                msg = f"Script sinyalle sonlandı (signal {sig})."
        else:
            msg = (proc.stderr or "Script beklenmedik şekilde sonlandı.").strip()[:2000]
        return PythonRunResult(ok=False, error=msg, stdout=stdout)

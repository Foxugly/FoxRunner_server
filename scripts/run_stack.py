r"""One-command launcher for the FoxRunner backend stack (cross-platform).

Starts every backend process the deployment needs and stops them all together
on Ctrl-C (or when any of them exits). What it starts adapts to the platform:

  - API server         : always. `manage.py runserver` by default; gunicorn when
                         RUN_GUNICORN=true (Linux only — gunicorn has no Windows
                         support).
  - CLI scheduler      : always, supervised (relaunches itself on crash).
  - Celery worker+beat : only when Celery is enabled. RUN_CELERY defaults ON on
                         Linux, OFF on Windows (Redis/Celery are typically not
                         available there). Redis itself is infra — start it
                         separately (e.g. `docker compose up -d redis`).

Env overrides:
  HOST=127.0.0.1:8000       server bind address
  RUN_CELERY=true|false     force-enable/disable worker+beat
  RUN_GUNICORN=true         serve via gunicorn instead of runserver (Linux)

Usage:
  python scripts/run_stack.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_IS_WIN = sys.platform.startswith("win")


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _venv_bin(name: str) -> str:
    """Resolve a console script (celery/gunicorn) next to the running python."""
    candidate = Path(sys.executable).parent / (f"{name}.exe" if _IS_WIN else name)
    return str(candidate) if candidate.exists() else name


def _build_commands() -> list[tuple[str, list[str]]]:
    host = os.getenv("HOST", "127.0.0.1:8000")
    py = sys.executable
    cmds: list[tuple[str, list[str]]] = []

    if _flag("RUN_GUNICORN", False) and not _IS_WIN:
        cmds.append(("server", [_venv_bin("gunicorn"), "foxrunner.wsgi:application", "--bind", host, "--workers", "2"]))
    else:
        cmds.append(("server", [py, str(_REPO / "manage.py"), "runserver", host]))

    cmds.append(("scheduler", [py, str(_REPO / "scripts" / "run_scheduler_supervised.py")]))

    if _flag("RUN_CELERY", default=not _IS_WIN):
        worker = [_venv_bin("celery"), "-A", "foxrunner.celery_app", "worker", "--loglevel=INFO"]
        if _IS_WIN:
            worker.append("--pool=solo")
        cmds.append(("worker", worker))
        cmds.append(("beat", [_venv_bin("celery"), "-A", "foxrunner.celery_app", "beat", "--loglevel=INFO"]))

    return cmds


def main() -> int:
    cmds = _build_commands()
    print(f"[run_stack] launching: {', '.join(name for name, _ in cmds)}  (Ctrl-C stops all)")
    procs = [(name, subprocess.Popen(cmd, cwd=str(_REPO))) for name, cmd in cmds]
    try:
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"[run_stack] '{name}' exited ({code}) — stopping the rest")
                    return code or 0
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[run_stack] Ctrl-C — stopping all")
        return 0
    finally:
        for _name, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for _name, proc in procs:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

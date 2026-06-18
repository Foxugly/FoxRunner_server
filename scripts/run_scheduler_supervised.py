r"""Supervised launcher for the FoxRunner CLI scheduler.

The scheduler (``python main.py`` → ``SchedulerService.loop``) is a long-running
process that drives Selenium scenarios on their slots. Its own loop already
retries transient errors, but if the *process* dies (unhandled crash, OOM, a
closed terminal session) it stays down — which is the "le scheduleur a planté"
incident this guards against.

This wrapper runs the scheduler and **relaunches it on any unexpected exit**
(non-zero / crash) with capped exponential backoff, so an in-session crash
self-heals. A clean exit (code 0, e.g. Ctrl-C / operator stop) ends supervision.

For boot persistence (machine reboot, logoff) register THIS script under an OS
supervisor — see docs/OPERATIONS.md "Scheduler auto-restart":
  - Windows Task Scheduler: action ``…\.venv\Scripts\python.exe`` arg
    ``scripts\run_scheduler_supervised.py``, trigger "At startup", settings
    "If the task fails, restart every 1 minute".
  - NSSM:  nssm install FoxRunnerScheduler <python> <repo>\scripts\run_scheduler_supervised.py
  - systemd: ExecStart=<python> scripts/run_scheduler_supervised.py + Restart=always

Any CLI args are passed through to main.py (e.g. ``--dry-run``).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN = _REPO_ROOT / "main.py"

_INITIAL_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 60
# A run that lasted at least this long is considered "healthy", so the backoff
# resets — a crash after hours of uptime restarts fast, a crash loop backs off.
_HEALTHY_RUNTIME_SECONDS = 300


def main() -> int:
    passthrough = sys.argv[1:]
    backoff = _INITIAL_BACKOFF_SECONDS
    print(f"[supervisor] supervising scheduler: {sys.executable} {_MAIN} {' '.join(passthrough)}")

    while True:
        started = time.monotonic()
        completed = subprocess.run([sys.executable, str(_MAIN), *passthrough], cwd=str(_REPO_ROOT))
        ran_seconds = time.monotonic() - started
        code = completed.returncode

        if code == 0:
            print("[supervisor] scheduler exited cleanly (0) — stopping supervision.")
            return 0

        if ran_seconds >= _HEALTHY_RUNTIME_SECONDS:
            backoff = _INITIAL_BACKOFF_SECONDS

        print(
            f"[supervisor] scheduler exited with code {code} after {int(ran_seconds)}s "
            f"— restarting in {backoff}s",
            flush=True,
        )
        time.sleep(backoff)
        backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())

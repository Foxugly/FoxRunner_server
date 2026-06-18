r"""Dev helper: run the API server AND the supervised scheduler in one terminal.

Spawns the supervised scheduler as a child process, then runs Django's
``runserver`` in the foreground. Ctrl-C stops both. This is a local-dev
convenience so you don't need a second terminal for the scheduler — in
production run them as separate supervised services (see docs/OPERATIONS.md).

Usage:
    python scripts/run_dev.py            # serves on 127.0.0.1:8000
    python scripts/run_dev.py 0.0.0.0:8000
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:8000"
    print("[run_dev] starting supervised scheduler + API server (Ctrl-C stops both)")
    scheduler = subprocess.Popen(
        [sys.executable, str(_REPO / "scripts" / "run_scheduler_supervised.py")],
        cwd=str(_REPO),
    )
    try:
        return subprocess.run(
            [sys.executable, str(_REPO / "manage.py"), "runserver", host],
            cwd=str(_REPO),
        ).returncode
    finally:
        scheduler.terminate()
        try:
            scheduler.wait(timeout=10)
        except subprocess.TimeoutExpired:
            scheduler.kill()


if __name__ == "__main__":
    raise SystemExit(main())

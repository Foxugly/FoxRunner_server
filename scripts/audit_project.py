from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKS = (
    ("docs", [sys.executable, "scripts/check_docs.py"]),
    ("openapi", [sys.executable, "scripts/check_openapi.py"]),
    ("env-example", [sys.executable, "scripts/check_env_example.py"]),
)


def main() -> int:
    failures = 0
    for name, command in CHECKS:
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print(f"{name}:failed")
            failures += 1
        else:
            print(f"{name}:ok")
    for path in (".env.example", "alembic.ini", "openapi.json", "CHANGELOG.md"):
        if not Path(path).exists():
            print(f"missing:{path}")
            failures += 1
    migration_files = sorted(Path("migrations/versions").glob("*.py"))
    if not migration_files:
        print("missing:migrations")
        failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

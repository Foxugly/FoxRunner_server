from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKS = (
    ("docs", [sys.executable, "scripts/check_docs.py"]),
    ("openapi", [sys.executable, "scripts/check_openapi.py"]),
    ("env-example", [sys.executable, "scripts/check_env_example.py"]),
)

REQUIRED_FILES = (
    ".env.example",
    "openapi.json",
    "CHANGELOG.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    "manage.py",
)

# Minimum supported runtime — aligned with pyproject.toml target-version
# and the CI matrix.
MIN_PYTHON = (3, 12)


def _run_subprocess_checks() -> int:
    failures = 0
    for name, command in CHECKS:
        # Suppress stdout from child checks; audit_project is the final
        # aggregator and should produce its own single-line summary per check.
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"{name}:failed")
            if result.stdout.strip():
                print(result.stdout.strip())
            if result.stderr.strip():
                print(result.stderr.strip())
            failures += 1
        else:
            print(f"{name}:ok")
    return failures


def _check_required_files() -> int:
    failures = 0
    for path in REQUIRED_FILES:
        if not Path(path).exists():
            print(f"missing:{path}")
            failures += 1
        else:
            print(f"file:{path}:ok")
    return failures


def _check_python_version() -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"python-version:failed minimum={MIN_PYTHON[0]}.{MIN_PYTHON[1]} actual={sys.version_info[0]}.{sys.version_info[1]}")
        return 1
    print(f"python-version:ok {sys.version_info[0]}.{sys.version_info[1]}")
    return 0


def _check_django_migrations() -> int:
    apps = ("accounts", "catalog", "ops")
    failures = 0
    for app in apps:
        versions_dir = Path(app) / "migrations"
        if not versions_dir.exists():
            print(f"missing:{app}/migrations")
            failures += 1
            continue
        migration_files = sorted(p for p in versions_dir.glob("*.py") if p.name != "__init__.py")
        if not migration_files:
            print(f"empty:{app}/migrations")
            failures += 1
            continue
        print(f"django-migrations:{app}:ok files={len(migration_files)}")
    return failures


def main() -> int:
    failures = 0
    failures += _run_subprocess_checks()
    failures += _check_required_files()
    failures += _check_python_version()
    failures += _check_django_migrations()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

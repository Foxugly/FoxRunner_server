from __future__ import annotations

import re
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
    "alembic.ini",
    "openapi.json",
    "CHANGELOG.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
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


def _check_migration_chain() -> int:
    versions_dir = Path("migrations/versions")
    migration_files = sorted(versions_dir.glob("*.py")) if versions_dir.exists() else []
    if not migration_files:
        print("missing:migrations")
        return 1

    revision_re = re.compile(r"^revision(?:\s*:\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
    down_re = re.compile(r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(?:['\"]([^'\"]*)['\"]|(None))", re.MULTILINE)

    nodes: dict[str, str | None] = {}
    for path in migration_files:
        text = path.read_text(encoding="utf-8")
        revision_match = revision_re.search(text)
        down_match = down_re.search(text)
        if revision_match is None or down_match is None:
            print(f"migration-parse:failed {path.name}")
            return 1
        revision = revision_match.group(1)
        down = down_match.group(1) if down_match.group(1) else None
        nodes[revision] = down

    roots = [rev for rev, down in nodes.items() if down is None]
    heads = {rev for rev in nodes if rev not in {down for down in nodes.values() if down}}
    dangling = [rev for rev, down in nodes.items() if down is not None and down not in nodes]

    failures = 0
    if len(roots) != 1:
        print(f"migration-roots:failed count={len(roots)} expected=1")
        failures += 1
    if len(heads) != 1:
        print(f"migration-heads:failed count={len(heads)} heads={sorted(heads)}")
        failures += 1
    if dangling:
        print(f"migration-dangling:failed {dangling}")
        failures += 1
    if not failures:
        print(f"migration-chain:ok revisions={len(nodes)}")
    return failures


def main() -> int:
    failures = 0
    failures += _run_subprocess_checks()
    failures += _check_required_files()
    failures += _check_python_version()
    failures += _check_migration_chain()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

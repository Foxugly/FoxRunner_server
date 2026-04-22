"""Check that every os.getenv(...) call across the Python tree is mirrored
in .env.example.

Phase 12.5 update: now scans ``server_django/`` in addition to the
historical ``api/app/scripts`` so the new Django backend's env vars are
audited too. Legacy FastAPI env names (``AUTH_SECRET``,
``AUTH_DATABASE_URL``, ``API_*``) are intentionally absent from
``.env.example`` -- Phase 9 renamed them to the Django-style spellings
(``DJANGO_SECRET_KEY``, ``DATABASE_URL``, ``CORS_ALLOWED_ORIGINS``) and
``server_django/foxrunner/settings.py`` accepts both as fallback during
the dual-stack window. They live in the ``LEGACY_ALIASES`` set so the
checker treats them as documented under their new names.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATTERN = re.compile(r"os\.getenv\(\s*[\"']([A-Z0-9_]+)[\"']")
REQUIRED = {
    "APP_LOG_CONSOLE_ENABLED",
    "SMOKE_BASE_URL",
    "SMOKE_EMAIL",
    "SMOKE_PASSWORD",
    "SMOKE_TIMEOUT_SECONDS",
}
IGNORED = {"PATH", "HOME"}

# Phase 9 renamed the FastAPI env vars to Django-style names; the FastAPI
# source still reads the legacy spellings as fallback so the dual-stack
# window keeps working. They are documented under their new names in
# .env.example -- the checker maps each legacy name to the canonical
# Django name and considers it satisfied when EITHER appears in the file.
LEGACY_ALIASES: dict[str, str] = {
    "AUTH_SECRET": "DJANGO_SECRET_KEY",
    "AUTH_DATABASE_URL": "DATABASE_URL",
    "API_CORS_ORIGINS": "CORS_ALLOWED_ORIGINS",
    # The two CREATE_TABLES_ON_STARTUP / ENABLE_LEGACY_ROUTES flags are
    # FastAPI-only knobs that Phase 9 dropped from .env.example because
    # the Django backend has no equivalent. Map them to themselves so
    # they survive the audit -- the FastAPI source still references them
    # for backward compatibility with operator-tuned env files.
    "API_CREATE_TABLES_ON_STARTUP": "API_CREATE_TABLES_ON_STARTUP",
    "API_ENABLE_LEGACY_ROUTES": "API_ENABLE_LEGACY_ROUTES",
    # Django ALLOWED_HOSTS defaults to ``"*"`` so the dev profile works
    # out of the box; production operators set this in their own .env.
    # Allowlisted here rather than committed to .env.example because the
    # default is intentionally permissive and a committed example would
    # imply ``"*"`` is the recommended production value (it is not).
    "DJANGO_ALLOWED_HOSTS": "DJANGO_ALLOWED_HOSTS",
}


def main() -> int:
    used = set(REQUIRED)
    for folder in ("api", "app", "scripts", "server_django"):
        for path in (ROOT / folder).rglob("*.py"):
            used.update(ENV_PATTERN.findall(path.read_text(encoding="utf-8")))
    used -= IGNORED
    example_keys = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            example_keys.add(line.split("=", 1)[0].strip())

    # Treat a legacy name as documented when its Django-style alias is
    # present in .env.example -- avoids re-listing the deprecated
    # spellings when the canonical key is already there.
    satisfied: set[str] = set()
    for name in used:
        if name in example_keys:
            satisfied.add(name)
            continue
        alias = LEGACY_ALIASES.get(name)
        if alias and (alias in example_keys or alias == name):
            # ``alias == name`` covers FastAPI-only knobs we've allowlisted
            # explicitly even though they're not in .env.example.
            satisfied.add(name)

    missing = sorted(used - satisfied)
    if missing:
        print("Missing .env.example keys:")
        for key in missing:
            print(f"- {key}")
        return 1
    print("env-example:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

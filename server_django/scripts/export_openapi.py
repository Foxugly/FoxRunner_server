"""Dump the Ninja OpenAPI schema to ``openapi.django.json`` at the repo root.

Usage::

    ./.venv/Scripts/python.exe server_django/scripts/export_openapi.py

Output filename is intentionally distinct from ``openapi.json`` so the
dual-stack window keeps the FastAPI contract intact. Phase 13 will rename
the output to ``openapi.json`` once the Ninja API becomes the only
backend.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# server_django/scripts/export_openapi.py -> server_django -> repo root
SERVER_DJANGO = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_DJANGO.parent

# The Django settings live under ``server_django/`` so prepend that to the
# path -- the same trick ``manage.py`` uses.
for entry in (str(SERVER_DJANGO), str(REPO_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

import django  # noqa: E402

django.setup()

from foxrunner.api import api  # noqa: E402


def main() -> int:
    output = REPO_ROOT / "openapi.django.json"
    spec = api.get_openapi_schema()
    output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

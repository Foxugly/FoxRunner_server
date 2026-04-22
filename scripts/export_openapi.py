"""Dump the Ninja OpenAPI schema to ``openapi.json`` at the repo root.

Usage::

    ./.venv/Scripts/python.exe scripts/export_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# scripts/export_openapi.py -> scripts -> repo root
REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

import django  # noqa: E402

django.setup()

from foxrunner.api import api  # noqa: E402


def main() -> int:
    output = REPO_ROOT / "openapi.json"
    spec = api.get_openapi_schema()
    output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Dump the Ninja OpenAPI schema to ``openapi.json`` at the repo root.

Usage::

    ./.venv/Scripts/python.exe scripts/export_openapi.py

Post-processing: Ninja's ``@api.exception_handler`` doesn't propagate to
the OpenAPI spec, so every endpoint would otherwise be silent about its
error shape even though ``foxrunner.exception_handlers`` returns a
strict ``{code, message, details}`` envelope at runtime. This script
augments the generated spec by:

- Registering ``ErrorOut`` in ``components.schemas`` (Ninja already does
  this when the schema is referenced anywhere; we add it explicitly so
  the post-processing always has something to point at).
- For every operation, adding a ``default`` response that references
  ``ErrorOut`` if the route doesn't already declare one. The frontend
  ``gen:api`` then types every error response as ``ErrorOut``.
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
    # FoxrunnerNinjaAPI.get_openapi_schema applies the ErrorOut augmentation
    # already, so the file dump matches the live /api/v1/openapi.json byte
    # for byte. No extra post-processing needed here.
    spec = api.get_openapi_schema()
    output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

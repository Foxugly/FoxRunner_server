"""Verify the committed ``openapi.json`` matches the live Ninja schema.

Run this after editing any router so the OpenAPI contract stays in sync
with what the Angular client downloads via ``gen:api``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")

import django  # noqa: E402

django.setup()

from foxrunner.api import api  # noqa: E402


def main() -> int:
    expected_path = REPO_ROOT / "openapi.json"
    if not expected_path.exists():
        print("openapi.json is missing")
        return 1

    # FoxrunnerNinjaAPI.get_openapi_schema already applies the ErrorOut
    # augmentation, matching the file dump from scripts/export_openapi.py.
    spec = api.get_openapi_schema()
    generated = json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    expected = expected_path.read_text(encoding="utf-8")
    if generated != expected:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as handle:
            handle.write(generated)
            temp_path = handle.name
        print(f"openapi.json is stale. Regenerate with scripts/export_openapi.py. Fresh file: {temp_path}")
        return 1

    payload = json.loads(expected)
    paths = payload.get("paths", {})
    # The /api/v1 prefix is the Ninja contract enforced for the Angular client.
    if not any(key.startswith("/api/v1/") for key in paths):
        print("OpenAPI contract is missing /api/v1 routes")
        return 1
    print("openapi:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

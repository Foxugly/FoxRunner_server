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
from foxrunner.serializers import ErrorOut  # noqa: E402

ERROR_SCHEMA_NAME = "ErrorOut"


def _ensure_error_schema(spec: dict) -> None:
    """Add ErrorOut to components.schemas (Ninja-style serialisation)."""
    components = spec.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    if ERROR_SCHEMA_NAME in schemas:
        return
    # Generate the JSON-schema for ErrorOut via Pydantic (Ninja Schema is a
    # Pydantic BaseModel under the hood).
    raw = ErrorOut.model_json_schema(ref_template="#/components/schemas/{model}")
    raw.setdefault("title", ERROR_SCHEMA_NAME)
    schemas[ERROR_SCHEMA_NAME] = raw


def _attach_default_error_response(spec: dict) -> None:
    """Add a ``default`` response pointing at ErrorOut on every operation."""
    error_response = {
        "description": "Erreur applicative renvoyee par le handler global ({code, message, details}).",
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{ERROR_SCHEMA_NAME}"},
            },
        },
    }
    methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for path_item in spec.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() not in methods or not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            responses.setdefault("default", error_response)


def main() -> int:
    output = REPO_ROOT / "openapi.json"
    raw_spec = api.get_openapi_schema()
    # api.get_openapi_schema() returns a Pydantic model. Round-trip through
    # JSON to get a plain dict we can freely mutate (setdefault, insertion,
    # etc.) before re-serialising.
    spec = json.loads(json.dumps(raw_spec, default=str))
    _ensure_error_schema(spec)
    _attach_default_error_response(spec)
    output.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""OpenAPI post-processing applied to every spec the project emits.

Ninja's ``@api.exception_handler`` decorators run at request time and never
reach the OpenAPI generator, so without this layer the live spec at
``/api/v1/openapi.json`` would be silent about the global
``{code, message, details}`` error envelope produced by
``foxrunner.exception_handlers``.

Two helpers are exported:

- ``ensure_error_schema`` registers ``ErrorOut`` in
  ``components.schemas``.
- ``attach_default_error_response`` attaches a ``default`` response on
  every operation pointing at that schema.

Both are applied:

- by ``foxrunner.api.FoxrunnerNinjaAPI.get_openapi_schema`` for the live
  endpoint (so curl ``/openapi.json`` sees the augmentation immediately);
- by ``scripts/export_openapi.py`` and ``scripts/check_openapi.py`` for
  the file dump used by the frontend's ``gen:api`` workflow and the CI
  drift guard.

Keeping a single source of truth means every consumer sees the same
augmented spec.
"""

from __future__ import annotations

from typing import Any

from foxrunner.serializers import ErrorOut

ERROR_SCHEMA_NAME = "ErrorOut"
_ERROR_DESCRIPTION = "Erreur applicative renvoyee par le handler global ({code, message, details})."
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


def ensure_error_schema(spec: dict[str, Any]) -> None:
    """Register ``ErrorOut`` under ``components.schemas`` if absent."""
    components = spec.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    if ERROR_SCHEMA_NAME in schemas:
        return
    raw = ErrorOut.model_json_schema(ref_template="#/components/schemas/{model}")
    raw.setdefault("title", ERROR_SCHEMA_NAME)
    schemas[ERROR_SCHEMA_NAME] = raw


def attach_default_error_response(spec: dict[str, Any]) -> None:
    """Attach a ``default`` response referencing ``ErrorOut`` on every op.

    Existing explicit ``default`` declarations on individual routes are
    respected (``setdefault`` semantics).
    """
    error_response = {
        "description": _ERROR_DESCRIPTION,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{ERROR_SCHEMA_NAME}"},
            },
        },
    }
    for path_item in spec.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            responses.setdefault("default", error_response)

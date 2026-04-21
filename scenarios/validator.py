from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def validate_json_document(data: dict, schema_path: Path, filename: str) -> None:
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path)
    detail = f"{path}: {first.message}" if path else first.message
    raise ValueError(f"{filename}: schema JSON invalide: {detail}")

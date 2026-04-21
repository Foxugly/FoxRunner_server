from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.main import app


def main() -> int:
    generated = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    expected_path = Path("openapi.json")
    if not expected_path.exists():
        print("openapi.json is missing")
        return 1
    expected = expected_path.read_text(encoding="utf-8")
    if generated != expected:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as handle:
            handle.write(generated)
            temp_path = handle.name
        print(f"openapi.json is stale. Regenerate with scripts/export_openapi.py. Fresh file: {temp_path}")
        return 1
    payload = json.loads(expected)
    paths = payload.get("paths", {})
    if "/api/v1/health" not in paths or "/health" in paths:
        print("OpenAPI route contract is invalid")
        return 1
    print("openapi:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

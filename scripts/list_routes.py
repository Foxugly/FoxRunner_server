from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.main import app


def main() -> int:
    for route in sorted(app.routes, key=lambda item: getattr(item, "path", "")):
        path = getattr(route, "path", "")
        methods = ",".join(sorted(getattr(route, "methods", []) or []))
        include = getattr(route, "include_in_schema", False)
        print(f"{methods:20} {path} schema={include}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

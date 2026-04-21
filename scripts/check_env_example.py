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


def main() -> int:
    used = set(REQUIRED)
    for folder in ("api", "app", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            used.update(ENV_PATTERN.findall(path.read_text(encoding="utf-8")))
    used -= IGNORED
    example_keys = set()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            example_keys.add(line.split("=", 1)[0].strip())
    missing = sorted(used - example_keys)
    if missing:
        print("Missing .env.example keys:")
        for key in missing:
            print(f"- {key}")
        return 1
    print("env-example:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

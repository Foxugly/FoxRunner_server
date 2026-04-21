from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCS = (
    "docs/API.md",
    "docs/ARCHITECTURE.md",
    "docs/ADR.md",
    "docs/ADR_TIMEZONES.md",
    "docs/CONTRIBUTING.md",
    "docs/DB.md",
    "docs/ENVIRONMENT.md",
    "docs/FRONTEND.md",
    "docs/ANGULAR_CLIENT.md",
    "docs/OPERATIONS.md",
    "docs/OBSERVABILITY.md",
    "docs/PRODUCTION.md",
    "docs/RELEASE.md",
    "docs/RUNBOOKS.md",
    "docs/SECURITY.md",
    "docs/SECURITY_CHECKLIST.md",
    "docs/TESTING.md",
    "docs/TROUBLESHOOTING.md",
    "docs/GRAPH.md",
    "SCHEMA.md",
    "CHANGELOG.md",
    "examples/api_fixtures.json",
)


def main() -> int:
    missing = [path for path in REQUIRED_DOCS if not (ROOT / path).exists()]
    if missing:
        print("Missing documentation files:")
        for path in missing:
            print(f"- {path}")
        return 1
    print("docs:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

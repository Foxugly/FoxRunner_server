from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    for directory in Path(".").rglob("__pycache__"):
        if directory.is_dir():
            shutil.rmtree(directory)
    for pattern in ("*.pyc",):
        for path in Path(".").rglob(pattern):
            path.unlink(missing_ok=True)
    for path in (Path(".coverage"), Path(".ruff_cache"), Path(".runtime/alembic_validation.db")):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for path in (
        Path(".runtime/artifacts/pages/exec-test.html"),
        Path(".runtime/artifacts/screenshots/exec-test.png"),
        Path(".runtime/pages/exec-test.html"),
        Path(".runtime/screenshots/exec-test.png"),
    ):
        path.unlink(missing_ok=True)
    print("clean:ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

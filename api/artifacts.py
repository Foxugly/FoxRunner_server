from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

ARTIFACT_KINDS = {
    "screenshots": "screenshots",
    "pages": "pages",
}


def list_artifacts(artifacts_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for kind, folder in ARTIFACT_KINDS.items():
        base = artifacts_dir / folder
        if not base.exists():
            continue
        for path in sorted(item for item in base.iterdir() if item.is_file()):
            stat = path.stat()
            rows.append(
                {
                    "kind": kind,
                    "name": path.name,
                    "size": stat.st_size,
                    "updated_at": stat.st_mtime,
                }
            )
    return rows


def artifact_response(artifacts_dir: Path, kind: str, name: str) -> FileResponse:
    if kind not in ARTIFACT_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Type d'artifact introuvable.")
    if "/" in name or "\\" in name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nom d'artifact invalide.")
    path = artifacts_dir / ARTIFACT_KINDS[kind] / name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact introuvable.")
    return FileResponse(path)


def prune_artifacts(artifacts_dir: Path, *, older_than_days: int) -> int:
    import time

    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for kind in ARTIFACT_KINDS:
        base = artifacts_dir / kind
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    return removed

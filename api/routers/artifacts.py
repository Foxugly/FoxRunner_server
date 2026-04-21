from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.artifacts import artifact_response, list_artifacts, prune_artifacts
from api.audit import write_audit
from api.auth import User, current_active_user
from api.db import get_async_session
from api.dependencies import actor_id, get_config, require_superuser
from api.pagination import page_response
from api.schemas import ArtifactPagePayload
from app.config import AppConfig

router = APIRouter(tags=["artifacts"])


@router.get("/artifacts", response_model=ArtifactPagePayload)
async def artifacts_endpoint(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    config: AppConfig = Depends(get_config),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    artifacts = list_artifacts(config.runtime.artifacts_dir)
    return page_response(artifacts[offset : offset + limit], total=len(artifacts), limit=limit, offset=offset)


@router.get("/artifacts/{kind}/{name}")
async def artifact_download_endpoint(
    kind: str,
    name: str,
    config: AppConfig = Depends(get_config),
    current_user: User = Depends(current_active_user),
):
    require_superuser(current_user)
    return artifact_response(config.runtime.artifacts_dir, kind, name)


@router.delete("/artifacts")
async def prune_artifacts_endpoint(
    older_than_days: int = Query(default=30, ge=1),
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    removed = prune_artifacts(config.runtime.artifacts_dir, older_than_days=older_than_days)
    await write_audit(
        session,
        actor_user_id=actor_id(current_user),
        action="artifacts.prune",
        target_type="artifacts",
        target_id=str(config.runtime.artifacts_dir),
        before={"older_than_days": older_than_days},
        after={"removed": removed},
    )
    return {"removed": removed}

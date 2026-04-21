from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import count_audit, list_audit, serialize_audit
from api.auth import User, current_active_user
from api.db import get_async_session
from api.dependencies import get_config, require_superuser, serialize_user
from api.pagination import page_response
from api.schemas import (
    AdminConfigChecksPayload,
    AdminDbStatsPayload,
    AdminExportPayload,
    AdminImportDryRunPayload,
    AdminUserUpdatePayload,
    AppSettingPagePayload,
    AppSettingPayload,
    AppSettingResponsePayload,
    AuditPagePayload,
    DeletedPayload,
    RetentionPayload,
    UserPagePayload,
    UserPayload,
)
from api.services.admin import config_checks, db_stats, export_catalog, import_catalog, prune_records, remove_setting, save_setting, update_user
from api.settings import list_settings, serialize_setting
from app.config import AppConfig

router = APIRouter(tags=["admin"])


@router.get("/admin/users", response_model=UserPagePayload)
async def admin_list_users(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    total = await session.scalar(select(func.count(User.id)))
    items = [serialize_user(user) for user in await session.scalars(select(User).order_by(User.email).offset(offset).limit(limit))]
    return page_response(items, total=total or 0, limit=limit, offset=offset)


@router.patch("/admin/users/{target_user_id}", response_model=UserPayload)
async def admin_update_user(
    target_user_id: str,
    payload: AdminUserUpdatePayload,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await update_user(session, target_user_id=target_user_id, payload=payload, current_user=current_user)


@router.get("/audit", tags=["audit"], response_model=AuditPagePayload)
async def audit_log(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    items = [serialize_audit(record) for record in await list_audit(session, limit=limit, offset=offset, actor_user_id=actor_user_id, target_type=target_type, target_id=target_id)]
    total = await count_audit(session, actor_user_id=actor_user_id, target_type=target_type, target_id=target_id)
    return page_response(items, total=total, limit=limit, offset=offset)


@router.get("/admin/config-checks", response_model=AdminConfigChecksPayload)
async def admin_config_checks(
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await config_checks(session, config=config)


@router.get("/admin/db-stats", response_model=AdminDbStatsPayload)
async def admin_db_stats(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await db_stats(session)


@router.get("/admin/export", response_model=AdminExportPayload)
async def admin_export_catalog(
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await export_catalog(session, config=config)


@router.post("/admin/import", response_model=AdminImportDryRunPayload)
async def admin_import_catalog(
    payload: dict[str, Any],
    dry_run: bool = Query(default=True),
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await import_catalog(session, payload=payload, dry_run=dry_run, config=config, current_user=current_user)


@router.delete("/admin/retention", response_model=RetentionPayload)
async def admin_prune_database_records(
    jobs_days: int | None = Query(default=None, ge=1),
    audit_days: int | None = Query(default=None, ge=1),
    graph_notifications_days: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await prune_records(session, jobs_days=jobs_days, audit_days=audit_days, graph_notifications_days=graph_notifications_days, current_user=current_user)


@router.get("/admin/settings", response_model=AppSettingPagePayload)
async def admin_list_settings(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    items = [serialize_setting(record) for record in await list_settings(session)]
    return page_response(items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


@router.put("/admin/settings/{key}", response_model=AppSettingResponsePayload)
async def admin_upsert_setting(
    key: str,
    payload: AppSettingPayload,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await save_setting(session, key=key, value=payload.value, description=payload.description, current_user=current_user)


@router.delete("/admin/settings/{key}", response_model=DeletedPayload)
async def admin_delete_setting(
    key: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await remove_setting(session, key=key, current_user=current_user)

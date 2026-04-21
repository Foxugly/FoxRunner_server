from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AuditRecord
from api.serializers import serialize_audit as serialize_audit


async def write_audit(
    session: AsyncSession,
    *,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditRecord:
    record = AuditRecord(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before or {},
        after=after or {},
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def list_audit(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> list[AuditRecord]:
    query = select(AuditRecord).order_by(AuditRecord.id.desc()).offset(offset).limit(limit)
    if actor_user_id:
        query = query.where(AuditRecord.actor_user_id == actor_user_id)
    if target_type:
        query = query.where(AuditRecord.target_type == target_type)
    if target_id:
        query = query.where(AuditRecord.target_id == target_id)
    return list(await session.scalars(query))


async def count_audit(
    session: AsyncSession,
    *,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> int:
    query = select(func.count(AuditRecord.id))
    if actor_user_id:
        query = query.where(AuditRecord.actor_user_id == actor_user_id)
    if target_type:
        query = query.where(AuditRecord.target_type == target_type)
    if target_id:
        query = query.where(AuditRecord.target_id == target_id)
    return int(await session.scalar(query) or 0)

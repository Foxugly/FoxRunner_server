from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AppSettingRecord
from api.serializers import serialize_setting as serialize_setting


async def list_settings(session: AsyncSession) -> list[AppSettingRecord]:
    return list(await session.scalars(select(AppSettingRecord).order_by(AppSettingRecord.key)))


async def get_setting(session: AsyncSession, key: str) -> AppSettingRecord | None:
    return await session.scalar(select(AppSettingRecord).where(AppSettingRecord.key == key))


async def upsert_setting(
    session: AsyncSession,
    *,
    key: str,
    value: dict[str, Any],
    description: str = "",
    updated_by: str | None = None,
) -> AppSettingRecord:
    record = await session.scalar(select(AppSettingRecord).where(AppSettingRecord.key == key))
    if record is None:
        record = AppSettingRecord(key=key)
        session.add(record)
    record.value = value
    record.description = description
    record.updated_by = updated_by
    await session.commit()
    await session.refresh(record)
    return record


async def delete_setting(session: AsyncSession, key: str) -> None:
    record = await session.scalar(select(AppSettingRecord).where(AppSettingRecord.key == key))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting introuvable.")
    await session.delete(record)
    await session.commit()

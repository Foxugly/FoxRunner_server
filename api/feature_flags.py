from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.settings import get_setting


async def is_feature_enabled(session: AsyncSession, key: str, *, default: bool = False) -> bool:
    record = await get_setting(session, f"feature.{key}")
    if record is None:
        return default
    value = record.value or {}
    return bool(value.get("enabled", default))

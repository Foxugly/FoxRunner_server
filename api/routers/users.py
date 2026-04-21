from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User, current_active_user
from api.db import get_async_session
from api.schemas import FeatureFlagsPayload
from api.settings import list_settings

router = APIRouter(tags=["users"])


@router.get("/users/me/features", response_model=FeatureFlagsPayload)
async def my_features(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    records = await list_settings(session)
    features = {
        record.key.removeprefix("feature."): bool((record.value or {}).get("enabled", False))
        for record in records
        if record.key.startswith("feature.") and _is_visible_feature(record.key, current_user)
    }
    return {"features": features}


def _is_visible_feature(key: str, user: User) -> bool:
    return user.is_superuser or not key.startswith("feature.admin.")

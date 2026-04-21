from __future__ import annotations

import contextlib
import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User


async def timezone_for_user(session: AsyncSession, user_id: str, current_user: User) -> str:
    if user_id in {str(current_user.id), current_user.email}:
        return current_user.timezone_name
    predicates = [User.email == user_id]
    with contextlib.suppress(ValueError):
        predicates.append(User.id == uuid.UUID(user_id))
    target = await session.scalar(select(User).where(or_(*predicates)))
    return target.timezone_name if target is not None else current_user.timezone_name

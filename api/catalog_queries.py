from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import ScenarioRecord, ScenarioShareRecord, SlotRecord


def accessible_scenarios_query(user_id: str, *, is_superuser: bool = False):
    query = select(ScenarioRecord)
    if not is_superuser:
        shared = select(ScenarioShareRecord.scenario_id).where(ScenarioShareRecord.user_id == user_id)
        query = query.where(or_(ScenarioRecord.owner_user_id == user_id, ScenarioRecord.scenario_id.in_(shared)))
    return query


async def list_accessible_scenarios(
    session: AsyncSession,
    user_id: str,
    *,
    is_superuser: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ScenarioRecord], int]:
    base = accessible_scenarios_query(user_id, is_superuser=is_superuser)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(base.order_by(ScenarioRecord.scenario_id).offset(offset).limit(limit))
    return list(rows), total or 0


def accessible_slots_query(user_id: str, *, is_superuser: bool = False, scenario_id: str | None = None):
    query = select(SlotRecord)
    if scenario_id is not None:
        query = query.where(SlotRecord.scenario_id == scenario_id)
    if not is_superuser:
        scenarios = accessible_scenarios_query(user_id, is_superuser=False).subquery()
        query = query.join(scenarios, SlotRecord.scenario_id == scenarios.c.scenario_id)
    return query


async def list_accessible_slots(
    session: AsyncSession,
    user_id: str,
    *,
    is_superuser: bool = False,
    scenario_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[SlotRecord], int]:
    base = accessible_slots_query(user_id, is_superuser=is_superuser, scenario_id=scenario_id)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(base.order_by(SlotRecord.slot_id).offset(offset).limit(limit))
    return list(rows), total or 0

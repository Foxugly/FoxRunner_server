from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User
from api.catalog import create_slot, get_scenario_for_user, get_slot, slot_summary, sync_slots_file
from api.dependencies import actor_id
from api.routers.common import require_scenario_owner
from api.schemas import SlotPayload, SlotUpdatePayload
from app.config import AppConfig


async def create_owned_slot(
    session: AsyncSession,
    *,
    payload: SlotPayload,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    scenario = await get_scenario_for_user(session, str(current_user.email), payload.scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    record = await create_slot(session, slot_id=payload.slot_id, scenario_id=payload.scenario_id, days=payload.days, start=payload.start, end=payload.end, enabled=payload.enabled)
    await sync_slots_file(session, config.runtime.slots_file)
    result = slot_summary(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="slot.create", target_type="slot", target_id=record.slot_id, after=result)
    return result


async def update_owned_slot(
    session: AsyncSession,
    *,
    slot_id: str,
    payload: SlotUpdatePayload,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    record = await get_slot(session, slot_id)
    scenario = await get_scenario_for_user(session, str(current_user.email), record.scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    before = slot_summary(record)
    if payload.scenario_id is not None:
        target_scenario = await get_scenario_for_user(session, str(current_user.email), payload.scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
        require_scenario_owner(target_scenario, current_user)
        record.scenario_id = payload.scenario_id
    if payload.days is not None:
        record.days = payload.days
    if payload.start is not None:
        record.start = payload.start
    if payload.end is not None:
        record.end = payload.end
    if payload.enabled is not None:
        record.enabled = payload.enabled
    await session.commit()
    await session.refresh(record)
    await sync_slots_file(session, config.runtime.slots_file)
    result = slot_summary(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="slot.update", target_type="slot", target_id=record.slot_id, before=before, after=result)
    return result


async def delete_owned_slot(
    session: AsyncSession,
    *,
    slot_id: str,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    record = await get_slot(session, slot_id)
    scenario = await get_scenario_for_user(session, str(current_user.email), record.scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    before = slot_summary(record)
    await session.delete(record)
    await session.commit()
    await sync_slots_file(session, config.runtime.slots_file)
    await write_audit(session, actor_user_id=actor_id(current_user), action="slot.delete", target_type="slot", target_id=slot_id, before=before)
    return {"deleted": slot_id}

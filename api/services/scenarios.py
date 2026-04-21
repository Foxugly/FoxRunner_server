from __future__ import annotations

import json

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User, ensure_user_scope
from api.catalog import (
    create_scenario,
    delete_scenario,
    export_scenarios_document,
    get_scenario_for_user,
    save_scenario_definition,
    scenario_summary,
    share_scenario,
    unshare_scenario,
)
from api.dependencies import actor_id
from api.models import ScenarioRecord, SlotRecord
from api.routers.common import require_scenario_owner
from api.schemas import ScenarioPayload, ScenarioUpdatePayload
from app.config import AppConfig


async def create_owned_scenario(
    session: AsyncSession,
    *,
    payload: ScenarioPayload,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    ensure_user_scope(payload.owner_user_id, current_user)
    record = await create_scenario(session, scenario_id=payload.scenario_id, owner_user_id=payload.owner_user_id, description=payload.description, definition=payload.definition)
    await save_scenario_definition(session, record, record.definition, config.runtime.scenarios_file)
    result = scenario_summary(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="scenario.create", target_type="scenario", target_id=record.scenario_id, after=result)
    return result


async def update_owned_scenario(
    session: AsyncSession,
    *,
    scenario_id: str,
    payload: ScenarioUpdatePayload,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    record = await get_scenario_for_user(session, str(current_user.email), scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(record, current_user)
    before = scenario_summary(record)
    if payload.scenario_id and payload.scenario_id != record.scenario_id:
        if await session.scalar(select(ScenarioRecord.id).where(ScenarioRecord.scenario_id == payload.scenario_id)):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scenario deja existant.")
        for slot in await session.scalars(select(SlotRecord).where(SlotRecord.scenario_id == record.scenario_id)):
            slot.scenario_id = payload.scenario_id
        record.scenario_id = payload.scenario_id
    if payload.owner_user_id is not None:
        ensure_user_scope(payload.owner_user_id, current_user)
        record.owner_user_id = payload.owner_user_id
    definition = dict(record.definition or {})
    if payload.description is not None:
        definition["description"] = payload.description
    if payload.definition is not None:
        definition = payload.definition
    record.definition = definition
    await save_scenario_definition(session, record, definition, config.runtime.scenarios_file)
    result = scenario_summary(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="scenario.update", target_type="scenario", target_id=record.scenario_id, before=before, after=result)
    return result


async def duplicate_owned_scenario(
    session: AsyncSession,
    *,
    scenario_id: str,
    new_scenario_id: str,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    source = await get_scenario_for_user(session, str(current_user.email), scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(source, current_user)
    record = await create_scenario(
        session, scenario_id=new_scenario_id, owner_user_id=source.owner_user_id, description=source.description, definition=dict(source.definition or {})
    )
    await save_scenario_definition(session, record, record.definition, config.runtime.scenarios_file)
    result = scenario_summary(record)
    await write_audit(
        session, actor_user_id=actor_id(current_user), action="scenario.duplicate", target_type="scenario", target_id=new_scenario_id, before={"source": scenario_id}, after=result
    )
    return result


async def delete_owned_scenario(
    session: AsyncSession,
    *,
    scenario_id: str,
    config: AppConfig,
    current_user: User,
) -> dict[str, object]:
    record = await get_scenario_for_user(session, str(current_user.email), scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(record, current_user)
    if await session.scalar(select(SlotRecord.id).where(SlotRecord.scenario_id == scenario_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Supprime ou deplace les slots avant le scenario.")
    before = scenario_summary(record)
    await delete_scenario(session, scenario_id)
    raw = await export_scenarios_document(session, config.runtime.scenarios_file)
    config.runtime.scenarios_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    await write_audit(session, actor_user_id=actor_id(current_user), action="scenario.delete", target_type="scenario", target_id=scenario_id, before=before)
    return {"deleted": scenario_id}


async def share_owned_scenario(session: AsyncSession, *, scenario_id: str, share_user_id: str, current_user: User) -> dict[str, object]:
    record = await get_scenario_for_user(session, str(current_user.email), scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(record, current_user)
    share = await share_scenario(session, scenario_id, share_user_id)
    await write_audit(session, actor_user_id=actor_id(current_user), action="scenario.share", target_type="scenario", target_id=scenario_id, after={"user_id": share.user_id})
    return {"scenario_id": scenario_id, "user_id": share.user_id}


async def unshare_owned_scenario(session: AsyncSession, *, scenario_id: str, share_user_id: str, current_user: User) -> dict[str, object]:
    record = await get_scenario_for_user(session, str(current_user.email), scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    require_scenario_owner(record, current_user)
    await unshare_scenario(session, scenario_id, share_user_id)
    await write_audit(session, actor_user_id=actor_id(current_user), action="scenario.unshare", target_type="scenario", target_id=scenario_id, before={"user_id": share_user_id})
    return {"deleted": share_user_id}

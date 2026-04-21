from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User, ensure_user_scope
from api.catalog import ensure_step_collection, get_scenario_for_user, mutable_step_collection, save_scenario_definition, step_at
from api.dependencies import actor_id
from api.routers.common import require_scenario_owner
from api.schemas import StepPayload
from app.config import AppConfig


async def create_step(
    session: AsyncSession,
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    payload: StepPayload,
    insert_at: int | None,
    config: AppConfig,
    current_user: User,
) -> dict[str, Any]:
    ensure_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition)
    steps = mutable_step_collection(definition, collection)
    index = len(steps) if insert_at is None else min(insert_at, len(steps))
    steps.insert(index, payload.step)
    await save_scenario_definition(session, scenario, definition, config.runtime.scenarios_file)
    await write_audit(
        session,
        actor_user_id=actor_id(current_user),
        action="step.create",
        target_type="scenario",
        target_id=scenario_id,
        after={"collection": collection, "index": index, "step": payload.step},
    )
    return {"index": index, "step": payload.step}


async def update_step(
    session: AsyncSession,
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    payload: StepPayload,
    config: AppConfig,
    current_user: User,
) -> dict[str, Any]:
    ensure_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition)
    steps = mutable_step_collection(definition, collection)
    step_at(steps, index)
    before = step_at(steps, index)
    steps[index] = payload.step
    await save_scenario_definition(session, scenario, definition, config.runtime.scenarios_file)
    await write_audit(
        session,
        actor_user_id=actor_id(current_user),
        action="step.update",
        target_type="scenario",
        target_id=scenario_id,
        before={"collection": collection, "index": index, "step": before},
        after={"collection": collection, "index": index, "step": payload.step},
    )
    return {"index": index, "step": payload.step}


async def delete_step(
    session: AsyncSession,
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    config: AppConfig,
    current_user: User,
) -> dict[str, Any]:
    ensure_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition)
    steps = mutable_step_collection(definition, collection)
    deleted = step_at(steps, index)
    del steps[index]
    await save_scenario_definition(session, scenario, definition, config.runtime.scenarios_file)
    await write_audit(
        session,
        actor_user_id=actor_id(current_user),
        action="step.delete",
        target_type="scenario",
        target_id=scenario_id,
        before={"collection": collection, "index": index, "step": deleted},
    )
    return {"index": index, "deleted": deleted}

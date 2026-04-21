from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User, current_active_user, ensure_user_scope
from api.catalog import (
    STEP_COLLECTIONS,
    ensure_step_collection,
    get_scenario_for_user,
    get_slot,
    list_scenario_shares,
    scenario_ids_for_user,
    slot_summary,
    step_at,
    step_collection,
)
from api.catalog_queries import list_accessible_scenarios, list_accessible_slots
from api.db import get_async_session
from api.dependencies import build_service_from_db, get_config
from api.history import import_history_jsonl, list_history, serialize_history
from api.idempotency import get_idempotent_response, store_idempotent_response
from api.pagination import page_response
from api.routers.common import scenario_summary_for_user
from api.schemas import (
    DeletedPayload,
    HistoryPagePayload,
    PlanPayload,
    RunScenarioResponsePayload,
    ScenarioDetailPayload,
    ScenarioPagePayload,
    ScenarioPayload,
    ScenarioSummaryPayload,
    ScenarioUpdatePayload,
    ShareListPayload,
    SharePayload,
    ShareResponsePayload,
    SlotPagePayload,
    SlotPayload,
    SlotSummaryPayload,
    SlotUpdatePayload,
    StepDeletePayload,
    StepMutationPayload,
    StepPayload,
)
from api.services.scenarios import create_owned_scenario, delete_owned_scenario, duplicate_owned_scenario, share_owned_scenario, unshare_owned_scenario, update_owned_scenario
from api.services.slots import create_owned_slot, delete_owned_slot, update_owned_slot
from api.services.steps import create_step, delete_step, update_step
from api.services.users import timezone_for_user
from app.config import AppConfig
from scenarios.loader import load_scenario_data

router = APIRouter()


@router.post("/scenarios", status_code=status.HTTP_201_CREATED, tags=["scenarios"], response_model=ScenarioSummaryPayload)
async def create_scenario_endpoint(
    request: Request,
    payload: ScenarioPayload,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(payload.owner_user_id, current_user)
    cached = await get_idempotent_response(session, request=request, user_id=str(current_user.email), payload=payload.model_dump())
    if cached is not None:
        return cached
    result = await create_owned_scenario(session, payload=payload, config=config, current_user=current_user)
    await store_idempotent_response(session, request=request, user_id=str(current_user.email), payload=payload.model_dump(), response=result, status_code=201)
    return result


@router.patch("/scenarios/{scenario_id}", tags=["scenarios"], response_model=ScenarioSummaryPayload)
async def update_scenario_endpoint(
    scenario_id: str,
    payload: ScenarioUpdatePayload,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await update_owned_scenario(session, scenario_id=scenario_id, payload=payload, config=config, current_user=current_user)


@router.post("/scenarios/{scenario_id}/duplicate", status_code=status.HTTP_201_CREATED, tags=["scenarios"], response_model=ScenarioSummaryPayload)
async def duplicate_scenario_endpoint(
    scenario_id: str,
    new_scenario_id: str,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await duplicate_owned_scenario(session, scenario_id=scenario_id, new_scenario_id=new_scenario_id, config=config, current_user=current_user)


@router.delete("/scenarios/{scenario_id}", tags=["scenarios"], response_model=DeletedPayload)
async def delete_scenario_endpoint(
    scenario_id: str,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await delete_owned_scenario(session, scenario_id=scenario_id, config=config, current_user=current_user)


@router.get("/scenarios/{scenario_id}/shares", tags=["scenarios"], response_model=ShareListPayload)
async def scenario_shares_endpoint(
    scenario_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    await get_scenario_for_user(session, str(current_user.email), scenario_id, is_superuser=current_user.is_superuser)
    return {"scenario_id": scenario_id, "user_ids": await list_scenario_shares(session, scenario_id)}


@router.post("/scenarios/{scenario_id}/shares", status_code=status.HTTP_201_CREATED, tags=["scenarios"], response_model=ShareResponsePayload)
async def share_scenario_endpoint(
    scenario_id: str,
    payload: SharePayload,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await share_owned_scenario(session, scenario_id=scenario_id, share_user_id=payload.user_id, current_user=current_user)


@router.delete("/scenarios/{scenario_id}/shares/{share_user_id}", tags=["scenarios"], response_model=DeletedPayload)
async def unshare_scenario_endpoint(
    scenario_id: str,
    share_user_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await unshare_owned_scenario(session, scenario_id=scenario_id, share_user_id=share_user_id, current_user=current_user)


@router.get("/slots", tags=["slots"], response_model=SlotPagePayload)
async def list_slots_endpoint(
    scenario_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    records, total = await list_accessible_slots(session, str(current_user.email), is_superuser=current_user.is_superuser, scenario_id=scenario_id, limit=limit, offset=offset)
    if scenario_id is not None and total == 0 and not current_user.is_superuser:
        await get_scenario_for_user(session, str(current_user.email), scenario_id, is_superuser=False)
    return page_response([slot_summary(record) for record in records], total=total, limit=limit, offset=offset)


@router.post("/slots", status_code=status.HTTP_201_CREATED, tags=["slots"], response_model=SlotSummaryPayload)
async def create_slot_endpoint(
    request: Request,
    payload: SlotPayload,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    cached = await get_idempotent_response(session, request=request, user_id=str(current_user.email), payload=payload.model_dump())
    if cached is not None:
        return cached
    result = await create_owned_slot(session, payload=payload, config=config, current_user=current_user)
    await store_idempotent_response(session, request=request, user_id=str(current_user.email), payload=payload.model_dump(), response=result, status_code=201)
    return result


@router.get("/slots/{slot_id}", tags=["slots"], response_model=SlotSummaryPayload)
async def get_slot_endpoint(
    slot_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    record = await get_slot(session, slot_id)
    await get_scenario_for_user(session, str(current_user.email), record.scenario_id, is_superuser=current_user.is_superuser)
    return slot_summary(record)


@router.patch("/slots/{slot_id}", tags=["slots"], response_model=SlotSummaryPayload)
async def update_slot_endpoint(
    slot_id: str,
    payload: SlotUpdatePayload,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await update_owned_slot(session, slot_id=slot_id, payload=payload, config=config, current_user=current_user)


@router.delete("/slots/{slot_id}", tags=["slots"], response_model=DeletedPayload)
async def delete_slot_endpoint(
    slot_id: str,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    return await delete_owned_slot(session, slot_id=slot_id, config=config, current_user=current_user)


@router.get("/users/{user_id}/plan", tags=["scenarios"], response_model=PlanPayload)
async def user_plan(
    user_id: str,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    scenario_ids = await scenario_ids_for_user(session, user_id, is_superuser=current_user.is_superuser)
    if not scenario_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun scenario pour cet utilisateur.")
    try:
        service = await build_service_from_db(config, session, timezone_name=await timezone_for_user(session, user_id, current_user))
        return service.describe_plan_for_scenarios(scenario_ids)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/users/{user_id}/slots", tags=["slots"], response_model=SlotPagePayload)
async def user_slots(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    records, total = await list_accessible_slots(session, user_id, is_superuser=current_user.is_superuser, limit=limit, offset=offset)
    return page_response([slot_summary(slot) for slot in records], total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}/scenarios", tags=["scenarios"], response_model=ScenarioPagePayload)
async def user_scenarios(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    records, total = await list_accessible_scenarios(session, user_id, is_superuser=current_user.is_superuser, limit=limit, offset=offset)
    return page_response([scenario_summary_for_user(scenario, current_user) for scenario in records], total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}/scenarios/{scenario_id}", tags=["scenarios"], response_model=ScenarioDetailPayload)
async def user_scenario(
    user_id: str,
    scenario_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    return {**scenario_summary_for_user(scenario, current_user), "definition": scenario.definition}


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections", tags=["steps"])
async def scenario_step_collections(
    user_id: str,
    scenario_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, list[dict[str, Any]]]:
    ensure_user_scope(user_id, current_user)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    return {collection: step_collection(scenario.definition, collection) for collection in sorted(STEP_COLLECTIONS)}


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}", tags=["steps"])
async def list_scenario_steps(
    user_id: str,
    scenario_id: str,
    collection: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> list[dict[str, Any]]:
    ensure_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    return step_collection(scenario.definition, collection)


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}", tags=["steps"])
async def get_scenario_step(
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, Any]:
    ensure_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    return step_at(step_collection(scenario.definition, collection), index)


@router.post("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}", status_code=status.HTTP_201_CREATED, tags=["steps"], response_model=StepMutationPayload)
async def create_scenario_step(
    user_id: str,
    scenario_id: str,
    collection: str,
    payload: StepPayload,
    insert_at: int | None = Query(default=None, ge=0),
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, Any]:
    return await create_step(
        session, user_id=user_id, scenario_id=scenario_id, collection=collection, payload=payload, insert_at=insert_at, config=config, current_user=current_user
    )


@router.put("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}", tags=["steps"], response_model=StepMutationPayload)
async def update_scenario_step(
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    payload: StepPayload,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, Any]:
    return await update_step(session, user_id=user_id, scenario_id=scenario_id, collection=collection, index=index, payload=payload, config=config, current_user=current_user)


@router.delete("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}", tags=["steps"], response_model=StepDeletePayload)
async def delete_scenario_step(
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, Any]:
    return await delete_step(session, user_id=user_id, scenario_id=scenario_id, collection=collection, index=index, config=config, current_user=current_user)


@router.post("/users/{user_id}/scenarios/{scenario_id}/run", tags=["scenarios"], response_model=RunScenarioResponsePayload)
async def run_user_scenario(
    user_id: str,
    scenario_id: str,
    dry_run: bool = Query(default=True),
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    await get_scenario_for_user(session, user_id, scenario_id, is_superuser=current_user.is_superuser)
    service = await build_service_from_db(config, session)
    exit_code = service.run_scenario(scenario_id, dry_run=dry_run)
    return {"scenario_id": scenario_id, "dry_run": dry_run, "exit_code": exit_code, "success": exit_code == 0}


@router.post("/users/{user_id}/run-next", tags=["scenarios"], response_model=RunScenarioResponsePayload)
async def run_user_next(
    user_id: str,
    dry_run: bool = Query(default=True),
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    scenario_ids = await scenario_ids_for_user(session, user_id, is_superuser=current_user.is_superuser)
    if not scenario_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun scenario pour cet utilisateur.")
    service = await build_service_from_db(config, session, timezone_name=await timezone_for_user(session, user_id, current_user))
    exit_code = service.run_next_for_scenarios(scenario_ids, dry_run=dry_run)
    return {"dry_run": dry_run, "exit_code": exit_code, "success": exit_code == 0}


@router.get("/users/{user_id}/history", tags=["scenarios"], response_model=HistoryPagePayload)
async def user_history(
    user_id: str,
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
    slot_id: str | None = None,
    scenario_id: str | None = None,
    execution_id: str | None = None,
    config: AppConfig = Depends(get_config),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    allowed_ids = await scenario_ids_for_user(session, user_id, is_superuser=current_user.is_superuser)
    if scenario_id is not None and scenario_id not in allowed_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario introuvable pour cet utilisateur.")
    await import_history_jsonl(session, config.runtime.history_file)
    records, total = await list_history(
        session,
        limit=limit,
        offset=offset,
        status=status_filter,
        slot_id=slot_id,
        scenario_id=scenario_id,
        scenario_ids=allowed_ids if not current_user.is_superuser else None,
        execution_id=execution_id,
    )
    items = [serialize_history(row) for row in records]
    return page_response(items, total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}/scenario-data", tags=["scenarios"])
async def user_scenario_data(
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
    config: AppConfig = Depends(get_config),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    if not await scenario_ids_for_user(session, user_id, is_superuser=current_user.is_superuser):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Aucun scenario pour cet utilisateur.")
    data = load_scenario_data(config.runtime.scenarios_file)
    return {
        "default_pushover_key": data.default_pushover_key,
        "default_network_key": data.default_network_key,
        "pushovers": sorted(data.pushovers),
        "networks": sorted(data.networks),
    }

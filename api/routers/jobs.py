from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User, current_active_user, ensure_user_scope
from api.db import get_async_session
from api.idempotency import get_idempotent_response, store_idempotent_response
from api.jobs import count_jobs, get_job_for_user, list_job_events, list_jobs, serialize_job, serialize_job_event
from api.pagination import page_response
from api.schemas import JobEventPayload, JobPagePayload, JobPayload
from api.services.jobs import cancel_job, enqueue_scenario_job, retry_job

router = APIRouter(tags=["jobs"])


@router.post("/users/{user_id}/scenarios/{scenario_id}/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=JobPayload)
async def enqueue_user_scenario(
    request: Request,
    user_id: str,
    scenario_id: str,
    dry_run: bool = Query(default=True),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    idem_payload = {"user_id": user_id, "scenario_id": scenario_id, "dry_run": dry_run}
    cached = await get_idempotent_response(session, request=request, user_id=str(current_user.email), payload=idem_payload)
    if cached is not None:
        return cached
    result = await enqueue_scenario_job(session, user_id=user_id, scenario_id=scenario_id, dry_run=dry_run, current_user=current_user)
    await store_idempotent_response(session, request=request, user_id=str(current_user.email), payload=idem_payload, response=result, status_code=202)
    return result


@router.get("/jobs", response_model=JobPagePayload)
async def list_jobs_endpoint(
    user_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    scenario_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    effective_user_id = user_id
    if not current_user.is_superuser:
        effective_user_id = str(current_user.email)
        if user_id is not None and user_id != effective_user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acces jobs refuse.")
    records = await list_jobs(
        session,
        user_id=effective_user_id,
        status_filter=status_filter,
        scenario_id=scenario_id,
        limit=limit,
        offset=offset,
    )
    total = await count_jobs(session, user_id=effective_user_id, status_filter=status_filter, scenario_id=scenario_id)
    return page_response([serialize_job(record) for record in records], total=total, limit=limit, offset=offset)


@router.post("/jobs/{job_id}/cancel", response_model=JobPayload)
async def cancel_job_endpoint(
    job_id: str,
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    return await cancel_job(session, job_id=job_id, user_id=user_id, current_user=current_user)


@router.post("/jobs/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=JobPayload)
async def retry_job_endpoint(
    job_id: str,
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    return await retry_job(session, job_id=job_id, user_id=user_id, current_user=current_user)


@router.get("/jobs/{job_id}", response_model=JobPayload)
async def get_job(
    job_id: str,
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    ensure_user_scope(user_id, current_user)
    job = await get_job_for_user(session, job_id, user_id, is_superuser=current_user.is_superuser)
    return serialize_job(job)


@router.get("/jobs/{job_id}/events", response_model=list[JobEventPayload])
async def get_job_events(
    job_id: str,
    user_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> list[dict[str, object]]:
    ensure_user_scope(user_id, current_user)
    await get_job_for_user(session, job_id, user_id, is_superuser=current_user.is_superuser)
    return [serialize_job_event(event) for event in await list_job_events(session, job_id)]

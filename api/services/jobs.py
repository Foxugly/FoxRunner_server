from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User
from api.catalog import get_scenario_for_user
from api.celery_app import celery_app
from api.dependencies import actor_id
from api.jobs import append_job_event, create_job, get_job_for_user, mark_job_cancelled, serialize_job, set_celery_task_id
from api.tasks import run_scenario_job


async def enqueue_scenario_job(session: AsyncSession, *, user_id: str, scenario_id: str, dry_run: bool, current_user: User) -> dict[str, object]:
    await get_scenario_for_user(session, user_id, scenario_id, email=current_user.email, is_superuser=current_user.is_superuser)
    job = await create_job(session, kind="run_scenario", user_id=user_id, target_id=scenario_id, dry_run=dry_run, payload={"scenario_id": scenario_id})
    task = run_scenario_job.delay(job.job_id, scenario_id, dry_run)
    await set_celery_task_id(session, job.job_id, task.id)
    await append_job_event(session, job_id=job.job_id, event_type="submitted", message="Tache Celery soumise.", payload={"celery_task_id": task.id})
    await session.refresh(job)
    return serialize_job(job)


async def cancel_job(session: AsyncSession, *, job_id: str, user_id: str, current_user: User) -> dict[str, object]:
    record = await get_job_for_user(session, job_id, user_id, is_superuser=current_user.is_superuser)
    before = serialize_job(record)
    if record.celery_task_id:
        celery_app.control.revoke(record.celery_task_id, terminate=False)
    await mark_job_cancelled(session, record)
    await session.refresh(record)
    result = serialize_job(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="job.cancel", target_type="job", target_id=job_id, before=before, after=result)
    return result


async def retry_job(session: AsyncSession, *, job_id: str, user_id: str, current_user: User) -> dict[str, object]:
    source = await get_job_for_user(session, job_id, user_id, is_superuser=current_user.is_superuser)
    if source.kind != "run_scenario":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Retry non supporte pour ce type de job.")
    await get_scenario_for_user(session, user_id, source.target_id, email=current_user.email, is_superuser=current_user.is_superuser)
    retry = await create_job(
        session,
        kind=source.kind,
        user_id=source.user_id,
        target_id=source.target_id,
        dry_run=source.dry_run,
        payload={**(source.payload or {}), "retry_of": source.job_id},
    )
    task = run_scenario_job.delay(retry.job_id, retry.target_id, retry.dry_run)
    await set_celery_task_id(session, retry.job_id, task.id)
    await append_job_event(session, job_id=retry.job_id, event_type="submitted", message="Retry Celery soumis.", payload={"celery_task_id": task.id, "retry_of": source.job_id})
    await session.refresh(retry)
    await write_audit(session, actor_user_id=actor_id(current_user), action="job.retry", target_type="job", target_id=source.job_id, after={"new_job_id": retry.job_id})
    return serialize_job(retry)

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import JobEventRecord, JobRecord
from api.serializers import serialize_job as serialize_job
from api.serializers import serialize_job_event as serialize_job_event
from api.time_utils import utc_now_naive


async def create_job(
    session: AsyncSession,
    *,
    kind: str,
    user_id: str,
    target_id: str,
    dry_run: bool,
    payload: dict | None = None,
) -> JobRecord:
    record = JobRecord(
        job_id=uuid4().hex,
        kind=kind,
        user_id=user_id,
        target_id=target_id,
        dry_run=dry_run,
        status="queued",
        payload=payload or {},
        result={},
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    await append_job_event(
        session,
        job_id=record.job_id,
        event_type="queued",
        message=f"Job {kind} mis en file.",
        payload={"target_id": target_id, "dry_run": dry_run},
    )
    return record


async def get_job_for_user(session: AsyncSession, job_id: str, user_id: str, *, is_superuser: bool = False) -> JobRecord:
    record = await session.scalar(select(JobRecord).where(JobRecord.job_id == job_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job introuvable.")
    if not is_superuser and record.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acces job refuse.")
    return record


async def list_jobs(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    status_filter: str | None = None,
    scenario_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[JobRecord]:
    query = select(JobRecord).order_by(JobRecord.id.desc()).offset(offset).limit(limit)
    if user_id:
        query = query.where(JobRecord.user_id == user_id)
    if status_filter:
        query = query.where(JobRecord.status == status_filter)
    if scenario_id:
        query = query.where(JobRecord.target_id == scenario_id)
    return list(await session.scalars(query))


async def count_jobs(
    session: AsyncSession,
    *,
    user_id: str | None = None,
    status_filter: str | None = None,
    scenario_id: str | None = None,
) -> int:
    query = select(func.count(JobRecord.id))
    if user_id:
        query = query.where(JobRecord.user_id == user_id)
    if status_filter:
        query = query.where(JobRecord.status == status_filter)
    if scenario_id:
        query = query.where(JobRecord.target_id == scenario_id)
    return int(await session.scalar(query) or 0)


async def set_celery_task_id(session: AsyncSession, job_id: str, celery_task_id: str) -> None:
    record = await session.scalar(select(JobRecord).where(JobRecord.job_id == job_id))
    if record is None:
        raise RuntimeError(f"Job introuvable: {job_id}")
    record.celery_task_id = celery_task_id
    await session.commit()


async def mark_job_cancelled(session: AsyncSession, record: JobRecord) -> None:
    if record.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Seuls les jobs queued/running peuvent etre annules.")
    record.status = "cancelled"
    record.finished_at = utc_now_naive()
    await session.commit()
    await append_job_event(session, job_id=record.job_id, event_type="cancelled", message="Job annule.", level="warning")


async def append_job_event(
    session: AsyncSession,
    *,
    job_id: str,
    event_type: str,
    message: str,
    level: str = "info",
    step: str | None = None,
    payload: dict | None = None,
) -> JobEventRecord:
    record = JobEventRecord(
        job_id=job_id,
        event_type=event_type,
        level=level,
        message=message,
        step=step,
        payload=payload or {},
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def list_job_events(session: AsyncSession, job_id: str) -> list[JobEventRecord]:
    result = await session.scalars(select(JobEventRecord).where(JobEventRecord.job_id == job_id).order_by(JobEventRecord.created_at, JobEventRecord.id))
    return list(result)

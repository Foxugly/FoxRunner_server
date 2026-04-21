from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import GraphSubscriptionRecord, JobRecord
from api.time_utils import utc_now


async def monitoring_summary(session: AsyncSession, *, stuck_after_minutes: int = 30, graph_expiring_hours: int = 24) -> dict[str, object]:
    now = utc_now()
    stuck_before = now - timedelta(minutes=stuck_after_minutes)
    expiring_before = now + timedelta(hours=graph_expiring_hours)

    total_jobs = await session.scalar(select(func.count(JobRecord.id)))
    failed_jobs = await session.scalar(select(func.count(JobRecord.id)).where(JobRecord.status == "failed"))
    queued_jobs = await session.scalar(select(func.count(JobRecord.id)).where(JobRecord.status == "queued"))
    running_jobs = await session.scalar(select(func.count(JobRecord.id)).where(JobRecord.status == "running"))
    by_status_rows = await session.execute(select(JobRecord.status, func.count(JobRecord.id)).group_by(JobRecord.status))
    stuck_jobs = await session.scalar(
        select(func.count(JobRecord.id)).where(
            JobRecord.status.in_(("queued", "running")),
            JobRecord.updated_at < stuck_before.replace(tzinfo=None),
        )
    )
    expiring_graph_subscriptions = await session.scalar(
        select(func.count(GraphSubscriptionRecord.id)).where(
            GraphSubscriptionRecord.expiration_datetime.is_not(None),
            GraphSubscriptionRecord.expiration_datetime < expiring_before.replace(tzinfo=None),
        )
    )
    completed_jobs = list(
        await session.scalars(
            select(JobRecord)
            .where(
                JobRecord.started_at.is_not(None),
                JobRecord.finished_at.is_not(None),
            )
            .limit(1000)
        )
    )
    durations = [(job.finished_at - job.started_at).total_seconds() for job in completed_jobs if job.finished_at and job.started_at]
    average_duration = sum(durations) / len(durations) if durations else None

    return {
        "jobs": {
            "total": total_jobs or 0,
            "failed": failed_jobs or 0,
            "queued": queued_jobs or 0,
            "running": running_jobs or 0,
            "stuck": stuck_jobs or 0,
            "by_status": {str(status): int(count) for status, count in by_status_rows.all()},
            "average_duration_seconds": average_duration,
        },
        "graph": {
            "subscriptions_expiring": expiring_graph_subscriptions or 0,
            "expiring_within_hours": graph_expiring_hours,
        },
    }

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import AuditRecord, GraphNotificationRecord, JobEventRecord, JobRecord
from api.time_utils import utc_now_naive


async def prune_database_records(
    session: AsyncSession,
    *,
    jobs_days: int | None = None,
    audit_days: int | None = None,
    graph_notifications_days: int | None = None,
) -> dict[str, int]:
    removed = {"jobs": 0, "job_events": 0, "audit": 0, "graph_notifications": 0}
    now = utc_now_naive()

    if jobs_days is not None:
        cutoff = now - timedelta(days=jobs_days)
        job_ids = list(await session.scalars(select(JobRecord.job_id).where(JobRecord.finished_at.is_not(None), JobRecord.finished_at < cutoff)))
        if job_ids:
            result = await session.execute(delete(JobEventRecord).where(JobEventRecord.job_id.in_(job_ids)))
            removed["job_events"] = result.rowcount or 0
            result = await session.execute(delete(JobRecord).where(JobRecord.job_id.in_(job_ids)))
            removed["jobs"] = result.rowcount or 0

    if audit_days is not None:
        cutoff = now - timedelta(days=audit_days)
        result = await session.execute(delete(AuditRecord).where(AuditRecord.created_at < cutoff))
        removed["audit"] = result.rowcount or 0

    if graph_notifications_days is not None:
        cutoff = now - timedelta(days=graph_notifications_days)
        result = await session.execute(delete(GraphNotificationRecord).where(GraphNotificationRecord.created_at < cutoff))
        removed["graph_notifications"] = result.rowcount or 0

    await session.commit()
    return removed

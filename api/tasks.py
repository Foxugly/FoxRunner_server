from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

from sqlalchemy import select

from api.artifacts import prune_artifacts
from api.catalog import load_scheduler_catalog
from api.celery_app import celery_app
from api.db import async_session_maker
from api.graph import is_graph_configured, renew_graph_subscription
from api.jobs import append_job_event
from api.models import GraphSubscriptionRecord, JobRecord
from api.retention import prune_database_records
from api.time_utils import db_utc, parse_utc, utc_now, utc_now_naive
from app.config import load_config
from app.main import build_runtime_services_from_catalog

logger = logging.getLogger("smiley.api.tasks")


@celery_app.task(name="api.tasks.run_scenario_job")
def run_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict[str, object]:
    return asyncio.run(_run_scenario_job(job_id, scenario_id, dry_run))


@celery_app.task(name="api.tasks.renew_graph_subscriptions_task")
def renew_graph_subscriptions_task() -> dict[str, object]:
    return asyncio.run(_renew_graph_subscriptions_task())


@celery_app.task(name="api.tasks.prune_retention_task")
def prune_retention_task() -> dict[str, object]:
    return asyncio.run(_prune_retention_task())


async def _run_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict[str, object]:
    async with async_session_maker() as session:
        record = await _get_job(session, job_id)
        record.status = "running"
        record.started_at = utc_now_naive()
        await session.commit()
        await append_job_event(session, job_id=job_id, event_type="running", message="Execution du scenario demarree.")

    try:
        config = load_config()
        async with async_session_maker() as session:
            slots, scenarios = await load_scheduler_catalog(session)
        service = build_runtime_services_from_catalog(config, slots, scenarios)
        exit_code = service.run_scenario(scenario_id, dry_run=dry_run)
        async with async_session_maker() as session:
            record = await _get_job(session, job_id)
            record.status = "success" if exit_code == 0 else "failed"
            record.exit_code = exit_code
            record.finished_at = utc_now_naive()
            record.result = {"scenario_id": scenario_id, "dry_run": dry_run}
            await session.commit()
            await append_job_event(
                session,
                job_id=job_id,
                event_type=record.status,
                level="info" if exit_code == 0 else "error",
                message=f"Execution terminee avec exit_code={exit_code}.",
                payload={"exit_code": exit_code},
            )
        return {"job_id": job_id, "exit_code": exit_code}
    except Exception as exc:
        async with async_session_maker() as session:
            record = await _get_job(session, job_id)
            record.status = "failed"
            record.error = str(exc)
            record.finished_at = utc_now_naive()
            await session.commit()
            await append_job_event(
                session,
                job_id=job_id,
                event_type="failed",
                level="error",
                message=str(exc),
            )
        raise


async def _get_job(session, job_id: str) -> JobRecord:
    record = await session.scalar(select(JobRecord).where(JobRecord.job_id == job_id))
    if record is None:
        raise RuntimeError(f"Job introuvable: {job_id}")
    return record


async def _renew_graph_subscriptions_task() -> dict[str, object]:
    if os.getenv("GRAPH_SUBSCRIPTION_RENEW_ENABLED", "true").lower() != "true":
        return {"enabled": False, "renewed": 0}
    if not is_graph_configured():
        return {"enabled": True, "configured": False, "renewed": 0}

    renew_before_hours = int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS", "24"))
    extension_hours = int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS", "48"))
    now = utc_now()
    renew_before = now + timedelta(hours=renew_before_hours)
    new_expiration = now + timedelta(hours=extension_hours)
    renewed = 0
    errors: list[dict[str, str]] = []

    async with async_session_maker() as session:
        records = list(
            await session.scalars(
                select(GraphSubscriptionRecord).where(
                    GraphSubscriptionRecord.expiration_datetime.is_not(None),
                    GraphSubscriptionRecord.expiration_datetime < renew_before.replace(tzinfo=None),
                )
            )
        )
        for record in records:
            try:
                raw = await renew_graph_subscription(record.subscription_id, new_expiration.isoformat().replace("+00:00", "Z"))
                raw_expiration = raw.get("expirationDateTime", new_expiration.isoformat())
                record.expiration_datetime = db_utc(parse_utc(raw_expiration))
                from api.redaction import redact

                record.raw_payload = redact(raw)
                renewed += 1
            except Exception as exc:
                logger.error("Graph subscription renewal failed for %s: %s", record.subscription_id, exc)
                errors.append({"subscription_id": record.subscription_id, "error": str(exc)})
        await session.commit()
    return {"enabled": True, "configured": True, "renewed": renewed, "errors": errors}


async def _prune_retention_task() -> dict[str, object]:
    if os.getenv("RETENTION_PRUNE_ENABLED", "false").lower() != "true":
        return {"enabled": False}
    jobs_days = _optional_int("RETENTION_JOBS_DAYS")
    audit_days = _optional_int("RETENTION_AUDIT_DAYS")
    graph_days = _optional_int("RETENTION_GRAPH_NOTIFICATIONS_DAYS")
    artifact_days = _optional_int("RETENTION_ARTIFACTS_DAYS")
    async with async_session_maker() as session:
        removed = await prune_database_records(session, jobs_days=jobs_days, audit_days=audit_days, graph_notifications_days=graph_days)
    artifact_removed = 0
    if artifact_days is not None:
        config = load_config()
        artifact_removed = prune_artifacts(config.runtime.artifacts_dir, older_than_days=artifact_days)
    return {"enabled": True, "removed": {**removed, "artifacts": artifact_removed}}


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return int(raw)

from __future__ import annotations

import contextlib
import json
import os
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User
from api.catalog import export_scenarios_document, export_slots_document, sync_slots_file
from api.dependencies import actor_id, serialize_user
from api.health import readiness
from api.models import ScenarioRecord, ScenarioShareRecord, SlotRecord
from api.retention import prune_database_records
from api.schemas import AdminUserUpdatePayload
from api.settings import delete_setting, serialize_setting, upsert_setting
from api.time_utils import isoformat_utc, utc_now
from app.config import AppConfig
from scenarios.loader import validate_scenarios_document, validate_slots_document


async def update_user(session: AsyncSession, *, target_user_id: str, payload: AdminUserUpdatePayload, current_user: User) -> dict[str, object]:
    predicates = [User.email == target_user_id]
    with contextlib.suppress(ValueError):
        predicates.append(User.id == uuid.UUID(target_user_id))
    user = await session.scalar(select(User).where(or_(*predicates)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    before = serialize_user(user)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.is_superuser is not None:
        user.is_superuser = payload.is_superuser
    if payload.is_verified is not None:
        user.is_verified = payload.is_verified
    if payload.timezone_name is not None:
        user.timezone_name = payload.timezone_name
    await session.commit()
    await session.refresh(user)
    result = serialize_user(user)
    await write_audit(session, actor_user_id=actor_id(current_user), action="admin.update_user", target_type="user", target_id=str(user.id), before=before, after=result)
    return result


async def config_checks(session: AsyncSession, *, config: AppConfig) -> dict[str, object]:
    ready_state = await readiness(session)
    checks = dict(ready_state.get("checks", {}))
    checks.update(
        {
            "auth_secret_configured": bool(os.getenv("AUTH_SECRET")),
            "database_url_configured": bool(os.getenv("AUTH_DATABASE_URL")),
            "celery_broker_url_configured": bool(os.getenv("CELERY_BROKER_URL")),
            "celery_result_backend_configured": bool(os.getenv("CELERY_RESULT_BACKEND")),
            "scenarios_file_exists": config.runtime.scenarios_file.exists(),
            "slots_file_exists": config.runtime.slots_file.exists(),
            "artifacts_dir": str(config.runtime.artifacts_dir),
        }
    )
    return {"status": "ok" if checks.get("database") == "ok" else "degraded", "checks": checks}


async def db_stats(session: AsyncSession) -> dict[str, object]:
    from datetime import timedelta

    from api.models import (
        AppSettingRecord,
        AuditRecord,
        ExecutionHistoryRecord,
        GraphNotificationRecord,
        GraphSubscriptionRecord,
        IdempotencyRecord,
        JobEventRecord,
        JobRecord,
        ScenarioRecord,
        ScenarioShareRecord,
        SlotRecord,
        User,
    )

    tables = {
        "users": await _count(session, User),
        "scenarios": await _count(session, ScenarioRecord),
        "scenario_shares": await _count(session, ScenarioShareRecord),
        "slots": await _count(session, SlotRecord),
        "jobs": await _count(session, JobRecord),
        "job_events": await _count(session, JobEventRecord),
        "graph_subscriptions": await _count(session, GraphSubscriptionRecord),
        "graph_notifications": await _count(session, GraphNotificationRecord),
        "audit_log": await _count(session, AuditRecord),
        "execution_history": await _count(session, ExecutionHistoryRecord),
        "app_settings": await _count(session, AppSettingRecord),
        "idempotency_keys": await _count(session, IdempotencyRecord),
    }
    last_execution_at = await session.scalar(select(func.max(ExecutionHistoryRecord.executed_at)))
    failed_jobs = await session.scalar(select(func.count(JobRecord.id)).where(JobRecord.status == "failed"))
    expiring_before = utc_now() + timedelta(hours=24)
    graph_subscriptions_expiring = await session.scalar(
        select(func.count(GraphSubscriptionRecord.id)).where(
            GraphSubscriptionRecord.expiration_datetime.is_not(None),
            GraphSubscriptionRecord.expiration_datetime < expiring_before.replace(tzinfo=None),
        )
    )
    return {
        "tables": tables,
        "last_execution_at": isoformat_utc(last_execution_at),
        "failed_jobs": failed_jobs or 0,
        "graph_subscriptions_expiring": graph_subscriptions_expiring or 0,
    }


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count(model.id))) or 0)


async def export_catalog(session: AsyncSession, *, config: AppConfig) -> dict[str, object]:
    return {"scenarios": await export_scenarios_document(session, config.runtime.scenarios_file), "slots": await export_slots_document(session)}


async def import_catalog(session: AsyncSession, *, payload: dict[str, Any], dry_run: bool, config: AppConfig, current_user: User) -> dict[str, object]:
    scenarios_raw = payload.get("scenarios")
    slots_raw = payload.get("slots")
    if not isinstance(scenarios_raw, dict) or not isinstance(slots_raw, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Payload import invalide.")
    validate_scenarios_document(scenarios_raw, "imported scenarios")
    validate_slots_document(slots_raw, "imported slots")
    if dry_run:
        return {"dry_run": True, "scenarios": len(scenarios_raw.get("scenarios", {})), "slots": len(slots_raw.get("slots", []))}

    before = await export_catalog(session, config=config)
    for record in list(await session.scalars(select(SlotRecord))):
        await session.delete(record)
    for record in list(await session.scalars(select(ScenarioShareRecord))):
        await session.delete(record)
    for record in list(await session.scalars(select(ScenarioRecord))):
        await session.delete(record)
    await session.commit()

    for scenario_id, definition in scenarios_raw.get("scenarios", {}).items():
        if not isinstance(definition, dict):
            continue
        owner = str(definition.get("user_id", definition.get("owner_user_id", "default")))
        session.add(ScenarioRecord(scenario_id=str(scenario_id), owner_user_id=owner, description=str(definition.get("description", "")), definition=definition))
        for shared_user in definition.get("user_ids", []):
            session.add(ScenarioShareRecord(scenario_id=str(scenario_id), user_id=str(shared_user)))
    for slot in slots_raw.get("slots", []):
        session.add(SlotRecord(slot_id=str(slot["id"]), scenario_id=str(slot["scenario"]), days=list(slot["days"]), start=str(slot["start"]), end=str(slot["end"]), enabled=True))
    await session.commit()
    config.runtime.scenarios_file.write_text(json.dumps(scenarios_raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    await sync_slots_file(session, config.runtime.slots_file)
    after = {"scenarios": len(scenarios_raw.get("scenarios", {})), "slots": len(slots_raw.get("slots", []))}
    await write_audit(session, actor_user_id=actor_id(current_user), action="admin.import_catalog", target_type="catalog", target_id="catalog", before=before, after=after)
    return {"dry_run": False, "imported": True}


async def prune_records(session: AsyncSession, *, jobs_days: int | None, audit_days: int | None, graph_notifications_days: int | None, current_user: User) -> dict[str, object]:
    removed = await prune_database_records(session, jobs_days=jobs_days, audit_days=audit_days, graph_notifications_days=graph_notifications_days)
    await write_audit(
        session,
        actor_user_id=actor_id(current_user),
        action="admin.retention_prune",
        target_type="database",
        target_id="retention",
        before={"jobs_days": jobs_days, "audit_days": audit_days, "graph_notifications_days": graph_notifications_days},
        after=removed,
    )
    return {"removed": removed}


async def save_setting(session: AsyncSession, *, key: str, value: dict[str, Any], description: str, current_user: User) -> dict[str, object]:
    record = await upsert_setting(session, key=key, value=value, description=description, updated_by=str(current_user.email))
    result = serialize_setting(record)
    await write_audit(session, actor_user_id=actor_id(current_user), action="admin.setting_upsert", target_type="setting", target_id=key, after=result)
    return result


async def remove_setting(session: AsyncSession, *, key: str, current_user: User) -> dict[str, object]:
    await delete_setting(session, key)
    await write_audit(session, actor_user_id=actor_id(current_user), action="admin.setting_delete", target_type="setting", target_id=key)
    return {"deleted": key}

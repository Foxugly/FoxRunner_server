from __future__ import annotations

from typing import Any

from api.models import (
    AppSettingRecord,
    AuditRecord,
    ExecutionHistoryRecord,
    GraphNotificationRecord,
    GraphSubscriptionRecord,
    JobEventRecord,
    JobRecord,
    User,
)
from api.time_utils import isoformat_utc


def serialize_user(user: User) -> dict[str, object]:
    return {
        "id": str(user.id),
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "is_verified": user.is_verified,
        "timezone_name": user.timezone_name,
    }


def serialize_job(record: JobRecord) -> dict[str, object]:
    return {
        "job_id": record.job_id,
        "celery_task_id": record.celery_task_id,
        "status": record.status,
        "created_at": isoformat_utc(record.created_at),
        "updated_at": isoformat_utc(record.updated_at),
        "started_at": isoformat_utc(record.started_at),
        "finished_at": isoformat_utc(record.finished_at),
        "kind": record.kind,
        "user_id": record.user_id,
        "target_id": record.target_id,
        "dry_run": record.dry_run,
        "exit_code": record.exit_code,
        "error": record.error,
        "payload": record.payload or {},
        "result": record.result or {},
    }


def serialize_job_event(record: JobEventRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "job_id": record.job_id,
        "event_type": record.event_type,
        "level": record.level,
        "message": record.message,
        "step": record.step,
        "payload": record.payload or {},
        "created_at": isoformat_utc(record.created_at),
    }


def serialize_audit(record: AuditRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "actor_user_id": record.actor_user_id,
        "action": record.action,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "before": record.before or {},
        "after": record.after or {},
        "created_at": isoformat_utc(record.created_at),
    }


def serialize_setting(record: AppSettingRecord) -> dict[str, Any]:
    return {
        "key": record.key,
        "value": record.value or {},
        "description": record.description,
        "updated_by": record.updated_by,
        "created_at": isoformat_utc(record.created_at),
        "updated_at": isoformat_utc(record.updated_at),
    }


def serialize_graph_subscription(record: GraphSubscriptionRecord) -> dict[str, Any]:
    return {
        "subscription_id": record.subscription_id,
        "resource": record.resource,
        "change_type": record.change_type,
        "notification_url": record.notification_url,
        "lifecycle_notification_url": record.lifecycle_notification_url,
        "expiration_datetime": isoformat_utc(record.expiration_datetime),
        "created_at": isoformat_utc(record.created_at),
        "updated_at": isoformat_utc(record.updated_at),
    }


def serialize_graph_notification(record: GraphNotificationRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "subscription_id": record.subscription_id,
        "change_type": record.change_type,
        "resource": record.resource,
        "tenant_id": record.tenant_id,
        "client_state": record.client_state,
        "lifecycle_event": record.lifecycle_event,
        "raw_payload": record.raw_payload or {},
        "created_at": isoformat_utc(record.created_at),
    }


def serialize_history(record: ExecutionHistoryRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "slot_key": record.slot_key,
        "slot_id": record.slot_id,
        "scenario_id": record.scenario_id,
        "execution_id": record.execution_id,
        "executed_at": isoformat_utc(record.executed_at) or "",
        "status": record.status,
        "step": record.step,
        "message": record.message,
        "updated_at": isoformat_utc(record.updated_at),
    }

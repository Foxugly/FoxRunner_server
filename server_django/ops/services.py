"""Ops domain services.

Centralises logic currently spread across ``api/jobs.py``,
``api/history.py``, ``api/audit.py``, ``api/settings.py``,
``api/artifacts.py``, ``api/graph.py``, ``api/monitoring.py``,
``api/retention.py``. Ninja handlers stay thin and delegate here.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import time
import uuid as _uuid_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from accounts.models import User
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import connection, transaction
from django.db.models import Max, QuerySet
from ninja.errors import HttpError

from ops.models import (
    AppSetting,
    AuditEntry,
    ExecutionHistory,
    GraphNotification,
    GraphSubscription,
    IdempotencyKey,
    Job,
    JobEvent,
)


def write_audit(
    *,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEntry:
    """Persist a single audit row.

    Sync port of ``api/audit.py::write_audit`` -- the FastAPI version was
    async only because it shared the ``AsyncSession``. The Django ORM is
    sync and Ninja handlers run in a sync request cycle, so the helper
    drops the ``await`` plumbing.

    Post-phase-5 the actor is stored as a ``ForeignKey(User)`` (column
    ``actor_user_id`` preserved). Callers pass the ``User`` object; the
    column may be ``NULL`` (the FK is nullable + ``SET_NULL``) so an
    explicit ``actor=None`` is allowed for system-generated audit rows.
    """
    return AuditEntry.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before or {},
        after=after or {},
    )


def list_audit(
    *,
    limit: int = 100,
    offset: int = 0,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> list[AuditEntry]:
    """Return audit rows matching the optional filters, newest first.

    ``actor_user_id`` is the API-shape filter (UUID string); the FK column
    name remains ``actor_user_id`` so the filter maps to ``actor_id`` on
    the ORM side.
    """
    qs = AuditEntry.objects.all().order_by("-id")
    if actor_user_id:
        qs = qs.filter(actor_id=actor_user_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    return list(qs[offset : offset + limit])


def count_audit(
    *,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> int:
    """Return the total number of audit rows matching the optional filters."""
    qs = AuditEntry.objects.all()
    if actor_user_id:
        qs = qs.filter(actor_id=actor_user_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    return qs.count()


# --------------------------------------------------------------------------
# Execution history (Phase 4.8). Sync port of ``api/history.py``. The
# legacy JSONL file at ``config.runtime.history_file`` is the source of
# truth for the CLI scheduler; the API endpoint synchronises it into
# ``ops.execution_history`` on every request so the DB-backed read stays
# consistent with what the CLI writes. This dual-stack hack goes away in
# Phase 13 once the CLI is rewritten to write directly to the DB.
# --------------------------------------------------------------------------


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp into a naive UTC datetime.

    The ``execution_history`` table stores naive datetimes (the SQLAlchemy
    schema does the same — see ``api/time_utils.py::db_utc``); the parser
    accepts both ``Z`` and ``+00:00`` suffixes.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _utc_iso(dt: datetime | None) -> str | None:
    """Serialise a datetime as ISO 8601 UTC with a ``Z`` suffix.

    The DB stores naive datetimes (UTC by convention) — assume UTC when
    the value is naive, otherwise convert. Mirrors
    ``api/time_utils.py::isoformat_utc``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@transaction.atomic
def import_history_jsonl(jsonl_path: pathlib.Path) -> int:
    """Sync the legacy history JSONL file into ``ExecutionHistory``.

    Idempotent: repeated calls update existing rows in-place via
    ``update_or_create`` keyed by the ``(execution_id, slot_id,
    scenario_id)`` triple that backs ``uq_execution_history_identity``.
    Returns the number of rows imported (one per non-empty JSONL line).

    Missing or empty file is OK — returns 0 without raising. This mirrors
    ``api/history.py::import_history_jsonl`` (which also tolerates the
    file being absent on first boot).
    """
    if not jsonl_path.exists():
        return 0
    imported = 0
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            slot_id = str(row.get("slot_id", ""))
            scenario_id = str(row.get("scenario_id", ""))
            if not slot_id or not scenario_id:
                continue
            execution_id_raw = row.get("execution_id")
            execution_id = str(execution_id_raw) if execution_id_raw else None
            ExecutionHistory.objects.update_or_create(
                execution_id=execution_id,
                slot_id=slot_id,
                scenario_id=scenario_id,
                defaults={
                    "slot_key": str(row.get("slot_key", "")),
                    "executed_at": _parse_iso_utc(str(row.get("executed_at"))),
                    "status": str(row.get("status", "")),
                    "step": str(row.get("step", "")),
                    "message": str(row.get("message", "")),
                    "updated_at": _parse_iso_utc(row.get("updated_at")) if row.get("updated_at") else None,
                },
            )
            imported += 1
    return imported


def list_history(
    *,
    status: str | None = None,
    slot_id: str | None = None,
    scenario_id: str | None = None,
    scenario_ids: set[str] | None = None,
    execution_id: str | None = None,
) -> QuerySet[ExecutionHistory]:
    """Return the execution-history queryset matching the optional filters.

    Order matches ``api/history.py::list_history`` (newest first, with
    ``id`` as a deterministic tie-breaker). When ``scenario_id`` is
    provided it takes precedence over ``scenario_ids`` (single-row
    permission filter); when ``scenario_ids`` is provided AND empty the
    queryset is forced empty (mirrors the FastAPI early-return path).
    """
    qs = ExecutionHistory.objects.all()
    if status is not None:
        qs = qs.filter(status=status)
    if slot_id is not None:
        qs = qs.filter(slot_id=slot_id)
    if scenario_id is not None:
        qs = qs.filter(scenario_id=scenario_id)
    elif scenario_ids is not None:
        if not scenario_ids:
            return ExecutionHistory.objects.none()
        qs = qs.filter(scenario_id__in=scenario_ids)
    if execution_id is not None:
        qs = qs.filter(execution_id=execution_id)
    return qs.order_by("-executed_at", "-id")


def serialize_history(record: ExecutionHistory) -> dict[str, Any]:
    """Serialise an ``ExecutionHistory`` row into the API payload shape.

    Mirrors ``api/serializers.py::serialize_history``. ``executed_at`` is
    rendered as an ISO 8601 UTC string with ``Z`` suffix (never ``None``
    — the DB column is non-nullable).
    """
    return {
        "id": record.id,
        "slot_key": record.slot_key,
        "slot_id": record.slot_id,
        "scenario_id": record.scenario_id,
        "execution_id": record.execution_id,
        "executed_at": _utc_iso(record.executed_at) or "",
        "status": record.status,
        "step": record.step,
        "message": record.message,
        "updated_at": _utc_iso(record.updated_at),
    }


# --------------------------------------------------------------------------
# Jobs + JobEvents (Phase 6). Sync Django-ORM port of ``api/jobs.py``. The
# FastAPI helpers were async-only because they shared the
# ``AsyncSession``; Django runs in a sync request cycle so the helpers
# drop the ``await`` plumbing and emit plain dicts / ORM rows.
# --------------------------------------------------------------------------


def serialize_job(record: Job) -> dict[str, Any]:
    """Serialise a :class:`Job` row into the API payload shape.

    Mirrors ``api/serializers.py::serialize_job``. ``user_id`` is coerced
    to ``str`` because the FK target is a ``UUIDField`` whose Python
    representation is ``uuid.UUID`` but the API contract expects a string.
    Timestamps are rendered with a ``Z`` suffix to match ADR 006.
    """
    return {
        "job_id": record.job_id,
        "celery_task_id": record.celery_task_id,
        "status": record.status,
        "created_at": _utc_iso(record.created_at),
        "updated_at": _utc_iso(record.updated_at),
        "started_at": _utc_iso(record.started_at),
        "finished_at": _utc_iso(record.finished_at),
        "kind": record.kind,
        "user_id": str(record.user_id),
        "target_id": record.target_id,
        "dry_run": record.dry_run,
        "exit_code": record.exit_code,
        "error": record.error,
        "payload": record.payload or {},
        "result": record.result or {},
    }


def serialize_job_event(record: JobEvent) -> dict[str, Any]:
    """Serialise a :class:`JobEvent` row.

    Mirrors ``api/serializers.py::serialize_job_event``.
    """
    return {
        "id": record.id,
        "job_id": record.job_id,
        "event_type": record.event_type,
        "level": record.level,
        "message": record.message,
        "step": record.step,
        "payload": record.payload or {},
        "created_at": _utc_iso(record.created_at),
    }


@transaction.atomic
def create_job(
    *,
    kind: str,
    user: User,
    target_id: str,
    dry_run: bool,
    payload: dict[str, Any] | None = None,
) -> Job:
    """Create a ``queued`` job + the initial ``queued`` event.

    Mirrors ``api/jobs.py::create_job``. The ``job_id`` is a hex UUID
    (uuid4 ``.hex``) so the value stays URL-safe; the FK ``user`` is the
    User object (post-phase-5 column ``user_id`` retained via
    ``db_column``).
    """
    record = Job.objects.create(
        job_id=uuid4().hex,
        user=user,
        kind=kind,
        target_id=target_id,
        dry_run=dry_run,
        status="queued",
        payload=payload or {},
        result={},
    )
    append_job_event(
        job_id=record.job_id,
        event_type="queued",
        message=f"Job {kind} mis en file.",
        payload={"target_id": target_id, "dry_run": dry_run},
    )
    return record


def get_job_for_user(job_id: str, user: User, *, is_superuser: bool = False) -> Job:
    """Return the :class:`Job` visible to ``user``.

    404 if the job does not exist; 403 if the owning user does not match
    (unless ``is_superuser``). Mirrors ``api/jobs.py::get_job_for_user``.
    """
    try:
        record = Job.objects.get(job_id=job_id)
    except Job.DoesNotExist:
        raise HttpError(404, "Job introuvable.") from None
    if not is_superuser and record.user_id != user.id:
        raise HttpError(403, "Acces job refuse.")
    return record


def list_jobs(
    *,
    user: User | None = None,
    status_filter: str | None = None,
    scenario_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Job], int]:
    """Return a ``(rows, total)`` tuple matching the optional filters.

    Sync mirror of ``api/jobs.py::list_jobs`` + ``count_jobs``. Ordering
    matches FastAPI (descending ``id``). ``scenario_id`` filters on
    ``target_id`` since the jobs table stores the scenario id in the
    generic ``target_id`` column.
    """
    qs = Job.objects.all().order_by("-id")
    if user is not None:
        qs = qs.filter(user=user)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if scenario_id:
        qs = qs.filter(target_id=scenario_id)
    total = qs.count()
    rows = list(qs[offset : offset + limit])
    return rows, total


def set_celery_task_id(job_id: str, celery_task_id: str) -> None:
    """Persist the Celery task id on the job row.

    Mirrors ``api/jobs.py::set_celery_task_id``. Raises if the job
    vanished between ``create_job`` and the dispatch call.
    """
    updated = Job.objects.filter(job_id=job_id).update(celery_task_id=celery_task_id)
    if updated == 0:
        raise RuntimeError(f"Job introuvable: {job_id}")


@transaction.atomic
def mark_job_cancelled(job: Job) -> None:
    """Flip a job to ``cancelled`` and emit the ``cancelled`` event.

    409 if the job is not in ``{queued, running}`` -- aligns with
    ``api/jobs.py::mark_job_cancelled``.
    """
    if job.status not in {"queued", "running"}:
        raise HttpError(409, "Seuls les jobs queued/running peuvent etre annules.")
    job.status = "cancelled"
    # Django (USE_TZ=True) prefers timezone-aware datetimes; the serializer
    # in ``serialize_job`` normalises both shapes back to UTC ``Z`` form.
    job.finished_at = datetime.now(UTC)
    job.save(update_fields=["status", "finished_at", "updated_at"])
    append_job_event(
        job_id=job.job_id,
        event_type="cancelled",
        message="Job annule.",
        level="warning",
    )


def append_job_event(
    *,
    job_id: str,
    event_type: str,
    message: str,
    level: str = "info",
    step: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JobEvent:
    """Insert a :class:`JobEvent` row.

    Mirrors ``api/jobs.py::append_job_event``. The FK is resolved by
    ``job_id`` (the ``to_field`` declared on the model), so callers only
    need the string job_id.
    """
    return JobEvent.objects.create(
        job_id=job_id,
        event_type=event_type,
        level=level,
        message=message,
        step=step,
        payload=payload or {},
    )


def list_job_events(job_id: str) -> list[JobEvent]:
    """Return events for a job ordered by creation time (then id).

    Mirrors ``api/jobs.py::list_job_events``.
    """
    qs = JobEvent.objects.filter(job_id=job_id).order_by("created_at", "id")
    return list(qs)


# --------------------------------------------------------------------------
# Orchestrators (Phase 6). Business logic that composes the low-level
# job helpers with the catalog visibility checks + Celery dispatch.
# Mirrors ``api/services/jobs.py``. These live here (not in the catalog
# app) because they produce ``Job`` + ``JobEvent`` + ``AuditEntry`` rows
# and should not depend on the catalog module's internals.
# --------------------------------------------------------------------------


def enqueue_scenario_job(
    *,
    user_id_str: str,
    scenario_id: str,
    dry_run: bool,
    current_user: User,
) -> dict[str, Any]:
    """Create a ``run_scenario`` job and dispatch the Celery task.

    Mirrors ``api/services/jobs.py::enqueue_scenario_job``. Resolves the
    target user against the catalog visibility rules (404 if the scenario
    is not owned or shared) then creates the job, fires the Celery
    ``run_scenario_job`` task, and records the ``submitted`` event.
    """
    # Import locally to avoid a circular dependency between ops.services,
    # catalog.services, and ops.tasks (tasks import ops.services).
    from accounts.permissions import resolve_user
    from catalog.services import get_scenario_for_user

    from ops.tasks import run_scenario_job

    target_user = resolve_user(user_id_str)
    get_scenario_for_user(scenario_id, target_user)
    job = create_job(
        kind="run_scenario",
        user=target_user,
        target_id=scenario_id,
        dry_run=dry_run,
        payload={"scenario_id": scenario_id},
    )
    task = run_scenario_job.delay(job.job_id, scenario_id, dry_run)
    set_celery_task_id(job.job_id, task.id)
    append_job_event(
        job_id=job.job_id,
        event_type="submitted",
        message="Tache Celery soumise.",
        payload={"celery_task_id": task.id},
    )
    job.refresh_from_db()
    return serialize_job(job)


def cancel_job_for_user(
    *,
    job_id: str,
    user_id_str: str,
    current_user: User,
) -> dict[str, Any]:
    """Cancel a job on behalf of ``current_user``.

    Mirrors ``api/services/jobs.py::cancel_job``. Revokes the Celery
    task (best-effort, ``terminate=False``), flips the job row to
    ``cancelled``, and writes a ``job.cancel`` audit row.
    """
    from accounts.permissions import resolve_user
    from foxrunner.celery import celery_app

    target_user = resolve_user(user_id_str)
    record = get_job_for_user(job_id, target_user, is_superuser=current_user.is_superuser)
    before = serialize_job(record)
    if record.celery_task_id:
        celery_app.control.revoke(record.celery_task_id, terminate=False)
    mark_job_cancelled(record)
    record.refresh_from_db()
    result = serialize_job(record)
    write_audit(
        actor=current_user,
        action="job.cancel",
        target_type="job",
        target_id=job_id,
        before=before,
        after=result,
    )
    return result


def retry_job_for_user(
    *,
    job_id: str,
    user_id_str: str,
    current_user: User,
) -> dict[str, Any]:
    """Clone a ``run_scenario`` job and dispatch the Celery task.

    Mirrors ``api/services/jobs.py::retry_job``. 409 when the source job
    is not ``kind="run_scenario"``. The new job's ``payload`` carries a
    ``retry_of`` pointer to the original so the UI can link them.
    """
    from accounts.permissions import resolve_user
    from catalog.services import get_scenario_for_user

    from ops.tasks import run_scenario_job

    target_user = resolve_user(user_id_str)
    source = get_job_for_user(job_id, target_user, is_superuser=current_user.is_superuser)
    if source.kind != "run_scenario":
        raise HttpError(409, "Retry non supporte pour ce type de job.")
    # Re-check scenario visibility at retry time -- the scenario may have
    # been unshared since the original job was submitted.
    get_scenario_for_user(source.target_id, target_user)
    retry = create_job(
        kind=source.kind,
        user=target_user,
        target_id=source.target_id,
        dry_run=source.dry_run,
        payload={**(source.payload or {}), "retry_of": source.job_id},
    )
    task = run_scenario_job.delay(retry.job_id, retry.target_id, retry.dry_run)
    set_celery_task_id(retry.job_id, task.id)
    append_job_event(
        job_id=retry.job_id,
        event_type="submitted",
        message="Retry Celery soumis.",
        payload={"celery_task_id": task.id, "retry_of": source.job_id},
    )
    retry.refresh_from_db()
    write_audit(
        actor=current_user,
        action="job.retry",
        target_type="job",
        target_id=source.job_id,
        after={"new_job_id": retry.job_id},
    )
    return serialize_job(retry)


# --------------------------------------------------------------------------
# Phase 7 -- Admin / monitoring / audit / settings / artifacts
# Sync ports of:
#   - api/services/admin.py   (admin management endpoints)
#   - api/settings.py         (AppSetting CRUD)
#   - api/retention.py        (jobs/audit/graph_notifications pruning)
#   - api/artifacts.py        (file listing / streaming / pruning)
#   - api/monitoring.py       (jobs / Graph aggregates)
# Every mutation ultimately writes an :class:`AuditEntry` via
# :func:`write_audit` so the audit trail is preserved 1:1 with FastAPI.
# --------------------------------------------------------------------------


ARTIFACT_KINDS: dict[str, str] = {"screenshots": "screenshots", "pages": "pages"}


def serialize_user(user: User) -> dict[str, Any]:
    """Serialise a :class:`accounts.models.User`. Mirrors ``api/serializers.py::serialize_user``."""
    return {
        "id": str(user.id),
        "email": user.email,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "is_verified": user.is_verified,
        "timezone_name": user.timezone_name,
        "date_joined": _utc_iso(user.date_joined),
    }


def serialize_audit(record: AuditEntry) -> dict[str, Any]:
    """Serialise an :class:`AuditEntry`. Mirrors ``api/serializers.py::serialize_audit``.

    ``actor_user_id`` is rendered as a ``str | None`` (the FK is nullable).
    """
    return {
        "id": record.id,
        "actor_user_id": str(record.actor_id) if record.actor_id else None,
        "action": record.action,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "before": record.before or {},
        "after": record.after or {},
        "created_at": _utc_iso(record.created_at),
    }


def serialize_setting(record: AppSetting) -> dict[str, Any]:
    """Serialise an :class:`AppSetting`. Mirrors ``api/serializers.py::serialize_setting``."""
    return {
        "key": record.key,
        "value": record.value or {},
        "description": record.description,
        "updated_by": record.updated_by,
        "created_at": _utc_iso(record.created_at),
        "updated_at": _utc_iso(record.updated_at),
    }


# --- Admin user management ------------------------------------------------


def update_admin_user(
    *,
    target_user_id: str,
    is_active: bool | None,
    is_superuser: bool | None,
    is_verified: bool | None,
    timezone_name: str | None,
    current_user: User,
) -> dict[str, Any]:
    """PATCH /admin/users/{target_user_id}.

    Sync port of ``api/services/admin.py::update_user``. Accepts a UUID or
    an email path argument; persists the changes; writes an audit row.
    """
    user = None
    with contextlib.suppress(User.DoesNotExist, ValueError, DjangoValidationError):
        user = User.objects.get(id=target_user_id)
    if user is None:
        try:
            user = User.objects.get(email=target_user_id)
        except User.DoesNotExist:
            raise HttpError(404, "Utilisateur introuvable.") from None
    before = serialize_user(user)
    if timezone_name is not None:
        try:
            ZoneInfo(timezone_name)
        except Exception as exc:
            raise HttpError(422, "Timezone IANA invalide.") from exc
    if is_active is not None:
        user.is_active = is_active
    if is_superuser is not None:
        user.is_superuser = is_superuser
    if is_verified is not None:
        user.is_verified = is_verified
    if timezone_name is not None:
        user.timezone_name = timezone_name
    user.save()
    user.refresh_from_db()
    after = serialize_user(user)
    write_audit(
        actor=current_user,
        action="admin.update_user",
        target_type="user",
        target_id=str(user.id),
        before=before,
        after=after,
    )
    return after


# --- Config / DB stats ----------------------------------------------------


def _readiness() -> dict[str, Any]:
    """Lightweight readiness probe -- only checks DB reachability.

    Mirrors the contract of ``api/health.py::readiness`` but trims the
    Redis / Celery / Graph checks: those depend on infra that isn't always
    reachable from a unit-test environment.
    """
    checks: dict[str, Any] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
    return {"status": "ok" if checks.get("database") == "ok" else "degraded", "checks": checks}


def config_checks() -> dict[str, Any]:
    """GET /admin/config-checks. Sync port of ``api/services/admin.py::config_checks``.

    Loads ``app.config`` lazily so unit tests that don't need it can avoid
    the import (the app config reads env vars at call time).
    """
    from app.config import load_config

    config = load_config()
    ready = _readiness()
    checks = dict(ready.get("checks", {}))
    checks.update(
        {
            "auth_secret_configured": bool(os.getenv("AUTH_SECRET") or os.getenv("DJANGO_SECRET_KEY")),
            "database_url_configured": bool(os.getenv("AUTH_DATABASE_URL") or os.getenv("DATABASE_URL")),
            "celery_broker_url_configured": bool(os.getenv("CELERY_BROKER_URL")),
            "celery_result_backend_configured": bool(os.getenv("CELERY_RESULT_BACKEND")),
            "scenarios_file_exists": config.runtime.scenarios_file.exists(),
            "slots_file_exists": config.runtime.slots_file.exists(),
            "artifacts_dir": str(config.runtime.artifacts_dir),
        }
    )
    return {"status": "ok" if checks.get("database") == "ok" else "degraded", "checks": checks}


def db_stats() -> dict[str, Any]:
    """GET /admin/db-stats. Sync port of ``api/services/admin.py::db_stats``.

    Reports per-table counts + the most-recent execution timestamp + the
    failed-jobs / graph-subscription expiring counters. ``last_execution_at``
    uses ``Max('executed_at')`` so it survives DB pruning.
    """
    # Local imports keep the module-level dependency surface small for
    # tests that don't exercise the catalog.
    from catalog.models import Scenario, ScenarioShare, Slot

    tables = {
        "users": User.objects.count(),
        "scenarios": Scenario.objects.count(),
        "scenario_shares": ScenarioShare.objects.count(),
        "slots": Slot.objects.count(),
        "jobs": Job.objects.count(),
        "job_events": JobEvent.objects.count(),
        "graph_subscriptions": GraphSubscription.objects.count(),
        "graph_notifications": GraphNotification.objects.count(),
        "audit_log": AuditEntry.objects.count(),
        "execution_history": ExecutionHistory.objects.count(),
        "app_settings": AppSetting.objects.count(),
        "idempotency_keys": IdempotencyKey.objects.count(),
    }
    last_execution_at = ExecutionHistory.objects.aggregate(value=Max("executed_at"))["value"]
    failed_jobs = Job.objects.filter(status="failed").count()
    expiring_before = datetime.now(UTC) + timedelta(hours=24)
    graph_subscriptions_expiring = GraphSubscription.objects.filter(
        expiration_datetime__isnull=False,
        expiration_datetime__lt=expiring_before.replace(tzinfo=None),
    ).count()
    return {
        "tables": tables,
        "last_execution_at": _utc_iso(last_execution_at),
        "failed_jobs": failed_jobs,
        "graph_subscriptions_expiring": graph_subscriptions_expiring,
    }


# --- Catalog export / import ----------------------------------------------


def _export_scenarios_document() -> dict[str, Any]:
    """Build the scenarios JSON document from the Scenario rows.

    Mirrors ``api/catalog.py::export_scenarios_document`` but does NOT
    re-read the on-disk JSON file (post-Phase-13 cutover the DB is
    canonical, and we don't want imports to drift onto file metadata).
    """
    from catalog.models import Scenario

    payload: dict[str, Any] = {"scenarios": {}}
    for record in Scenario.objects.all().order_by("scenario_id"):
        payload["scenarios"][record.scenario_id] = record.definition
    return payload


def _export_slots_document() -> dict[str, Any]:
    """Build the slots JSON document from the Slot rows. Mirrors ``api/catalog.py::export_slots_document``."""
    from catalog.models import Slot

    return {
        "slots": [
            {
                "id": record.slot_id,
                "days": record.days,
                "start": record.start,
                "end": record.end,
                "scenario": record.scenario_id,
            }
            for record in Slot.objects.filter(enabled=True).order_by("slot_id")
        ]
    }


def export_catalog() -> dict[str, Any]:
    """GET /admin/export. Sync port of ``api/services/admin.py::export_catalog``."""
    return {"scenarios": _export_scenarios_document(), "slots": _export_slots_document()}


def _resolve_owner_user(value: str | None) -> User | None:
    """Return the User row matching ``value`` (UUID), else ``None``.

    Post-Phase-5 the Scenario.owner column is an FK and the seed value
    ``"default"`` is no longer valid. The caller treats ``None`` as
    "skip this row" and increments the skipped counter.
    """
    if not value:
        return None
    try:
        parsed = _uuid_mod.UUID(str(value))
    except (TypeError, ValueError):
        return None
    try:
        return User.objects.get(id=parsed)
    except User.DoesNotExist:
        return None


@transaction.atomic
def _apply_catalog_import(scenarios_raw: dict[str, Any], slots_raw: dict[str, Any]) -> tuple[int, int, int]:
    """Replace the catalog rows with the import payload.

    Returns ``(scenarios_imported, slots_imported, scenarios_skipped)``.
    """
    from catalog.models import Scenario, ScenarioShare, Slot

    Slot.objects.all().delete()
    ScenarioShare.objects.all().delete()
    Scenario.objects.all().delete()

    valid_scenario_ids: set[str] = set()
    skipped = 0
    imported_scenarios = 0
    for scenario_id, definition in (scenarios_raw.get("scenarios") or {}).items():
        if not isinstance(definition, dict):
            continue
        owner_value = definition.get("user_id", definition.get("owner_user_id"))
        owner = _resolve_owner_user(owner_value if owner_value is None else str(owner_value))
        if owner is None:
            skipped += 1
            continue
        Scenario.objects.create(
            scenario_id=str(scenario_id),
            owner=owner,
            description=str(definition.get("description", "")),
            definition=definition,
        )
        valid_scenario_ids.add(str(scenario_id))
        imported_scenarios += 1
        for shared_user in definition.get("user_ids", []) or []:
            shared = _resolve_owner_user(str(shared_user))
            if shared is None:
                continue
            ScenarioShare.objects.get_or_create(
                scenario_id=str(scenario_id),
                user=shared,
            )
    imported_slots = 0
    for slot in slots_raw.get("slots", []) or []:
        scenario_id = str(slot.get("scenario", ""))
        if scenario_id not in valid_scenario_ids:
            # The Slot FK targets Scenario.scenario_id; orphan slots would
            # raise IntegrityError, so skip them silently.
            continue
        Slot.objects.create(
            slot_id=str(slot["id"]),
            scenario_id=scenario_id,
            days=list(slot.get("days") or []),
            start=str(slot["start"]),
            end=str(slot["end"]),
            enabled=True,
        )
        imported_slots += 1
    return imported_scenarios, imported_slots, skipped


def import_catalog(*, payload: dict[str, Any], dry_run: bool, current_user: User) -> dict[str, Any]:
    """POST /admin/import. Sync port of ``api/services/admin.py::import_catalog``.

    Quirks:
      * Validates the loose payload shape (``scenarios`` and ``slots`` MUST
        be objects) and returns 422 otherwise.
      * Captures the full export() BEFORE the wipe so the audit row carries
        the complete delta.
      * Skips scenario rows whose ``owner_user_id`` no longer maps to a real
        User (post-Phase-5 FK semantics) and reports the skipped count.
    """
    scenarios_raw = payload.get("scenarios")
    slots_raw = payload.get("slots")
    if not isinstance(scenarios_raw, dict) or not isinstance(slots_raw, dict):
        raise HttpError(422, "Payload import invalide.")
    # Best-effort schema validation -- mirror FastAPI behaviour by tolerating
    # absent loaders in the test environment.
    try:
        from scenarios.loader import validate_scenarios_document, validate_slots_document

        validate_scenarios_document(scenarios_raw, "imported scenarios")
        validate_slots_document(slots_raw, "imported slots")
    except HttpError:
        raise
    except Exception as exc:  # pragma: no cover - the loader raises ValueError
        raise HttpError(422, f"Validation echouee: {exc}") from exc

    if dry_run:
        return {
            "dry_run": True,
            "scenarios": len(scenarios_raw.get("scenarios") or {}),
            "slots": len(slots_raw.get("slots") or []),
        }
    before = export_catalog()
    imported_scenarios, imported_slots, skipped = _apply_catalog_import(scenarios_raw, slots_raw)
    after = {"scenarios": imported_scenarios, "slots": imported_slots, "skipped_scenarios": skipped}
    write_audit(
        actor=current_user,
        action="admin.import_catalog",
        target_type="catalog",
        target_id="catalog",
        before=before,
        after=after,
    )
    return {"dry_run": False, "imported": True, "skipped_scenarios": skipped}


# --- Retention ------------------------------------------------------------


def prune_database_records(
    *,
    jobs_days: int | None = None,
    audit_days: int | None = None,
    graph_notifications_days: int | None = None,
) -> dict[str, int]:
    """Sync port of ``api/retention.py::prune_database_records``.

    Three independent age cutoffs (one per table family). Returns the row
    counts removed -- jobs deletes cascade to job_events via the FK so we
    delete the events explicitly first to surface the count.
    """
    removed = {"jobs": 0, "job_events": 0, "audit": 0, "graph_notifications": 0}
    now = datetime.now(UTC).replace(tzinfo=None)

    if jobs_days is not None:
        cutoff = now - timedelta(days=jobs_days)
        job_ids = list(Job.objects.filter(finished_at__isnull=False, finished_at__lt=cutoff).values_list("job_id", flat=True))
        if job_ids:
            events_count = JobEvent.objects.filter(job_id__in=job_ids).count()
            JobEvent.objects.filter(job_id__in=job_ids).delete()
            removed["job_events"] = events_count
            jobs_count = Job.objects.filter(job_id__in=job_ids).count()
            Job.objects.filter(job_id__in=job_ids).delete()
            removed["jobs"] = jobs_count

    if audit_days is not None:
        cutoff = now - timedelta(days=audit_days)
        count = AuditEntry.objects.filter(created_at__lt=cutoff).count()
        AuditEntry.objects.filter(created_at__lt=cutoff).delete()
        removed["audit"] = count

    if graph_notifications_days is not None:
        cutoff = now - timedelta(days=graph_notifications_days)
        count = GraphNotification.objects.filter(created_at__lt=cutoff).count()
        GraphNotification.objects.filter(created_at__lt=cutoff).delete()
        removed["graph_notifications"] = count
    return removed


def prune_records(
    *,
    jobs_days: int | None,
    audit_days: int | None,
    graph_notifications_days: int | None,
    current_user: User,
) -> dict[str, Any]:
    """DELETE /admin/retention. Sync port of ``api/services/admin.py::prune_records``."""
    removed = prune_database_records(
        jobs_days=jobs_days,
        audit_days=audit_days,
        graph_notifications_days=graph_notifications_days,
    )
    write_audit(
        actor=current_user,
        action="admin.retention_prune",
        target_type="database",
        target_id="retention",
        before={
            "jobs_days": jobs_days,
            "audit_days": audit_days,
            "graph_notifications_days": graph_notifications_days,
        },
        after=removed,
    )
    return {"removed": removed}


# --- Settings -------------------------------------------------------------


def list_app_settings(*, limit: int, offset: int) -> tuple[list[AppSetting], int]:
    """Sync port of ``api/settings.py::list_settings`` + paginate."""
    qs = AppSetting.objects.all().order_by("key")
    total = qs.count()
    rows = list(qs[offset : offset + limit])
    return rows, total


def save_setting(
    *,
    key: str,
    value: dict[str, Any],
    description: str,
    current_user: User,
) -> dict[str, Any]:
    """PUT /admin/settings/{key}. Sync port of ``api/services/admin.py::save_setting``."""
    record, _created = AppSetting.objects.update_or_create(
        key=key,
        defaults={"value": value, "description": description, "updated_by": current_user.email},
    )
    result = serialize_setting(record)
    write_audit(
        actor=current_user,
        action="admin.setting_upsert",
        target_type="setting",
        target_id=key,
        after=result,
    )
    return result


def remove_setting(*, key: str, current_user: User) -> dict[str, Any]:
    """DELETE /admin/settings/{key}. Sync port of ``api/services/admin.py::remove_setting``."""
    try:
        record = AppSetting.objects.get(key=key)
    except AppSetting.DoesNotExist:
        raise HttpError(404, "Setting introuvable.") from None
    record.delete()
    write_audit(
        actor=current_user,
        action="admin.setting_delete",
        target_type="setting",
        target_id=key,
    )
    return {"deleted": key}


# --- Artifacts ------------------------------------------------------------


def _artifacts_dir() -> Path:
    """Resolve the configured artifacts directory at call time.

    Lazily reads the AppConfig so unit tests can override
    ``APP_ARTIFACTS_DIR`` per-test via env vars without import-time caching.
    """
    from app.config import load_config

    return load_config().runtime.artifacts_dir


def list_artifacts(*, limit: int, offset: int, base_dir: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    """GET /artifacts. Sync port of ``api/artifacts.py::list_artifacts``.

    ``base_dir`` override is provided for tests that monkey-patch the
    artifacts directory.
    """
    artifacts_dir = base_dir if base_dir is not None else _artifacts_dir()
    rows: list[dict[str, Any]] = []
    for kind, folder in ARTIFACT_KINDS.items():
        base = artifacts_dir / folder
        if not base.exists():
            continue
        for path in sorted(item for item in base.iterdir() if item.is_file()):
            stat = path.stat()
            rows.append({"kind": kind, "name": path.name, "size": stat.st_size, "updated_at": stat.st_mtime})
    total = len(rows)
    return rows[offset : offset + limit], total


def artifact_path(kind: str, name: str, *, base_dir: Path | None = None) -> Path:
    """Resolve the absolute path for an artifact request, with traversal protection.

    Mirrors the validation logic in ``api/artifacts.py::artifact_response``:
    ``kind`` must be one of ``ARTIFACT_KINDS``; ``name`` must not contain
    a path separator; the file must exist.
    """
    if kind not in ARTIFACT_KINDS:
        raise HttpError(404, "Type d'artifact introuvable.")
    if "/" in name or "\\" in name:
        raise HttpError(400, "Nom d'artifact invalide.")
    artifacts_dir = base_dir if base_dir is not None else _artifacts_dir()
    path = artifacts_dir / ARTIFACT_KINDS[kind] / name
    if not path.exists() or not path.is_file():
        raise HttpError(404, "Artifact introuvable.")
    return path


def prune_artifacts(*, older_than_days: int, current_user: User, base_dir: Path | None = None) -> dict[str, Any]:
    """DELETE /artifacts. Sync port of ``api/artifacts.py::prune_artifacts``.

    Returns ``{"removed": int}`` and writes an audit row.
    """
    artifacts_dir = base_dir if base_dir is not None else _artifacts_dir()
    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for kind in ARTIFACT_KINDS:
        base = artifacts_dir / kind
        if not base.exists():
            continue
        for path in base.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
    write_audit(
        actor=current_user,
        action="artifacts.prune",
        target_type="artifacts",
        target_id=str(artifacts_dir),
        before={"older_than_days": older_than_days},
        after={"removed": removed},
    )
    return {"removed": removed}


# --- Monitoring -----------------------------------------------------------


def monitoring_summary(*, stuck_after_minutes: int = 30, graph_expiring_hours: int = 24) -> dict[str, Any]:
    """GET /monitoring/summary. Sync port of ``api/monitoring.py::monitoring_summary``.

    ``average_duration_seconds`` is computed from the first 1000 completed
    jobs to bound query cost (the FastAPI version does the same).
    """
    now = datetime.now(UTC)
    stuck_before = now - timedelta(minutes=stuck_after_minutes)
    expiring_before = now + timedelta(hours=graph_expiring_hours)

    total_jobs = Job.objects.count()
    failed_jobs = Job.objects.filter(status="failed").count()
    queued_jobs = Job.objects.filter(status="queued").count()
    running_jobs = Job.objects.filter(status="running").count()
    by_status = {row["status"]: int(row["c"]) for row in Job.objects.values("status").annotate(c=models_count())}
    stuck_jobs = Job.objects.filter(
        status__in=("queued", "running"),
        updated_at__lt=stuck_before.replace(tzinfo=None),
    ).count()
    expiring = GraphSubscription.objects.filter(
        expiration_datetime__isnull=False,
        expiration_datetime__lt=expiring_before.replace(tzinfo=None),
    ).count()
    completed_jobs = list(Job.objects.filter(started_at__isnull=False, finished_at__isnull=False).order_by("-id")[:1000])
    durations = [(job.finished_at - job.started_at).total_seconds() for job in completed_jobs if job.finished_at and job.started_at]
    average_duration = sum(durations) / len(durations) if durations else None
    return {
        "jobs": {
            "total": total_jobs,
            "failed": failed_jobs,
            "queued": queued_jobs,
            "running": running_jobs,
            "stuck": stuck_jobs,
            "by_status": by_status,
            "average_duration_seconds": average_duration,
        },
        "graph": {
            "subscriptions_expiring": expiring,
            "expiring_within_hours": graph_expiring_hours,
        },
    }


def models_count():
    """Tiny helper -- keeps the ``Count`` import lazy so the module-level
    import block stays grouped with Django ORM symbols.
    """
    from django.db.models import Count

    return Count("id")


def metrics_text() -> str:
    """GET /metrics. Returns Prometheus text exposition (text/plain; v=0.0.4).

    Sync port of ``api/routers/runtime.py::metrics_endpoint``. Uses the
    historical ``foxrunner_*`` namespace agreed in the Phase 7 plan
    (FastAPI emits ``smiley_*`` -- the Django swap is the moment to align
    on the project name).
    """
    summary = monitoring_summary()
    jobs = summary["jobs"]
    graph = summary["graph"]
    avg = jobs["average_duration_seconds"]
    avg_value = "NaN" if avg is None else f"{avg:.6f}"
    lines = [
        "# HELP foxrunner_jobs_total Total persisted jobs.",
        "# TYPE foxrunner_jobs_total gauge",
        f"foxrunner_jobs_total {jobs['total']}",
        "# HELP foxrunner_jobs_failed Failed jobs.",
        "# TYPE foxrunner_jobs_failed gauge",
        f"foxrunner_jobs_failed {jobs['failed']}",
        "# HELP foxrunner_jobs_queued Queued jobs.",
        "# TYPE foxrunner_jobs_queued gauge",
        f"foxrunner_jobs_queued {jobs['queued']}",
        "# HELP foxrunner_jobs_running Running jobs.",
        "# TYPE foxrunner_jobs_running gauge",
        f"foxrunner_jobs_running {jobs['running']}",
        "# HELP foxrunner_jobs_stuck Queued or running jobs older than threshold.",
        "# TYPE foxrunner_jobs_stuck gauge",
        f"foxrunner_jobs_stuck {jobs['stuck']}",
        "# HELP foxrunner_jobs_average_duration_seconds Average duration of completed jobs.",
        "# TYPE foxrunner_jobs_average_duration_seconds gauge",
        f"foxrunner_jobs_average_duration_seconds {avg_value}",
        "# HELP foxrunner_jobs_by_status Jobs grouped by status.",
        "# TYPE foxrunner_jobs_by_status gauge",
        *[f'foxrunner_jobs_by_status{{status="{status}"}} {count}' for status, count in sorted(jobs.get("by_status", {}).items())],
        "# HELP foxrunner_graph_subscriptions_expiring Graph subscriptions close to expiration.",
        "# TYPE foxrunner_graph_subscriptions_expiring gauge",
        f"foxrunner_graph_subscriptions_expiring {graph['subscriptions_expiring']}",
    ]
    return "\n".join(lines) + "\n"

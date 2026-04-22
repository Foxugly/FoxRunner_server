"""Ops domain services.

Centralises logic currently spread across ``api/jobs.py``,
``api/history.py``, ``api/audit.py``, ``api/settings.py``,
``api/artifacts.py``, ``api/graph.py``, ``api/monitoring.py``,
``api/retention.py``. Ninja handlers stay thin and delegate here.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from accounts.models import User
from django.db import transaction
from django.db.models import QuerySet
from ninja.errors import HttpError

from ops.models import AuditEntry, ExecutionHistory, Job, JobEvent


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

"""Shared scenario-job runner used by both the Celery task and the inline
dispatcher. Translates engine StepEvents into JobEvent rows and updates the
Job row. The DB is the single source of truth the frontend polls."""

from __future__ import annotations

import traceback
from datetime import UTC, datetime

from scenarios.events import StepEvent


def _run_scenario_with_sink(scenario_id: str, dry_run: bool, on_event, execution_id: str | None = None) -> int:
    """Build the DB-backed scheduler service and run one scenario with a sink.

    Kept as a tiny seam so tests can patch it without spinning Selenium.
    """
    from catalog.services import build_service_from_db

    service = build_service_from_db()
    return service.run_scenario(scenario_id, dry_run=dry_run, on_event=on_event, execution_id=execution_id)


def execute_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict:
    from ops.models import Job
    from ops.services import append_job_event

    try:
        record = Job.objects.get(job_id=job_id)
    except Job.DoesNotExist as exc:
        # Defensive: the Job row may have been pruned between dispatch and
        # execution. Surface a clean, typed error (matches the convention in
        # ``ops.services`` which raises ``RuntimeError(f"Job introuvable: ...")``)
        # rather than leaking the ORM's ``DoesNotExist``.
        raise RuntimeError(f"Job introuvable: {job_id}") from exc
    record.status = "running"
    record.started_at = datetime.now(UTC)
    record.save(update_fields=["status", "started_at", "updated_at"])
    append_job_event(job_id=job_id, event_type="running", message="Exécution démarrée.")

    def sink(ev: StepEvent) -> None:
        append_job_event(
            job_id=job_id,
            event_type=ev.event_type,
            level=ev.level,
            message=ev.message,
            step=ev.step_id,
            payload={"step_type": ev.step_type, "duration_ms": ev.duration_ms, "traceback": ev.traceback, **(ev.payload or {})},
        )

    try:
        exit_code = _run_scenario_with_sink(scenario_id, dry_run, sink, job_id)
        record.refresh_from_db()
        record.status = "success" if exit_code == 0 else "failed"
        record.exit_code = exit_code
        record.finished_at = datetime.now(UTC)
        record.result = {"scenario_id": scenario_id, "dry_run": dry_run}
        record.save(update_fields=["status", "exit_code", "finished_at", "result", "updated_at"])
        append_job_event(
            job_id=job_id,
            event_type=record.status,
            level="info" if exit_code == 0 else "error",
            message=f"Exécution terminée (exit_code={exit_code}).",
            payload={"exit_code": exit_code},
        )
        return {"job_id": job_id, "exit_code": exit_code}
    except Exception as exc:
        record.refresh_from_db()
        record.status = "failed"
        record.error = traceback.format_exc()
        record.finished_at = datetime.now(UTC)
        record.save(update_fields=["status", "error", "finished_at", "updated_at"])
        append_job_event(job_id=job_id, event_type="failed", level="error", message=str(exc))
        raise

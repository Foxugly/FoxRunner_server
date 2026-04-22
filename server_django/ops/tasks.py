"""Celery tasks (sync Django-ORM rewrite of ``api/tasks.py``).

Three tasks replicated from the FastAPI backend:

- ``run_scenario_job(job_id, scenario_id, dry_run)`` — drives the Selenium
  runner through ``scheduler.service.SchedulerService`` on behalf of an
  API-submitted job. Updates ``Job.status`` + timestamps + emits
  :class:`~ops.models.JobEvent` rows along the way.
- ``renew_graph_subscriptions_task()`` — placeholder until Phase 8 (real
  Microsoft Graph integration). Beat keeps calling it -- it just returns
  a marker dict so operators can distinguish the stub from a silent
  scheduling gap.
- ``prune_retention_task()`` — placeholder until Phase 7 (admin +
  retention). Same rationale: beat keeps calling it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from foxrunner.celery import celery_app

logger = logging.getLogger("smiley.api.tasks")


def _utc_now() -> datetime:
    """Return a timezone-aware UTC datetime for the DB columns.

    Django (``USE_TZ=True``) prefers aware datetimes; serializers in
    ``ops/services.py`` normalise the value back to UTC before rendering
    so the ``Z`` suffix stays consistent regardless of stored shape.
    """
    return datetime.now(UTC)


@celery_app.task(name="ops.tasks.run_scenario_job")
def run_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict:
    """Execute a scenario on behalf of an API-submitted job.

    Sync Django-ORM port of ``api/tasks.py::_run_scenario_job``. Each
    phase (running / success-or-failed / error) writes both the Job row
    and a matching :class:`~ops.models.JobEvent`. On exception the job is
    marked ``failed`` with the exception message captured in
    ``Job.error`` and re-raised so Celery records the failure.
    """
    # Import late so the module imports work even when the Django app
    # registry isn't ready yet (e.g. at Celery beat boot time before
    # autodiscover runs).
    from catalog.services import build_service_from_db

    from ops.models import Job
    from ops.services import append_job_event

    try:
        record = Job.objects.get(job_id=job_id)
    except Job.DoesNotExist as exc:
        raise RuntimeError(f"Job introuvable: {job_id}") from exc

    record.status = "running"
    record.started_at = _utc_now()
    record.save(update_fields=["status", "started_at", "updated_at"])
    append_job_event(
        job_id=job_id,
        event_type="running",
        message="Execution du scenario demarree.",
    )

    try:
        service = build_service_from_db()
        exit_code = service.run_scenario(scenario_id, dry_run=dry_run)
        record.refresh_from_db()
        record.status = "success" if exit_code == 0 else "failed"
        record.exit_code = exit_code
        record.finished_at = _utc_now()
        record.result = {"scenario_id": scenario_id, "dry_run": dry_run}
        record.save(update_fields=["status", "exit_code", "finished_at", "result", "updated_at"])
        append_job_event(
            job_id=job_id,
            event_type=record.status,
            level="info" if exit_code == 0 else "error",
            message=f"Execution terminee avec exit_code={exit_code}.",
            payload={"exit_code": exit_code},
        )
        return {"job_id": job_id, "exit_code": exit_code}
    except Exception as exc:
        record.refresh_from_db()
        record.status = "failed"
        record.error = str(exc)
        record.finished_at = _utc_now()
        record.save(update_fields=["status", "error", "finished_at", "updated_at"])
        append_job_event(
            job_id=job_id,
            event_type="failed",
            level="error",
            message=str(exc),
        )
        raise


@celery_app.task(name="ops.tasks.renew_graph_subscriptions_task")
def renew_graph_subscriptions_task() -> dict:
    """Microsoft Graph subscription renewal -- implemented in Phase 8.

    Returning the marker keeps Celery beat happy (the scheduled entry
    keeps firing) while operators can still see the task is a stub.
    """
    logger.info("renew_graph_subscriptions_task stub -- implementation lands in phase 8")
    return {"enabled": False, "reason": "implemented_in_phase_8"}


@celery_app.task(name="ops.tasks.prune_retention_task")
def prune_retention_task() -> dict:
    """Database + artifact retention pruning -- implemented in Phase 7.

    Same rationale as :func:`renew_graph_subscriptions_task`: beat keeps
    firing, the marker tells operators it's a stub.
    """
    logger.info("prune_retention_task stub -- implementation lands in phase 7")
    return {"enabled": False, "reason": "implemented_in_phase_7"}

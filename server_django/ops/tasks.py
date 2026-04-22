"""Celery tasks (sync Django-ORM rewrite of ``api/tasks.py``).

Three tasks replicated from the FastAPI backend:

- ``run_scenario_job(job_id, scenario_id, dry_run)`` — drives the Selenium
  runner through ``scheduler.service.SchedulerService`` on behalf of an
  API-submitted job.
- ``renew_graph_subscriptions_task()`` — periodic renewal, scheduled by
  Celery beat.
- ``prune_retention_task()`` — periodic database retention pruning.

Populated during phase 6. Until then Celery beat stays pointed at the
FastAPI tasks so operational jobs keep running during migration.
"""

from __future__ import annotations

import logging

from foxrunner.celery import celery_app

logger = logging.getLogger("smiley.api.tasks")


@celery_app.task(name="ops.tasks.run_scenario_job")
def run_scenario_job(job_id: str, scenario_id: str, dry_run: bool) -> dict:
    logger.warning("run_scenario_job not yet implemented in Django backend (job_id=%s)", job_id)
    return {"job_id": job_id, "status": "skipped", "reason": "migration_in_progress"}


@celery_app.task(name="ops.tasks.renew_graph_subscriptions_task")
def renew_graph_subscriptions_task() -> dict:
    logger.warning("renew_graph_subscriptions_task not yet implemented in Django backend")
    return {"enabled": False, "reason": "migration_in_progress"}


@celery_app.task(name="ops.tasks.prune_retention_task")
def prune_retention_task() -> dict:
    logger.warning("prune_retention_task not yet implemented in Django backend")
    return {"enabled": False, "reason": "migration_in_progress"}

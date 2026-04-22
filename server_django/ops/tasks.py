"""Celery tasks (sync Django-ORM rewrite of ``api/tasks.py``).

Three tasks replicated from the FastAPI backend:

- ``run_scenario_job(job_id, scenario_id, dry_run)`` -- drives the Selenium
  runner through ``scheduler.service.SchedulerService`` on behalf of an
  API-submitted job. Updates ``Job.status`` + timestamps + emits
  :class:`~ops.models.JobEvent` rows along the way.
- ``renew_graph_subscriptions_task()`` -- renews Microsoft Graph
  subscriptions whose ``expirationDateTime`` falls within
  ``GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS`` of now. Sync port of
  ``api.tasks._renew_graph_subscriptions_task``.
- ``prune_retention_task()`` -- prunes old jobs, audit rows, Graph
  notifications and on-disk artifacts based on ``RETENTION_*`` env vars.
  Sync port of ``api.tasks._prune_retention_task``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from foxrunner.celery import celery_app

logger = logging.getLogger("smiley.api.tasks")


def _utc_now() -> datetime:
    """Return a timezone-aware UTC datetime for the DB columns.

    Django (``USE_TZ=True``) prefers aware datetimes; serializers in
    ``ops/services.py`` normalise the value back to UTC before rendering
    so the ``Z`` suffix stays consistent regardless of stored shape.
    """
    return datetime.now(UTC)


def _utc_iso_z(value: datetime) -> str:
    """Serialise a UTC datetime as ISO 8601 with a trailing ``Z``.

    Mirrors ``api.time_utils.isoformat_utc`` -- Microsoft Graph requires
    the ``Z`` suffix, ``isoformat()`` alone produces ``+00:00``.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_graph_iso(value: str) -> datetime:
    """Parse a Graph ISO 8601 timestamp into a naive UTC datetime.

    Mirrors ``api.time_utils.parse_utc`` + ``db_utc``; the DB columns
    store naive datetimes (UTC by convention).
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _optional_int(name: str) -> int | None:
    """Read an env var as ``int`` or ``None`` when unset/empty.

    Mirrors ``api.tasks._optional_int``.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    return int(raw)


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
    """Renew Graph subscriptions whose expiration is within the renew window.

    Sync port of ``api/tasks.py::_renew_graph_subscriptions_task``.

    Behaviour:
      * Honours ``GRAPH_SUBSCRIPTION_RENEW_ENABLED`` (default ``"true"``);
        returns ``{"enabled": False, "renewed": 0}`` when disabled.
      * Returns ``{"enabled": True, "configured": False, "renewed": 0}``
        when ``ops.graph.is_graph_configured()`` reports missing creds --
        the task should not fire actual HTTP calls in that case.
      * Reads ``GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS`` (default 24) for
        the renew window and ``GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS``
        (default 48) for the new ``expirationDateTime`` we send to Graph.
      * Iterates ``GraphSubscription`` rows whose ``expiration_datetime``
        is non-null AND falls before the renew window; calls
        ``ops.graph.renew_graph_subscription`` for each, then updates the
        row from the response (``expiration_datetime`` reparsed from
        Graph + ``raw_payload`` redacted via ``api.redaction.redact``).
      * Per-row failures are logged + appended to the ``errors`` list and
        do not abort the loop.

    Returns ``{"enabled": True, "configured": True, "renewed": <int>,
    "errors": [...]}`` for the success path.
    """
    from ops.graph import is_graph_configured, renew_graph_subscription
    from ops.models import GraphSubscription

    if os.getenv("GRAPH_SUBSCRIPTION_RENEW_ENABLED", "true").lower() != "true":
        return {"enabled": False, "renewed": 0}
    if not is_graph_configured():
        return {"enabled": True, "configured": False, "renewed": 0}

    renew_before_hours = int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS", "24"))
    extension_hours = int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS", "48"))
    now = _utc_now()
    renew_before = now + timedelta(hours=renew_before_hours)
    new_expiration = now + timedelta(hours=extension_hours)
    new_expiration_iso = _utc_iso_z(new_expiration)
    renewed = 0
    errors: list[dict[str, str]] = []

    # The DB column stores naive datetimes (UTC by convention); compare
    # against the naive equivalent of ``renew_before``.
    records = list(
        GraphSubscription.objects.filter(
            expiration_datetime__isnull=False,
            expiration_datetime__lt=renew_before.replace(tzinfo=None),
        )
    )
    # Imported lazily so the test suite can monkey-patch without forcing
    # the FastAPI module load at Celery boot.
    from api.redaction import redact

    for record in records:
        try:
            raw = renew_graph_subscription(record.subscription_id, new_expiration_iso)
            raw_expiration = raw.get("expirationDateTime", new_expiration_iso)
            record.expiration_datetime = _parse_graph_iso(raw_expiration)
            record.raw_payload = redact(raw)
            record.save(update_fields=["expiration_datetime", "raw_payload", "updated_at"])
            renewed += 1
        except Exception as exc:
            logger.error("Graph subscription renewal failed for %s: %s", record.subscription_id, exc)
            errors.append({"subscription_id": record.subscription_id, "error": str(exc)})

    return {"enabled": True, "configured": True, "renewed": renewed, "errors": errors}


@celery_app.task(name="ops.tasks.prune_retention_task")
def prune_retention_task() -> dict:
    """Prune old jobs / audit / graph notifications + artifacts.

    Sync port of ``api/tasks.py::_prune_retention_task``.

    Behaviour:
      * Honours ``RETENTION_PRUNE_ENABLED`` (default ``"false"``);
        returns ``{"enabled": False}`` when disabled so the beat loop
        knows the task ran and decided to skip.
      * Reads four optional ``RETENTION_*_DAYS`` env vars; ``None`` for
        any of them means "do not prune that table family".
      * Calls ``ops.services.prune_database_records`` for the DB part
        (jobs / audit / graph_notifications) and
        ``ops.services.prune_artifacts`` for the on-disk part. Artifacts
        pruning is opt-in via ``RETENTION_ARTIFACTS_DAYS``.
      * The artifacts helper signature in ops.services takes
        ``current_user`` (it writes an audit row); we pass ``None`` here
        because the task has no actor -- ops.services.prune_artifacts
        accepts that and produces a system-actor audit entry.
        (To stay loosely coupled and avoid changing prune_artifacts, the
        artifacts pruning is performed inline via the lower-level
        os/path scan -- mirrors ``api.tasks`` which calls
        ``api.artifacts.prune_artifacts(path, older_than_days=...)``
        without an audit hop.)

    Returns ``{"enabled": True, "removed": {...}}`` on success.
    """
    if os.getenv("RETENTION_PRUNE_ENABLED", "false").lower() != "true":
        return {"enabled": False}

    from ops.services import prune_database_records

    jobs_days = _optional_int("RETENTION_JOBS_DAYS")
    audit_days = _optional_int("RETENTION_AUDIT_DAYS")
    graph_days = _optional_int("RETENTION_GRAPH_NOTIFICATIONS_DAYS")
    artifact_days = _optional_int("RETENTION_ARTIFACTS_DAYS")

    removed = prune_database_records(
        jobs_days=jobs_days,
        audit_days=audit_days,
        graph_notifications_days=graph_days,
    )
    artifact_removed = 0
    if artifact_days is not None:
        artifact_removed = _prune_artifacts_files(artifact_days)
    return {"enabled": True, "removed": {**removed, "artifacts": artifact_removed}}


def _prune_artifacts_files(older_than_days: int) -> int:
    """Delete artifact files older than ``older_than_days``; return removed count.

    Inline mirror of ``api.artifacts.prune_artifacts``: scans the kinds
    declared in ``ops.services.ARTIFACT_KINDS`` and removes any file
    whose mtime predates the cutoff. We avoid calling
    ``ops.services.prune_artifacts`` directly because its FastAPI parity
    contract requires a ``current_user`` for the audit row -- the
    Celery-beat invocation has no actor.
    """
    import time

    from app.config import load_config
    from ops.services import ARTIFACT_KINDS

    artifacts_dir = load_config().runtime.artifacts_dir
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
    return removed

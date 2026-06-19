"""``manage.py reconcile_jobs [--older-than-minutes N]``.

Fails jobs left stuck in ``queued``/``running`` past a staleness threshold.

A job only reaches a terminal state when the process driving it
(``ops.runner_bridge.execute_scenario_job``, via a Celery worker or the
inline dispatcher thread) finishes. If that process dies mid-run -- a
worker crash, a web-process restart while a job runs inline, an OOM kill
-- the ``Job`` row is orphaned in ``running`` (or never leaves ``queued``)
forever, and the live view shows a job that will never complete.

This command reconciles those: any ``queued``/``running`` job whose
``updated_at`` predates ``now - older_than_minutes`` is marked ``failed``
with an explanatory ``error`` and a ``failed`` :class:`~ops.models.JobEvent`
so the failure shows up in the live event stream like any other.

Run it on startup and/or periodically (a Celery-beat task can call the
same helper). The default threshold comes from ``settings.JOB_STALE_MINUTES``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from ops.models import Job
from ops.services import append_job_event

STALE_STATUSES = ("queued", "running")


def reconcile_stale_jobs(older_than_minutes: int) -> list[str]:
    """Fail every stale ``queued``/``running`` job; return the job ids touched.

    A job is stale when its ``updated_at`` is older than the cutoff. Each
    reconciled job gets ``status="failed"``, an explanatory ``error``, a
    ``finished_at`` stamp, and a ``failed`` JobEvent.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    # Job.updated_at stores naive UTC datetimes (USE_TZ normalises on read);
    # compare against the naive cutoff like monitoring_summary does.
    stale = list(
        Job.objects.filter(
            status__in=STALE_STATUSES,
            updated_at__lt=cutoff.replace(tzinfo=None),
        )
    )
    reconciled: list[str] = []
    for job in stale:
        message = f"Job orphelin reconcilie: bloque en '{job.status}' depuis plus de {older_than_minutes} min."
        job.status = "failed"
        job.error = message
        job.finished_at = datetime.now(UTC)
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        append_job_event(
            job_id=job.job_id,
            event_type="failed",
            level="error",
            message=message,
            payload={"reconciled": True, "older_than_minutes": older_than_minutes},
        )
        reconciled.append(job.job_id)
    return reconciled


class Command(BaseCommand):
    help = "Mark orphaned queued/running jobs (older than a threshold) as failed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-minutes",
            type=int,
            default=None,
            help="Staleness threshold in minutes (default: settings.JOB_STALE_MINUTES).",
        )

    def handle(self, *args, older_than_minutes: int | None = None, **opts):
        threshold = older_than_minutes if older_than_minutes is not None else int(getattr(settings, "JOB_STALE_MINUTES", 30))
        reconciled = reconcile_stale_jobs(threshold)
        self.stdout.write(self.style.SUCCESS(f"reconciled:{len(reconciled)}"))

"""Tests for the ``reconcile_jobs`` management command + helper.

A job left in ``queued``/``running`` past the staleness threshold (the
process driving it died mid-run) must be reconciled to ``failed`` with an
explanatory error + a ``failed`` JobEvent, while fresh jobs and already
terminal jobs are untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from ops.management.commands.reconcile_jobs import reconcile_stale_jobs
from ops.models import Job, JobEvent


class ReconcileJobsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="ops@example.com", password="password123!")

    def _make_job(self, job_id: str, status: str, age_minutes: int) -> Job:
        job = Job.objects.create(
            job_id=job_id,
            user=self.user,
            kind="run_scenario",
            target_id="sc-1",
            status=status,
            dry_run=True,
        )
        # updated_at is auto_now; bypass it with a raw UPDATE to backdate.
        stale_at = datetime.now(UTC) - timedelta(minutes=age_minutes)
        Job.objects.filter(pk=job.pk).update(updated_at=stale_at)
        return job

    def test_stale_running_job_is_failed_with_event(self):
        self._make_job("stale-run", "running", age_minutes=60)
        reconciled = reconcile_stale_jobs(older_than_minutes=30)
        self.assertEqual(reconciled, ["stale-run"])
        job = Job.objects.get(job_id="stale-run")
        self.assertEqual(job.status, "failed")
        self.assertTrue(job.error)
        self.assertIsNotNone(job.finished_at)
        events = JobEvent.objects.filter(job_id="stale-run", event_type="failed")
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().level, "error")
        self.assertTrue(events.first().payload.get("reconciled"))

    def test_stale_queued_job_is_failed(self):
        self._make_job("stale-queued", "queued", age_minutes=45)
        reconciled = reconcile_stale_jobs(older_than_minutes=30)
        self.assertEqual(reconciled, ["stale-queued"])
        self.assertEqual(Job.objects.get(job_id="stale-queued").status, "failed")

    def test_fresh_job_is_untouched(self):
        self._make_job("fresh", "running", age_minutes=5)
        reconciled = reconcile_stale_jobs(older_than_minutes=30)
        self.assertEqual(reconciled, [])
        self.assertEqual(Job.objects.get(job_id="fresh").status, "running")

    def test_terminal_job_is_untouched(self):
        self._make_job("done", "success", age_minutes=120)
        reconciled = reconcile_stale_jobs(older_than_minutes=30)
        self.assertEqual(reconciled, [])
        self.assertEqual(Job.objects.get(job_id="done").status, "success")

    @override_settings(JOB_STALE_MINUTES=10)
    def test_command_uses_setting_default(self):
        self._make_job("via-cmd", "running", age_minutes=20)
        out = StringIO()
        call_command("reconcile_jobs", stdout=out)
        self.assertIn("reconciled:1", out.getvalue())
        self.assertEqual(Job.objects.get(job_id="via-cmd").status, "failed")

    def test_command_respects_explicit_flag(self):
        self._make_job("flagged", "running", age_minutes=20)
        out = StringIO()
        call_command("reconcile_jobs", "--older-than-minutes", "30", stdout=out)
        # 20 < 30 → not stale yet.
        self.assertIn("reconciled:0", out.getvalue())
        self.assertEqual(Job.objects.get(job_id="flagged").status, "running")

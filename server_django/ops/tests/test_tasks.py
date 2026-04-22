"""Unit tests for the Phase 6 Celery tasks.

The three tasks live in ``server_django/ops/tasks.py``:

- ``run_scenario_job`` — the real one; drives the scheduler service and
  mutates Job + JobEvent rows.
- ``renew_graph_subscriptions_task`` / ``prune_retention_task`` — stubs
  that return a phase marker.

Tests call the tasks directly (not via ``.delay``) so the Celery broker
isn't involved. The scheduler service is mocked at the
``catalog.services.build_service_from_db`` name (imported lazily inside
the task body to sidestep app-registry loading order).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from accounts.models import User
from django.test import TestCase

from ops.models import Job, JobEvent
from ops.tasks import (
    prune_retention_task,
    renew_graph_subscriptions_task,
    run_scenario_job,
)


class RunScenarioJobTaskTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.job = Job.objects.create(
            job_id="job-run-1",
            user=self.alice,
            kind="run_scenario",
            target_id="sc-alice",
            status="queued",
            dry_run=True,
            payload={"scenario_id": "sc-alice"},
        )

    def test_run_scenario_job_marks_success(self):
        service = MagicMock()
        service.run_scenario.return_value = 0
        with patch("catalog.services.build_service_from_db", return_value=service) as build_mock:
            result = run_scenario_job("job-run-1", "sc-alice", True)
        self.assertEqual(result, {"job_id": "job-run-1", "exit_code": 0})
        build_mock.assert_called_once_with()
        service.run_scenario.assert_called_once_with("sc-alice", dry_run=True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "success")
        self.assertEqual(self.job.exit_code, 0)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertEqual(self.job.result, {"scenario_id": "sc-alice", "dry_run": True})
        self.assertIsNone(self.job.error)

        events = list(JobEvent.objects.filter(job=self.job).order_by("id"))
        self.assertEqual([e.event_type for e in events], ["running", "success"])
        self.assertEqual(events[1].level, "info")
        self.assertEqual(events[1].payload, {"exit_code": 0})

    def test_run_scenario_job_marks_failed_on_nonzero(self):
        service = MagicMock()
        service.run_scenario.return_value = 1
        with patch("catalog.services.build_service_from_db", return_value=service):
            result = run_scenario_job("job-run-1", "sc-alice", False)
        self.assertEqual(result, {"job_id": "job-run-1", "exit_code": 1})

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.job.exit_code, 1)
        self.assertIsNone(self.job.error)
        self.assertEqual(self.job.result, {"scenario_id": "sc-alice", "dry_run": False})
        last_event = JobEvent.objects.filter(job=self.job).order_by("id").last()
        self.assertEqual(last_event.event_type, "failed")
        self.assertEqual(last_event.level, "error")

    def test_run_scenario_job_marks_failed_on_exception(self):
        service = MagicMock()
        service.run_scenario.side_effect = RuntimeError("boom")
        with (
            patch("catalog.services.build_service_from_db", return_value=service),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_scenario_job("job-run-1", "sc-alice", True)
        self.assertEqual(str(cm.exception), "boom")

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(self.job.error, "boom")
        self.assertIsNotNone(self.job.finished_at)
        # The ``running`` event was written before the exception; the
        # failure event must also be present with level="error".
        events = list(JobEvent.objects.filter(job=self.job).order_by("id"))
        self.assertEqual([e.event_type for e in events], ["running", "failed"])
        self.assertEqual(events[1].level, "error")
        self.assertEqual(events[1].message, "boom")

    def test_run_scenario_job_missing_job_raises(self):
        # Defensive: the worker should surface a clean RuntimeError if the
        # Job row disappeared between dispatch and execution.
        with (
            patch("catalog.services.build_service_from_db"),
            self.assertRaises(RuntimeError),
        ):
            run_scenario_job("does-not-exist", "sc-alice", True)


class GraphStubTaskTest(TestCase):
    def test_renew_graph_subscriptions_stub_returns_phase8_marker(self):
        result = renew_graph_subscriptions_task()
        self.assertEqual(result, {"enabled": False, "reason": "implemented_in_phase_8"})


class RetentionStubTaskTest(TestCase):
    def test_prune_retention_stub_returns_phase7_marker(self):
        result = prune_retention_task()
        self.assertEqual(result, {"enabled": False, "reason": "implemented_in_phase_7"})

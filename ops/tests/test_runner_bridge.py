from __future__ import annotations

from unittest import mock

from django.test import TestCase

from accounts.models import User
from ops.models import Job, JobEvent
from ops.runner_bridge import execute_scenario_job


class RunnerBridgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@b.co", password="pw12345!")
        self.job = Job.objects.create(job_id="j1", user=self.user, kind="run_scenario", target_id="scn", status="queued", dry_run=True)

    def test_success_writes_step_events_and_marks_success(self):
        from scenarios.events import StepEvent

        def fake_run(scenario_id, dry_run, on_event=None, execution_id=None):
            on_event(StepEvent(step_id="steps[0]", event_type="step_started", step_type="open_url"))
            on_event(StepEvent(step_id="steps[0]", event_type="step_succeeded", step_type="open_url", duration_ms=12))
            return 0

        with mock.patch("ops.runner_bridge._run_scenario_with_sink", side_effect=fake_run):
            execute_scenario_job("j1", "scn", True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "success")
        types = list(JobEvent.objects.filter(job_id="j1").values_list("event_type", flat=True))
        self.assertIn("step_started", types)
        self.assertIn("step_succeeded", types)

    def test_failure_records_traceback_in_job_error(self):
        def boom(scenario_id, dry_run, on_event=None, execution_id=None):
            raise RuntimeError("kaboom")

        with mock.patch("ops.runner_bridge._run_scenario_with_sink", side_effect=boom), self.assertRaises(RuntimeError):
            execute_scenario_job("j1", "scn", True)

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "failed")
        self.assertIn("kaboom", self.job.error or "")


class DispatchTests(TestCase):
    @mock.patch("ops.services._celery_worker_available", return_value=False)
    @mock.patch("ops.services._run_job_inline")
    def test_no_worker_runs_inline(self, run_inline, _avail):
        from accounts.models import User
        from catalog.models import Scenario
        from ops.services import enqueue_scenario_job

        user = User.objects.create_user(email="c@d.co", password="pw12345!")
        Scenario.objects.create(scenario_id="scn", owner=user, definition={"steps": []})
        enqueue_scenario_job(user_id_str=str(user.id), scenario_id="scn", dry_run=True, current_user=user)
        run_inline.assert_called_once()

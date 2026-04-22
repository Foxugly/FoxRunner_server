"""Unit tests for the Celery tasks.

The three tasks live in ``server_django/ops/tasks.py``:

- ``run_scenario_job`` -- drives the scheduler service and mutates Job +
  JobEvent rows.
- ``renew_graph_subscriptions_task`` -- renews Graph subscriptions whose
  expiration falls within the renew window. Phase 12.5 promoted this
  from a stub to a real port of ``api.tasks._renew_graph_subscriptions_task``.
- ``prune_retention_task`` -- prunes old jobs/audit/graph-notifications +
  artifacts. Phase 12.5 promoted this from a stub to a real port of
  ``api.tasks._prune_retention_task``.

Tests call the tasks directly (not via ``.delay``) so the Celery broker
isn't involved. External dependencies (Microsoft Graph HTTP, retention
helpers) are patched at the module-bound name on ``ops.tasks`` so the
late imports inside the task body are intercepted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import User
from ops.models import GraphSubscription, Job, JobEvent
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


class RenewGraphSubscriptionsTaskTest(TestCase):
    """Phase 12.5 -- promotes the stub to a real port of
    ``api.tasks._renew_graph_subscriptions_task``.
    """

    def test_renew_graph_when_disabled_returns_disabled(self):
        # GRAPH_SUBSCRIPTION_RENEW_ENABLED=false short-circuits before any
        # configuration check or DB query.
        with patch.dict("os.environ", {"GRAPH_SUBSCRIPTION_RENEW_ENABLED": "false"}, clear=False):
            result = renew_graph_subscriptions_task()
        self.assertEqual(result, {"enabled": False, "renewed": 0})

    def test_renew_graph_when_unconfigured_returns_configured_false(self):
        # Default-enabled but no Graph creds -> configured=False, no HTTP.
        env_overrides = {
            "GRAPH_SUBSCRIPTION_RENEW_ENABLED": "true",
            "GRAPH_TENANT_ID": "",
            "GRAPH_CLIENT_ID": "",
            "GRAPH_CLIENT_SECRET": "",
        }
        with patch.dict("os.environ", env_overrides, clear=False):
            result = renew_graph_subscriptions_task()
        self.assertEqual(result, {"enabled": True, "configured": False, "renewed": 0})

    def test_renew_graph_renews_only_expiring_subs(self):
        # Three subscriptions: one expiring within the window, one safely
        # in the future, one with a NULL expiration. Only the first one
        # must trigger a Graph HTTP call.
        now = datetime.now(UTC).replace(tzinfo=None)
        expiring = GraphSubscription.objects.create(
            subscription_id="sub-expiring",
            resource="users/me/messages",
            change_type="created",
            notification_url="https://callback.example.com",
            expiration_datetime=now + timedelta(hours=1),
            raw_payload={"id": "sub-expiring"},
        )
        GraphSubscription.objects.create(
            subscription_id="sub-far-future",
            resource="users/me/messages",
            change_type="created",
            notification_url="https://callback.example.com",
            expiration_datetime=now + timedelta(days=7),
            raw_payload={"id": "sub-far-future"},
        )
        GraphSubscription.objects.create(
            subscription_id="sub-no-expiration",
            resource="users/me/messages",
            change_type="created",
            notification_url="https://callback.example.com",
            expiration_datetime=None,
            raw_payload={"id": "sub-no-expiration"},
        )

        env_overrides = {
            "GRAPH_SUBSCRIPTION_RENEW_ENABLED": "true",
            "GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS": "24",
            "GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS": "48",
            "GRAPH_TENANT_ID": "tid",
            "GRAPH_CLIENT_ID": "cid",
            "GRAPH_CLIENT_SECRET": "csec",
        }
        renew_response = {
            "id": "sub-expiring",
            "expirationDateTime": "2099-01-02T03:04:05Z",
        }
        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch("ops.graph.renew_graph_subscription", return_value=renew_response) as renew_mock,
            patch("ops.graph.is_graph_configured", return_value=True),
        ):
            result = renew_graph_subscriptions_task()

        renew_mock.assert_called_once()
        called_with_id = renew_mock.call_args.args[0]
        self.assertEqual(called_with_id, "sub-expiring")
        self.assertEqual(result["enabled"], True)
        self.assertEqual(result["configured"], True)
        self.assertEqual(result["renewed"], 1)
        self.assertEqual(result["errors"], [])

        expiring.refresh_from_db()
        # Django (USE_TZ=True) returns the column as an aware UTC datetime
        # even though the helper passes a naive value -- the ORM coerces.
        actual = expiring.expiration_datetime
        if actual.tzinfo is not None:
            actual = actual.replace(tzinfo=None)
        self.assertEqual(actual, datetime(2099, 1, 2, 3, 4, 5))

    def test_renew_graph_handles_per_row_failures(self):
        # A failing renewal on row N must not stop row N+1.
        now = datetime.now(UTC).replace(tzinfo=None)
        for sub_id in ("sub-a", "sub-b"):
            GraphSubscription.objects.create(
                subscription_id=sub_id,
                resource="users/me/messages",
                change_type="created",
                notification_url="https://callback.example.com",
                expiration_datetime=now + timedelta(hours=1),
                raw_payload={"id": sub_id},
            )

        def fake_renew(sub_id, _expiration):
            if sub_id == "sub-a":
                raise RuntimeError("Graph 5xx")
            return {"id": sub_id, "expirationDateTime": "2099-01-02T03:04:05Z"}

        env_overrides = {
            "GRAPH_SUBSCRIPTION_RENEW_ENABLED": "true",
            "GRAPH_TENANT_ID": "tid",
            "GRAPH_CLIENT_ID": "cid",
            "GRAPH_CLIENT_SECRET": "csec",
        }
        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch("ops.graph.renew_graph_subscription", side_effect=fake_renew),
            patch("ops.graph.is_graph_configured", return_value=True),
        ):
            result = renew_graph_subscriptions_task()
        self.assertEqual(result["renewed"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["subscription_id"], "sub-a")


class PruneRetentionTaskTest(TestCase):
    """Phase 12.5 -- promotes the stub to a real port of
    ``api.tasks._prune_retention_task``.
    """

    def test_prune_retention_disabled_returns_disabled(self):
        with patch.dict("os.environ", {"RETENTION_PRUNE_ENABLED": "false"}, clear=False):
            result = prune_retention_task()
        self.assertEqual(result, {"enabled": False})

    def test_prune_retention_calls_prune_database_records_with_env_ints(self):
        env_overrides = {
            "RETENTION_PRUNE_ENABLED": "true",
            "RETENTION_JOBS_DAYS": "30",
            "RETENTION_AUDIT_DAYS": "90",
            "RETENTION_GRAPH_NOTIFICATIONS_DAYS": "14",
            "RETENTION_ARTIFACTS_DAYS": "",  # explicitly omitted -> no artifact pruning
        }
        # Patch the bound name inside ops.tasks (lazy-imported at call time
        # via ``from ops.services import prune_database_records``).
        fake_removed = {"jobs": 1, "job_events": 2, "audit": 3, "graph_notifications": 4}
        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch("ops.services.prune_database_records", return_value=fake_removed) as prune_mock,
        ):
            result = prune_retention_task()
        prune_mock.assert_called_once_with(jobs_days=30, audit_days=90, graph_notifications_days=14)
        self.assertEqual(
            result,
            {"enabled": True, "removed": {**fake_removed, "artifacts": 0}},
        )

    def test_prune_retention_includes_artifacts_when_configured(self):
        env_overrides = {
            "RETENTION_PRUNE_ENABLED": "true",
            "RETENTION_JOBS_DAYS": "",
            "RETENTION_AUDIT_DAYS": "",
            "RETENTION_GRAPH_NOTIFICATIONS_DAYS": "",
            "RETENTION_ARTIFACTS_DAYS": "7",
        }
        fake_removed = {"jobs": 0, "job_events": 0, "audit": 0, "graph_notifications": 0}
        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch("ops.services.prune_database_records", return_value=fake_removed),
            patch("ops.tasks._prune_artifacts_files", return_value=5) as artifact_mock,
        ):
            result = prune_retention_task()
        artifact_mock.assert_called_once_with(7)
        self.assertEqual(
            result,
            {"enabled": True, "removed": {**fake_removed, "artifacts": 5}},
        )

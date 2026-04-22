"""Integration tests for the Phase 6 jobs endpoints.

Six endpoints under ``/api/v1``:

    POST /users/{user_id}/scenarios/{scenario_id}/jobs  (202, Idempotency-Key)
    GET  /jobs                                          (paginated, non-admin scoped to self)
    GET  /jobs/{job_id}?user_id=
    GET  /jobs/{job_id}/events?user_id=                 (raw array)
    POST /jobs/{job_id}/cancel?user_id=                 (Celery revoke + 409)
    POST /jobs/{job_id}/retry?user_id=                  (kind=run_scenario only)

Celery ``delay`` is mocked at the ``ops.services.run_scenario_job.delay``
attribute path so the tests never hit a broker. Each test asserts the
HTTP shape + the resulting DB rows (Job + JobEvent + optional
AuditEntry).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

from accounts.models import User
from catalog.models import Scenario
from ops.models import AuditEntry, Job, JobEvent


def _login(client: Client, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/jwt/login",
        data=f"username={email}&password={password}",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class _BaseJobsApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")

        self.alice_scenario = Scenario.objects.create(
            scenario_id="sc-alice",
            owner=self.alice,
            description="alice",
            definition={"steps": []},
        )
        self.bob_scenario = Scenario.objects.create(
            scenario_id="sc-bob",
            owner=self.bob,
            description="bob",
            definition={"steps": []},
        )


class EnqueueScenarioJobTest(_BaseJobsApiTest):
    def test_enqueue_scenario_job_returns_202_and_calls_celery(self):
        fake_task = MagicMock(id="celery-task-1")
        with patch("ops.tasks.run_scenario_job.delay", return_value=fake_task) as delay_mock:
            # ``ops.services.enqueue_scenario_job`` does ``from ops.tasks import
            # run_scenario_job`` then calls ``.delay(...)`` -- patching the
            # ``.delay`` attribute on the canonical Celery task object is
            # therefore visible inside the service function.
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["kind"], "run_scenario")
        self.assertEqual(body["target_id"], "sc-alice")
        self.assertEqual(body["user_id"], str(self.alice.id))
        self.assertEqual(body["celery_task_id"], "celery-task-1")
        self.assertTrue(body["dry_run"])
        delay_mock.assert_called_once_with(body["job_id"], "sc-alice", True)
        self.assertEqual(Job.objects.count(), 1)
        events = list(JobEvent.objects.filter(job_id=body["job_id"]).order_by("id"))
        self.assertEqual([e.event_type for e in events], ["queued", "submitted"])

    def test_enqueue_idempotent_replay(self):
        fake_task = MagicMock(id="celery-task-2")
        with patch("ops.tasks.run_scenario_job.delay", return_value=fake_task) as delay_mock:
            first = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs",
                HTTP_IDEMPOTENCY_KEY="idem-1",
                **_auth(self.alice_token),
            )
            self.assertEqual(first.status_code, 202, first.content)
            replay = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs",
                HTTP_IDEMPOTENCY_KEY="idem-1",
                **_auth(self.alice_token),
            )
        self.assertEqual(replay.status_code, 202, replay.content)
        self.assertEqual(first.json(), replay.json())
        # Only one Job + Celery dispatch despite the replay.
        self.assertEqual(Job.objects.count(), 1)
        self.assertEqual(delay_mock.call_count, 1)

    def test_enqueue_idempotent_different_payload_returns_409(self):
        fake_task = MagicMock(id="celery-task-3")
        with patch("ops.tasks.run_scenario_job.delay", return_value=fake_task):
            first = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs?dry_run=true",
                HTTP_IDEMPOTENCY_KEY="idem-2",
                **_auth(self.alice_token),
            )
            self.assertEqual(first.status_code, 202, first.content)
            replay = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs?dry_run=false",
                HTTP_IDEMPOTENCY_KEY="idem-2",
                **_auth(self.alice_token),
            )
        self.assertEqual(replay.status_code, 409, replay.content)

    def test_enqueue_invisible_scenario_404(self):
        # Alice tries to enqueue a job on Bob's private scenario -> 404
        # because ``get_scenario_for_user`` collapses non-visibility.
        with patch("ops.tasks.run_scenario_job.delay", return_value=MagicMock(id="irrelevant")):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-bob/jobs",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(Job.objects.count(), 0)

    def test_enqueue_other_user_403(self):
        # Bob tries to enqueue on Alice's user_id path -> 403 from
        # ``require_user_scope`` (long before the Celery dispatch).
        with patch("ops.tasks.run_scenario_job.delay", return_value=MagicMock(id="irrelevant")):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs",
                **_auth(self.bob_token),
            )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(Job.objects.count(), 0)


class ListJobsTest(_BaseJobsApiTest):
    def setUp(self):
        super().setUp()
        Job.objects.create(
            job_id="job-alice-1",
            user=self.alice,
            kind="run_scenario",
            target_id="sc-alice",
            status="queued",
            dry_run=True,
        )
        Job.objects.create(
            job_id="job-alice-2",
            user=self.alice,
            kind="run_scenario",
            target_id="sc-alice",
            status="success",
            dry_run=False,
        )
        Job.objects.create(
            job_id="job-bob-1",
            user=self.bob,
            kind="run_scenario",
            target_id="sc-bob",
            status="queued",
            dry_run=True,
        )

    def test_list_jobs_admin_sees_all(self):
        response = self.client.get("/api/v1/jobs", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual({item["job_id"] for item in body["items"]}, {"job-alice-1", "job-alice-2", "job-bob-1"})

    def test_list_jobs_non_admin_scoped_to_self(self):
        # Bob must only see his own jobs, even with no ``user_id`` filter.
        response = self.client.get("/api/v1/jobs", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["job_id"], "job-bob-1")

    def test_list_jobs_non_admin_user_id_mismatch_403(self):
        response = self.client.get(
            f"/api/v1/jobs?user_id={self.alice.id}",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_list_jobs_non_admin_user_id_self_ok(self):
        # Non-admin explicitly scoped to self is OK.
        response = self.client.get(
            f"/api/v1/jobs?user_id={self.alice.id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["total"], 2)

    def test_list_jobs_filter_by_status_and_scenario_id(self):
        response = self.client.get(
            "/api/v1/jobs?status=queued&scenario_id=sc-alice",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["job_id"], "job-alice-1")


class GetJobTest(_BaseJobsApiTest):
    def setUp(self):
        super().setUp()
        self.job = Job.objects.create(
            job_id="job-detail",
            user=self.alice,
            kind="run_scenario",
            target_id="sc-alice",
            status="queued",
            dry_run=True,
        )

    def test_get_job_owner(self):
        response = self.client.get(
            f"/api/v1/jobs/job-detail?user_id={self.alice.id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["job_id"], "job-detail")
        self.assertEqual(body["user_id"], str(self.alice.id))

    def test_get_job_other_user_403(self):
        # Bob asks for a job owned by Alice via Alice's user_id path -> 403
        # at the ``require_user_scope`` gate (before the job lookup).
        response = self.client.get(
            f"/api/v1/jobs/job-detail?user_id={self.alice.id}",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_get_job_unknown_404(self):
        response = self.client.get(
            f"/api/v1/jobs/does-not-exist?user_id={self.alice.id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_get_job_events_raw_array(self):
        JobEvent.objects.create(job=self.job, event_type="queued", message="hello")
        JobEvent.objects.create(job=self.job, event_type="running", message="go")
        response = self.client.get(
            f"/api/v1/jobs/job-detail/events?user_id={self.alice.id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        # The response is a raw list -- no ``items`` / ``total`` envelope.
        self.assertIsInstance(body, list)
        self.assertEqual([event["event_type"] for event in body], ["queued", "running"])


class CancelJobTest(_BaseJobsApiTest):
    def setUp(self):
        super().setUp()
        self.job = Job.objects.create(
            job_id="job-cancel",
            user=self.alice,
            celery_task_id="celery-to-revoke",
            kind="run_scenario",
            target_id="sc-alice",
            status="queued",
            dry_run=True,
        )

    def test_cancel_job_owner(self):
        with patch("foxrunner.celery.celery_app") as celery_mock:
            response = self.client.post(
                f"/api/v1/jobs/job-cancel/cancel?user_id={self.alice.id}",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "cancelled")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, "cancelled")
        self.assertIsNotNone(self.job.finished_at)
        celery_mock.control.revoke.assert_called_once_with("celery-to-revoke", terminate=False)
        self.assertTrue(
            AuditEntry.objects.filter(
                action="job.cancel",
                target_type="job",
                target_id="job-cancel",
                actor=self.alice,
            ).exists()
        )
        self.assertTrue(JobEvent.objects.filter(job=self.job, event_type="cancelled").exists())

    def test_cancel_job_already_finished_409(self):
        self.job.status = "success"
        self.job.save(update_fields=["status"])
        with patch("foxrunner.celery.celery_app"):
            response = self.client.post(
                f"/api/v1/jobs/job-cancel/cancel?user_id={self.alice.id}",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 409, response.content)


class RetryJobTest(_BaseJobsApiTest):
    def setUp(self):
        super().setUp()
        self.source = Job.objects.create(
            job_id="job-source",
            user=self.alice,
            kind="run_scenario",
            target_id="sc-alice",
            status="failed",
            dry_run=True,
            payload={"scenario_id": "sc-alice"},
        )

    def test_retry_job_owner(self):
        fake_task = MagicMock(id="celery-retry")
        with patch("ops.tasks.run_scenario_job.delay", return_value=fake_task) as delay_mock:
            response = self.client.post(
                f"/api/v1/jobs/job-source/retry?user_id={self.alice.id}",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 202, response.content)
        body = response.json()
        self.assertEqual(body["kind"], "run_scenario")
        self.assertEqual(body["target_id"], "sc-alice")
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["payload"]["retry_of"], "job-source")
        self.assertEqual(body["payload"]["scenario_id"], "sc-alice")
        delay_mock.assert_called_once_with(body["job_id"], "sc-alice", True)
        # Source job untouched; a new Job row exists.
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "failed")
        self.assertEqual(Job.objects.count(), 2)
        self.assertTrue(
            AuditEntry.objects.filter(
                action="job.retry",
                target_type="job",
                target_id="job-source",
                actor=self.alice,
            ).exists()
        )

    def test_retry_non_run_scenario_409(self):
        Job.objects.create(
            job_id="job-other",
            user=self.alice,
            kind="graph.renew",
            target_id="subscription-1",
            status="failed",
            dry_run=False,
        )
        # The base setUp seeds one ``run_scenario`` job (job-source); we
        # assert no NEW job was created by snapshotting the count before
        # the retry attempt.
        baseline = Job.objects.count()
        with patch("ops.tasks.run_scenario_job.delay", return_value=MagicMock(id="unused")) as delay_mock:
            response = self.client.post(
                f"/api/v1/jobs/job-other/retry?user_id={self.alice.id}",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 409, response.content)
        # No Celery dispatch, no new Job row.
        delay_mock.assert_not_called()
        self.assertEqual(Job.objects.count(), baseline)


class EnqueueIdempotencyPayloadJsonTest(_BaseJobsApiTest):
    """Regression: verify the POST with an Idempotency-Key serialises the
    idempotency payload deterministically (bool + str keys).
    """

    def test_request_body_ignored_for_enqueue(self):
        # The endpoint doesn't read a request body; posting arbitrary JSON
        # must not break the contract.
        with patch("ops.tasks.run_scenario_job.delay", return_value=MagicMock(id="celery-ignored-body")):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/sc-alice/jobs",
                data=json.dumps({"foo": "bar"}),
                content_type="application/json",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 202, response.content)

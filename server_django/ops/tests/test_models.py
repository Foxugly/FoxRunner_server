from django.db import IntegrityError, transaction
from django.test import TestCase

from ops.models import (
    AppSetting,
    AuditEntry,
    ExecutionHistory,
    GraphNotification,
    GraphSubscription,
    IdempotencyKey,
    Job,
    JobEvent,
)


class OpsModelSmokeTest(TestCase):
    def test_job_and_event(self):
        job = Job.objects.create(
            job_id="j1",
            user_id="00000000-0000-0000-0000-000000000001",
            kind="run",
            target_id="t1",
            status="queued",
            payload={"a": 1},
        )
        evt = JobEvent.objects.create(job=job, event_type="started", message="ok")
        self.assertEqual(job.events.count(), 1)
        self.assertEqual(evt.payload, {})

    def test_history_unique(self):
        ExecutionHistory.objects.create(
            slot_key="k",
            slot_id="s1",
            scenario_id="sc1",
            execution_id="e1",
            executed_at="2026-04-22T10:00:00Z",
            status="ok",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExecutionHistory.objects.create(
                slot_key="k",
                slot_id="s1",
                scenario_id="sc1",
                execution_id="e1",
                executed_at="2026-04-22T10:00:00Z",
                status="ok",
            )

    def test_idempotency_unique(self):
        IdempotencyKey.objects.create(
            user_id="00000000-0000-0000-0000-000000000001",
            key="k1",
            request_fingerprint="f1",
            response={"a": 1},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            IdempotencyKey.objects.create(
                user_id="00000000-0000-0000-0000-000000000001",
                key="k1",
                request_fingerprint="f1",
                response={"a": 1},
            )

    def test_graph_notification_dedupe(self):
        GraphSubscription.objects.create(subscription_id="sub1")
        GraphNotification.objects.create(
            subscription_id="sub1",
            change_type="updated",
            resource="r1",
            lifecycle_event="renew",
            raw_payload={},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            GraphNotification.objects.create(
                subscription_id="sub1",
                change_type="updated",
                resource="r1",
                lifecycle_event="renew",
                raw_payload={},
            )

    def test_audit_and_settings(self):
        AppSetting.objects.create(key="k", value={"a": 1}, description="d")
        AuditEntry.objects.create(
            actor_user_id="00000000-0000-0000-0000-000000000001",
            action="create",
            target_type="scenario",
            target_id="s1",
            before={},
            after={"a": 1},
        )
        self.assertEqual(AppSetting.objects.get(key="k").value, {"a": 1})  # JSONField round-trip
        self.assertEqual(AuditEntry.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.first().after, {"a": 1})

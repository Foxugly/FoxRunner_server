import uuid

from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User
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
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(email="alice@x.com", password="x")

    def test_job_and_event(self):
        job = Job.objects.create(
            job_id="j1",
            user=self.alice,
            kind="run",
            target_id="t1",
            status="queued",
            payload={"a": 1},
        )
        evt = JobEvent.objects.create(job=job, event_type="started", message="ok")
        self.assertEqual(job.events.count(), 1)
        self.assertEqual(evt.payload, {})
        self.assertEqual(job.user_id, self.alice.id)

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
        # IdempotencyKey.user_id is a plain UUIDField post-phase-5 (no FK).
        user_uuid = uuid.uuid4()
        IdempotencyKey.objects.create(
            user_id=user_uuid,
            key="k1",
            request_fingerprint="f1",
            response={"a": 1},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            IdempotencyKey.objects.create(
                user_id=user_uuid,
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
            actor=self.alice,
            action="create",
            target_type="scenario",
            target_id="s1",
            before={},
            after={"a": 1},
        )
        self.assertEqual(AppSetting.objects.get(key="k").value, {"a": 1})  # JSONField round-trip
        self.assertEqual(AuditEntry.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.first().after, {"a": 1})
        self.assertEqual(AuditEntry.objects.first().actor_id, self.alice.id)

    def test_audit_actor_nullable(self):
        # Phase 5 makes actor nullable + SET_NULL so we keep the audit row
        # if the user is deleted (or for system-generated entries).
        AuditEntry.objects.create(
            actor=None,
            action="system.bootstrap",
            target_type="settings",
            target_id="seed",
            after={"ok": True},
        )
        row = AuditEntry.objects.get(action="system.bootstrap")
        self.assertIsNone(row.actor)
        self.assertIsNone(row.actor_id)

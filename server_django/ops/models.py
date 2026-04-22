"""Ops models — Job, JobEvent, GraphSubscription, GraphNotification,
AuditEntry, ExecutionHistory, AppSetting, IdempotencyKey.

Each field mirrors the SQLAlchemy counterpart in ``api/models.py``. The
relevant Alembic revisions for ops tables are:

- ``20260421_0002_jobs`` — ``jobs`` table + per-column indexes
- ``20260421_0003_job_events`` — ``job_events`` table + FK + per-column indexes
- ``20260421_0004_graph_mail_webhooks`` — ``graph_subscriptions`` and
  ``graph_notifications`` tables + per-column indexes
- ``20260421_0005_admin_operations`` — ``audit_log`` table + per-column indexes
- ``20260421_0006_settings_idempotency`` — ``app_settings`` and
  ``idempotency_keys`` tables + ``uq_idempotency_user_key``
- ``20260421_0007_query_indexes`` — two composite indexes on ``jobs``:
  ``ix_jobs_status_updated_at`` and ``ix_jobs_user_status`` (both declared
  in ``Job.Meta.indexes``); single-column ``ix_graph_subscriptions_expiration``
  and ``ix_audit_log_created_at`` (covered by ``db_index=True`` on the columns)
- ``20260421_0008_execution_history`` — ``execution_history`` table +
  ``uq_execution_history_identity`` + per-column indexes
- ``20260421_0009_graph_dedupe`` — ``uq_graph_notification_dedupe`` unique
  constraint on ``graph_notifications``
- ``20260421_0011_operational_indexes`` — composite
  ``ix_job_events_job_created_at`` and ``ix_execution_history_scenario_executed_at``

``*_user_id`` columns stay ``CharField(max_length=320)`` for now to match the
existing Alembic schema. Phase 5 promotes them to ``UUIDField`` and then to
``ForeignKey(User)`` after the data migration.
"""

from __future__ import annotations

from django.db import models


class Job(models.Model):
    job_id = models.CharField(max_length=64, unique=True, db_index=True)
    celery_task_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5 (no data normalization — jobs are always API-created with str(current_user.id))
    kind = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=128, db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    dry_run = models.BooleanField(default=True)
    exit_code = models.IntegerField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jobs"
        indexes = [
            # From migrations/versions/20260421_0007_query_indexes.py
            models.Index(fields=["status", "updated_at"], name="ix_jobs_status_updated_at"),
            models.Index(fields=["user_id", "status"], name="ix_jobs_user_status"),
        ]

    def __str__(self) -> str:
        return self.job_id


class JobEvent(models.Model):
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="events",
        to_field="job_id",
        db_column="job_id",
    )
    event_type = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, default="info")
    message = models.TextField(default="", blank=True)
    step = models.CharField(max_length=128, null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_events"
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(fields=["job", "created_at"], name="ix_job_events_job_created_at"),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} on {self.job_id}"


class GraphSubscription(models.Model):
    subscription_id = models.CharField(max_length=128, unique=True, db_index=True)
    resource = models.CharField(max_length=512, default="", db_index=True)
    change_type = models.CharField(max_length=128, default="")
    notification_url = models.CharField(max_length=1024, default="")
    lifecycle_notification_url = models.CharField(max_length=1024, null=True, blank=True)
    client_state = models.CharField(max_length=256, null=True, blank=True)
    # ix_graph_subscriptions_expiration (rev 20260421_0007)
    expiration_datetime = models.DateTimeField(null=True, blank=True, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "graph_subscriptions"

    def __str__(self) -> str:
        return self.subscription_id


class GraphNotification(models.Model):
    subscription_id = models.CharField(max_length=128, db_index=True)
    change_type = models.CharField(max_length=128, db_index=True)
    resource = models.CharField(max_length=1024, default="")
    tenant_id = models.CharField(max_length=128, null=True, blank=True)
    client_state = models.CharField(max_length=256, null=True, blank=True)
    lifecycle_event = models.CharField(max_length=128, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "graph_notifications"
        constraints = [
            # From migrations/versions/20260421_0009_graph_dedupe.py
            models.UniqueConstraint(
                fields=["subscription_id", "resource", "change_type", "lifecycle_event"],
                name="uq_graph_notification_dedupe",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.change_type}: {self.resource[:30]}"


class AuditEntry(models.Model):
    actor_user_id = models.CharField(max_length=320, db_index=True)  # FK(User) + nullable promotion in phase 5
    action = models.CharField(max_length=128, db_index=True)
    target_type = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=320, db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    # ix_audit_log_created_at (rev 20260421_0007)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_log"

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}/{self.target_id}"


class ExecutionHistory(models.Model):
    slot_key = models.CharField(max_length=256, db_index=True)
    slot_id = models.CharField(max_length=128, db_index=True)
    scenario_id = models.CharField(max_length=128, db_index=True)
    execution_id = models.CharField(max_length=128, null=True, db_index=True)
    executed_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    step = models.CharField(max_length=128, default="", blank=True)
    message = models.TextField(default="", blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "execution_history"
        constraints = [
            # From migrations/versions/20260421_0008_execution_history.py
            models.UniqueConstraint(
                fields=["execution_id", "slot_id", "scenario_id"],
                name="uq_execution_history_identity",
            ),
        ]
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(
                fields=["scenario_id", "executed_at"],
                name="ix_execution_history_scenario_executed_at",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scenario_id} @ {self.executed_at:%Y-%m-%d %H:%M}"


class AppSetting(models.Model):
    key = models.CharField(max_length=128, unique=True, db_index=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.TextField(default="", blank=True)
    updated_by = models.CharField(max_length=320, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_settings"

    def __str__(self) -> str:
        return self.key


class IdempotencyKey(models.Model):
    user_id = models.CharField(max_length=320, db_index=True)  # internal key; type-flipped to UUIDField in phase 5 (no data normalization needed) but no FK promotion
    key = models.CharField(max_length=128, db_index=True)
    request_fingerprint = models.CharField(max_length=128)
    status_code = models.IntegerField(default=200)
    response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_keys"
        constraints = [
            # From migrations/versions/20260421_0006_settings_idempotency.py
            models.UniqueConstraint(fields=["user_id", "key"], name="uq_idempotency_user_key"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.key}"

"""Ninja schemas for the ops endpoints.

Mirrors the Pydantic payloads in ``api/schemas.py`` for jobs + events +
pagination + admin / monitoring / audit / settings / artifacts. Kept in
``ops/schemas.py`` so Phase 8 (Microsoft Graph) can extend this module
without forcing the catalog app to own unrelated types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ninja import Schema


class JobOut(Schema):
    """Serialised :class:`ops.models.Job` row.

    Mirrors ``api/serializers.py::serialize_job``. ``user_id`` is the UUID
    primary key of the FK target rendered as ``str`` (the frontend
    contract still expects a string-shaped user identifier).
    """

    job_id: str
    celery_task_id: str | None = None
    user_id: str
    kind: str
    target_id: str
    status: str
    dry_run: bool
    exit_code: int | None = None
    error: str | None = None
    payload: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobEventOut(Schema):
    """Serialised :class:`ops.models.JobEvent` row."""

    event_type: str
    level: str
    message: str
    step: str | None = None
    payload: dict[str, Any]
    created_at: datetime


class JobPage(Schema):
    """Paginated envelope for :class:`JobOut` items."""

    items: list[JobOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Phase 7 -- Admin / monitoring / audit / settings / artifacts
# --------------------------------------------------------------------------


class AdminUserPatchIn(Schema):
    """PATCH body for ``/admin/users/{target_user_id}``.

    Mirrors ``api/schemas.py::AdminUserUpdatePayload``. All fields are
    optional and only applied when present.
    """

    is_active: bool | None = None
    is_superuser: bool | None = None
    is_verified: bool | None = None
    timezone_name: str | None = None


class AppSettingIn(Schema):
    """PUT body for ``/admin/settings/{key}``.

    Mirrors ``api/schemas.py::AppSettingPayload``.
    """

    value: dict[str, Any]
    description: str = ""


class AppSettingOut(Schema):
    """Serialised :class:`ops.models.AppSetting` row.

    Mirrors ``api/schemas.py::AppSettingResponsePayload`` /
    ``api/serializers.py::serialize_setting``.
    """

    key: str
    value: dict[str, Any]
    description: str
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AppSettingPage(Schema):
    items: list[AppSettingOut]
    total: int
    limit: int
    offset: int


class AuditOut(Schema):
    """Serialised :class:`ops.models.AuditEntry` row."""

    id: int
    actor_user_id: str | None = None
    action: str
    target_type: str
    target_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: datetime | None = None


class AuditPage(Schema):
    items: list[AuditOut]
    total: int
    limit: int
    offset: int


class ArtifactItem(Schema):
    """One row from the artifacts listing."""

    kind: str
    name: str
    size: int
    updated_at: float | None = None


class ArtifactPage(Schema):
    items: list[ArtifactItem]
    total: int
    limit: int
    offset: int


class MonitoringJobs(Schema):
    total: int
    failed: int
    queued: int
    running: int
    stuck: int
    by_status: dict[str, int] = {}
    average_duration_seconds: float | None = None


class MonitoringGraph(Schema):
    subscriptions_expiring: int
    expiring_within_hours: int


class MonitoringSummary(Schema):
    jobs: MonitoringJobs
    graph: MonitoringGraph


class ConfigChecksOut(Schema):
    status: str
    checks: dict[str, Any]


class DbStatsOut(Schema):
    tables: dict[str, int]
    last_execution_at: str | None = None
    failed_jobs: int
    graph_subscriptions_expiring: int


class ExportOut(Schema):
    """Full catalog export envelope."""

    scenarios: dict[str, Any]
    slots: dict[str, Any]


class ImportDryRun(Schema):
    """Response envelope for ``/admin/import``.

    Both dry-run and apply paths return the same shape. Skipped scenarios
    (orphan owner_user_id post-Phase-5 FK promotion) are reported when
    non-zero so operators can investigate.
    """

    dry_run: bool
    scenarios: int | None = None
    slots: int | None = None
    imported: bool | None = None
    skipped_scenarios: int | None = None


class RetentionResult(Schema):
    removed: dict[str, int]


# --------------------------------------------------------------------------
# Phase 8 -- Microsoft Graph subscriptions + notifications
# --------------------------------------------------------------------------


class GraphSubscriptionIn(Schema):
    """POST /graph/subscriptions body. Mirrors ``api/schemas.py::GraphSubscriptionPayload``.

    ``expiration_datetime`` MUST be timezone-aware -- the FastAPI version
    enforces UTC via a Pydantic validator; Ninja accepts any ``datetime``
    so the orchestrator normalises with ``_utc_iso`` before forwarding to
    Graph.
    """

    resource: str
    change_type: str = "created,updated"
    notification_url: str
    expiration_datetime: datetime
    lifecycle_notification_url: str | None = None


class GraphSubscriptionOut(Schema):
    """Serialised :class:`ops.models.GraphSubscription` row."""

    subscription_id: str
    resource: str
    change_type: str
    notification_url: str
    lifecycle_notification_url: str | None = None
    expiration_datetime: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GraphSubscriptionPage(Schema):
    items: list[GraphSubscriptionOut]
    total: int
    limit: int
    offset: int


class GraphRenewIn(Schema):
    """PATCH /graph/subscriptions/{subscription_id} body."""

    expiration_datetime: datetime


class GraphNotificationOut(Schema):
    """Serialised :class:`ops.models.GraphNotification` row."""

    id: int
    subscription_id: str
    change_type: str
    resource: str
    tenant_id: str | None = None
    client_state: str | None = None
    lifecycle_event: str | None = None
    raw_payload: dict[str, Any]
    created_at: datetime | None = None


class GraphNotificationPage(Schema):
    items: list[GraphNotificationOut]
    total: int
    limit: int
    offset: int


class AcceptedOut(Schema):
    """Webhook response envelope -- mirrors ``api/schemas.py::AcceptedPayload``."""

    accepted: int


class DeletedOut(Schema):
    """Mirrors ``api/schemas.py::DeletedPayload``. Used by DELETE /graph/subscriptions/{id}."""

    deleted: str

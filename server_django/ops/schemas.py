"""Ninja schemas for the ops endpoints.

Mirrors the Pydantic payloads in ``api/schemas.py`` for jobs + events +
pagination. Kept in ``ops/schemas.py`` so Phase 7 (admin / monitoring /
audit / settings) and Phase 8 (Microsoft Graph) can extend this module
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

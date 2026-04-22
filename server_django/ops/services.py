"""Ops domain services.

Centralises logic currently spread across ``api/jobs.py``,
``api/history.py``, ``api/audit.py``, ``api/settings.py``,
``api/artifacts.py``, ``api/graph.py``, ``api/monitoring.py``,
``api/retention.py``. Ninja handlers stay thin and delegate here.
"""

from __future__ import annotations

from typing import Any

from ops.models import AuditEntry


def write_audit(
    *,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEntry:
    """Persist a single audit row.

    Sync port of ``api/audit.py::write_audit`` -- the FastAPI version was
    async only because it shared the ``AsyncSession``. The Django ORM is
    sync and Ninja handlers run in a sync request cycle, so the helper
    drops the ``await`` plumbing.
    """
    return AuditEntry.objects.create(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before or {},
        after=after or {},
    )


def list_audit(
    *,
    limit: int = 100,
    offset: int = 0,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> list[AuditEntry]:
    """Return audit rows matching the optional filters, newest first."""
    qs = AuditEntry.objects.all().order_by("-id")
    if actor_user_id:
        qs = qs.filter(actor_user_id=actor_user_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    return list(qs[offset : offset + limit])


def count_audit(
    *,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> int:
    """Return the total number of audit rows matching the optional filters."""
    qs = AuditEntry.objects.all()
    if actor_user_id:
        qs = qs.filter(actor_user_id=actor_user_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    return qs.count()

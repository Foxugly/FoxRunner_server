"""Ops domain services.

Centralises logic currently spread across ``api/jobs.py``,
``api/history.py``, ``api/audit.py``, ``api/settings.py``,
``api/artifacts.py``, ``api/graph.py``, ``api/monitoring.py``,
``api/retention.py``. Ninja handlers stay thin and delegate here.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from typing import Any

from accounts.models import User
from django.db import transaction
from django.db.models import QuerySet

from ops.models import AuditEntry, ExecutionHistory


def write_audit(
    *,
    actor: User | None,
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

    Post-phase-5 the actor is stored as a ``ForeignKey(User)`` (column
    ``actor_user_id`` preserved). Callers pass the ``User`` object; the
    column may be ``NULL`` (the FK is nullable + ``SET_NULL``) so an
    explicit ``actor=None`` is allowed for system-generated audit rows.
    """
    return AuditEntry.objects.create(
        actor=actor,
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
    """Return audit rows matching the optional filters, newest first.

    ``actor_user_id`` is the API-shape filter (UUID string); the FK column
    name remains ``actor_user_id`` so the filter maps to ``actor_id`` on
    the ORM side.
    """
    qs = AuditEntry.objects.all().order_by("-id")
    if actor_user_id:
        qs = qs.filter(actor_id=actor_user_id)
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
        qs = qs.filter(actor_id=actor_user_id)
    if target_type:
        qs = qs.filter(target_type=target_type)
    if target_id:
        qs = qs.filter(target_id=target_id)
    return qs.count()


# --------------------------------------------------------------------------
# Execution history (Phase 4.8). Sync port of ``api/history.py``. The
# legacy JSONL file at ``config.runtime.history_file`` is the source of
# truth for the CLI scheduler; the API endpoint synchronises it into
# ``ops.execution_history`` on every request so the DB-backed read stays
# consistent with what the CLI writes. This dual-stack hack goes away in
# Phase 13 once the CLI is rewritten to write directly to the DB.
# --------------------------------------------------------------------------


def _parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp into a naive UTC datetime.

    The ``execution_history`` table stores naive datetimes (the SQLAlchemy
    schema does the same — see ``api/time_utils.py::db_utc``); the parser
    accepts both ``Z`` and ``+00:00`` suffixes.
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _utc_iso(dt: datetime | None) -> str | None:
    """Serialise a datetime as ISO 8601 UTC with a ``Z`` suffix.

    The DB stores naive datetimes (UTC by convention) — assume UTC when
    the value is naive, otherwise convert. Mirrors
    ``api/time_utils.py::isoformat_utc``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@transaction.atomic
def import_history_jsonl(jsonl_path: pathlib.Path) -> int:
    """Sync the legacy history JSONL file into ``ExecutionHistory``.

    Idempotent: repeated calls update existing rows in-place via
    ``update_or_create`` keyed by the ``(execution_id, slot_id,
    scenario_id)`` triple that backs ``uq_execution_history_identity``.
    Returns the number of rows imported (one per non-empty JSONL line).

    Missing or empty file is OK — returns 0 without raising. This mirrors
    ``api/history.py::import_history_jsonl`` (which also tolerates the
    file being absent on first boot).
    """
    if not jsonl_path.exists():
        return 0
    imported = 0
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            slot_id = str(row.get("slot_id", ""))
            scenario_id = str(row.get("scenario_id", ""))
            if not slot_id or not scenario_id:
                continue
            execution_id_raw = row.get("execution_id")
            execution_id = str(execution_id_raw) if execution_id_raw else None
            ExecutionHistory.objects.update_or_create(
                execution_id=execution_id,
                slot_id=slot_id,
                scenario_id=scenario_id,
                defaults={
                    "slot_key": str(row.get("slot_key", "")),
                    "executed_at": _parse_iso_utc(str(row.get("executed_at"))),
                    "status": str(row.get("status", "")),
                    "step": str(row.get("step", "")),
                    "message": str(row.get("message", "")),
                    "updated_at": _parse_iso_utc(row.get("updated_at")) if row.get("updated_at") else None,
                },
            )
            imported += 1
    return imported


def list_history(
    *,
    status: str | None = None,
    slot_id: str | None = None,
    scenario_id: str | None = None,
    scenario_ids: set[str] | None = None,
    execution_id: str | None = None,
) -> QuerySet[ExecutionHistory]:
    """Return the execution-history queryset matching the optional filters.

    Order matches ``api/history.py::list_history`` (newest first, with
    ``id`` as a deterministic tie-breaker). When ``scenario_id`` is
    provided it takes precedence over ``scenario_ids`` (single-row
    permission filter); when ``scenario_ids`` is provided AND empty the
    queryset is forced empty (mirrors the FastAPI early-return path).
    """
    qs = ExecutionHistory.objects.all()
    if status is not None:
        qs = qs.filter(status=status)
    if slot_id is not None:
        qs = qs.filter(slot_id=slot_id)
    if scenario_id is not None:
        qs = qs.filter(scenario_id=scenario_id)
    elif scenario_ids is not None:
        if not scenario_ids:
            return ExecutionHistory.objects.none()
        qs = qs.filter(scenario_id__in=scenario_ids)
    if execution_id is not None:
        qs = qs.filter(execution_id=execution_id)
    return qs.order_by("-executed_at", "-id")


def serialize_history(record: ExecutionHistory) -> dict[str, Any]:
    """Serialise an ``ExecutionHistory`` row into the API payload shape.

    Mirrors ``api/serializers.py::serialize_history``. ``executed_at`` is
    rendered as an ISO 8601 UTC string with ``Z`` suffix (never ``None``
    — the DB column is non-nullable).
    """
    return {
        "id": record.id,
        "slot_key": record.slot_key,
        "slot_id": record.slot_id,
        "scenario_id": record.scenario_id,
        "execution_id": record.execution_id,
        "executed_at": _utc_iso(record.executed_at) or "",
        "status": record.status,
        "step": record.step,
        "message": record.message,
        "updated_at": _utc_iso(record.updated_at),
    }

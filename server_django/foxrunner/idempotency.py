from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from ninja.errors import HttpError
from ops.models import IdempotencyKey


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _coerce_uuid(value: str | UUID) -> UUID:
    """Coerce a UUID-or-string into a UUID for the ``IdempotencyKey.user_id`` UUIDField."""
    return value if isinstance(value, UUID) else UUID(str(value))


def get_idempotent_response(request, *, user_id: str | UUID, payload: Any) -> dict[str, Any] | None:
    """Return the stored response for the (user_id, Idempotency-Key) pair, or None."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    user_uuid = _coerce_uuid(user_id)
    record = IdempotencyKey.objects.filter(user_id=user_uuid, key=key).first()
    if record is None:
        return None
    if record.request_fingerprint != _fingerprint(payload):
        raise HttpError(409, "Idempotency-Key reutilisee avec un payload different.")
    return record.response or {}


def store_idempotent_response(
    request,
    *,
    user_id: str | UUID,
    payload: Any,
    response: dict[str, Any],
    status_code: int = 200,
) -> None:
    """Persist the response for an Idempotency-Key. Race-safe."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return
    user_uuid = _coerce_uuid(user_id)
    fingerprint = _fingerprint(payload)
    try:
        with transaction.atomic():
            IdempotencyKey.objects.create(
                user_id=user_uuid,
                key=key,
                request_fingerprint=fingerprint,
                response=response,
                status_code=status_code,
            )
    except IntegrityError:
        existing = IdempotencyKey.objects.filter(user_id=user_uuid, key=key).first()
        if existing is not None and existing.request_fingerprint != fingerprint:
            raise HttpError(409, "Idempotency-Key reutilisee avec un payload different.") from None
        # else: the concurrent inserter stored the same fingerprint — silent success

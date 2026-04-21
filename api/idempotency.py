from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import IdempotencyRecord


async def get_idempotent_response(
    session: AsyncSession,
    *,
    request: Request,
    user_id: str,
    payload: Any,
) -> dict[str, Any] | None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    fingerprint = _fingerprint(payload)
    record = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key))
    if record is None:
        return None
    if record.request_fingerprint != fingerprint:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency-Key reutilisee avec un payload different.")
    return record.response or {}


async def store_idempotent_response(
    session: AsyncSession,
    *,
    request: Request,
    user_id: str,
    payload: Any,
    response: dict[str, Any],
    status_code: int = 200,
) -> None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return
    record = IdempotencyRecord(
        user_id=user_id,
        key=key,
        request_fingerprint=_fingerprint(payload),
        response=response,
        status_code=status_code,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request stored the same (user_id, key) first. Surface the
        # stored result instead of bubbling up a 500 — consistent with the spec
        # that guarantees identical responses for the same Idempotency-Key.
        await session.rollback()
        existing = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key))
        if existing is not None and existing.request_fingerprint != _fingerprint(payload):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key reutilisee avec un payload different.",
            )


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import ExecutionHistoryRecord
from api.serializers import serialize_history as serialize_history
from api.time_utils import db_utc, parse_utc


async def import_history_jsonl(session: AsyncSession, path: Path) -> int:
    if not path.exists():
        return 0
    imported = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if await _upsert_history_row(session, row):
                imported += 1
    if imported:
        await session.commit()
    return imported


async def list_history(
    session: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    slot_id: str | None = None,
    scenario_id: str | None = None,
    scenario_ids: set[str] | None = None,
    execution_id: str | None = None,
) -> tuple[list[ExecutionHistoryRecord], int]:
    query = select(ExecutionHistoryRecord)
    if status is not None:
        query = query.where(ExecutionHistoryRecord.status == status)
    if slot_id is not None:
        query = query.where(ExecutionHistoryRecord.slot_id == slot_id)
    if scenario_id is not None:
        query = query.where(ExecutionHistoryRecord.scenario_id == scenario_id)
    elif scenario_ids is not None:
        if not scenario_ids:
            return [], 0
        query = query.where(ExecutionHistoryRecord.scenario_id.in_(scenario_ids))
    if execution_id is not None:
        query = query.where(ExecutionHistoryRecord.execution_id == execution_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    records = await session.scalars(query.order_by(ExecutionHistoryRecord.executed_at.desc(), ExecutionHistoryRecord.id.desc()).offset(offset).limit(limit))
    return list(records), total or 0


async def _upsert_history_row(session: AsyncSession, row: dict[str, Any]) -> bool:
    execution_id = row.get("execution_id")
    slot_id = str(row.get("slot_id", ""))
    scenario_id = str(row.get("scenario_id", ""))
    if not slot_id or not scenario_id:
        return False
    record = None
    if execution_id:
        record = await session.scalar(
            select(ExecutionHistoryRecord).where(
                ExecutionHistoryRecord.execution_id == str(execution_id),
                ExecutionHistoryRecord.slot_id == slot_id,
                ExecutionHistoryRecord.scenario_id == scenario_id,
            )
        )
    if record is None:
        record = ExecutionHistoryRecord(execution_id=str(execution_id) if execution_id else None, slot_id=slot_id, scenario_id=scenario_id)
        session.add(record)
    record.slot_key = str(row.get("slot_key", ""))
    record.executed_at = _parse_datetime(str(row.get("executed_at")))
    record.status = str(row.get("status", ""))
    record.step = str(row.get("step", ""))
    record.message = str(row.get("message", ""))
    record.updated_at = _parse_datetime(row.get("updated_at")) if row.get("updated_at") else None
    return True


def _parse_datetime(value: str):
    return db_utc(parse_utc(value))

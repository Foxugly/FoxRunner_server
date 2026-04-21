from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from api.models import ScenarioRecord, ScenarioShareRecord, SlotRecord
from scenarios.loader import ScenarioDefinition, build_scenarios_from_map, build_slots_from_items, validate_scenarios_document, validate_slots_document
from scheduler.model import TimeSlot

STEP_COLLECTIONS = frozenset({"before_steps", "steps", "on_success", "on_failure", "finally_steps"})


async def seed_catalog_from_json(session: AsyncSession, scenarios_file: Path, slots_file: Path) -> None:
    existing = await session.scalar(select(ScenarioRecord.id).limit(1))
    if existing is not None:
        return
    scenarios_raw = _load_json(scenarios_file)
    slots_raw = _load_json(slots_file)
    validate_scenarios_document(scenarios_raw, scenarios_file.name)
    validate_slots_document(slots_raw, slots_file.name)

    for scenario_id, definition in scenarios_raw.get("scenarios", {}).items():
        if not isinstance(definition, dict):
            continue
        owner = _owner_for_definition(definition)
        session.add(
            ScenarioRecord(
                scenario_id=scenario_id,
                owner_user_id=owner,
                description=str(definition.get("description", "")),
                definition=definition,
            )
        )
        for user_id in _shared_users_for_definition(definition):
            session.add(ScenarioShareRecord(scenario_id=scenario_id, user_id=user_id))

    for slot in slots_raw.get("slots", []):
        session.add(
            SlotRecord(
                slot_id=slot["id"],
                scenario_id=slot["scenario"],
                days=slot["days"],
                start=slot["start"],
                end=slot["end"],
            )
        )
    await session.commit()


async def list_scenarios_for_user(session: AsyncSession, user_id: str, *, is_superuser: bool = False) -> list[ScenarioRecord]:
    if is_superuser:
        result = await session.scalars(select(ScenarioRecord).order_by(ScenarioRecord.scenario_id))
        return list(result)
    shared = select(ScenarioShareRecord.scenario_id).where(ScenarioShareRecord.user_id == user_id)
    result = await session.scalars(
        select(ScenarioRecord).where((ScenarioRecord.owner_user_id == user_id) | (ScenarioRecord.scenario_id.in_(shared))).order_by(ScenarioRecord.scenario_id)
    )
    return list(result)


async def scenario_ids_for_user(session: AsyncSession, user_id: str, *, is_superuser: bool = False) -> set[str]:
    return {scenario.scenario_id for scenario in await list_scenarios_for_user(session, user_id, is_superuser=is_superuser)}


async def get_scenario_for_user(
    session: AsyncSession,
    user_id: str,
    scenario_id: str,
    *,
    is_superuser: bool = False,
) -> ScenarioRecord:
    scenario = await session.scalar(select(ScenarioRecord).where(ScenarioRecord.scenario_id == scenario_id))
    if scenario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario introuvable.")
    if is_superuser or scenario.owner_user_id == user_id:
        return scenario
    shared = await session.scalar(
        select(ScenarioShareRecord.id).where(
            ScenarioShareRecord.scenario_id == scenario_id,
            ScenarioShareRecord.user_id == user_id,
        )
    )
    if shared is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario introuvable pour cet utilisateur.")
    return scenario


async def list_slots_for_scenarios(session: AsyncSession, scenario_ids: set[str]) -> list[SlotRecord]:
    if not scenario_ids:
        return []
    result = await session.scalars(select(SlotRecord).where(SlotRecord.scenario_id.in_(scenario_ids)).order_by(SlotRecord.slot_id))
    return list(result)


async def load_scheduler_catalog(session: AsyncSession) -> tuple[tuple[TimeSlot, ...], dict[str, ScenarioDefinition]]:
    scenario_records = list(await session.scalars(select(ScenarioRecord).order_by(ScenarioRecord.scenario_id)))
    slot_records = list(await session.scalars(select(SlotRecord).where(SlotRecord.enabled.is_(True)).order_by(SlotRecord.slot_id)))
    scenarios = build_scenarios_from_map(
        {record.scenario_id: record.definition for record in scenario_records},
        "database scenarios",
    )
    slots = build_slots_from_items(
        [
            {
                "id": record.slot_id,
                "days": record.days,
                "start": record.start,
                "end": record.end,
                "scenario": record.scenario_id,
            }
            for record in slot_records
        ],
        "database slots",
    )
    return slots, scenarios


def scenario_summary(record: ScenarioRecord) -> dict[str, object]:
    definition = record.definition or {}
    return {
        "scenario_id": record.scenario_id,
        "owner_user_id": record.owner_user_id,
        "description": record.description,
        "requires_enterprise_network": _requires_enterprise_network(definition),
        "before_steps": len(definition.get("before_steps", [])),
        "steps": len(definition.get("steps", [])),
        "on_success": len(definition.get("on_success", [])),
        "on_failure": len(definition.get("on_failure", [])),
        "finally_steps": len(definition.get("finally_steps", [])),
    }


def slot_summary(record: SlotRecord) -> dict[str, object]:
    return {
        "slot_id": record.slot_id,
        "days": record.days,
        "start": record.start,
        "end": record.end,
        "scenario_id": record.scenario_id,
        "enabled": record.enabled,
    }


async def get_scenario(session: AsyncSession, scenario_id: str) -> ScenarioRecord:
    record = await session.scalar(select(ScenarioRecord).where(ScenarioRecord.scenario_id == scenario_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario introuvable.")
    return record


async def create_scenario(
    session: AsyncSession,
    *,
    scenario_id: str,
    owner_user_id: str,
    description: str = "",
    definition: dict[str, Any] | None = None,
) -> ScenarioRecord:
    if await session.scalar(select(ScenarioRecord.id).where(ScenarioRecord.scenario_id == scenario_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Scenario deja existant.")
    payload = definition or {"description": description, "steps": []}
    payload.setdefault("description", description)
    payload.setdefault("steps", [])
    payload["owner_user_id"] = owner_user_id
    record = ScenarioRecord(
        scenario_id=scenario_id,
        owner_user_id=owner_user_id,
        description=str(payload.get("description", "")),
        definition=payload,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def delete_scenario(session: AsyncSession, scenario_id: str) -> None:
    record = await get_scenario(session, scenario_id)
    await session.delete(record)
    shares = list(await session.scalars(select(ScenarioShareRecord).where(ScenarioShareRecord.scenario_id == scenario_id)))
    for share in shares:
        await session.delete(share)
    await session.commit()


async def share_scenario(session: AsyncSession, scenario_id: str, user_id: str) -> ScenarioShareRecord:
    await get_scenario(session, scenario_id)
    existing = await session.scalar(
        select(ScenarioShareRecord).where(
            ScenarioShareRecord.scenario_id == scenario_id,
            ScenarioShareRecord.user_id == user_id,
        )
    )
    if existing:
        return existing
    record = ScenarioShareRecord(scenario_id=scenario_id, user_id=user_id)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def unshare_scenario(session: AsyncSession, scenario_id: str, user_id: str) -> None:
    record = await session.scalar(
        select(ScenarioShareRecord).where(
            ScenarioShareRecord.scenario_id == scenario_id,
            ScenarioShareRecord.user_id == user_id,
        )
    )
    if record is not None:
        await session.delete(record)
        await session.commit()


async def list_scenario_shares(session: AsyncSession, scenario_id: str) -> list[str]:
    result = await session.scalars(select(ScenarioShareRecord.user_id).where(ScenarioShareRecord.scenario_id == scenario_id).order_by(ScenarioShareRecord.user_id))
    return list(result)


async def get_slot(session: AsyncSession, slot_id: str) -> SlotRecord:
    record = await session.scalar(select(SlotRecord).where(SlotRecord.slot_id == slot_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot introuvable.")
    return record


async def create_slot(
    session: AsyncSession,
    *,
    slot_id: str,
    scenario_id: str,
    days: list[int],
    start: str,
    end: str,
    enabled: bool = True,
) -> SlotRecord:
    await get_scenario(session, scenario_id)
    if await session.scalar(select(SlotRecord.id).where(SlotRecord.slot_id == slot_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slot deja existant.")
    record = SlotRecord(slot_id=slot_id, scenario_id=scenario_id, days=days, start=start, end=end, enabled=enabled)
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def export_slots_document(session: AsyncSession) -> dict[str, Any]:
    records = list(await session.scalars(select(SlotRecord).order_by(SlotRecord.slot_id)))
    return {
        "slots": [
            {
                "id": record.slot_id,
                "days": record.days,
                "start": record.start,
                "end": record.end,
                "scenario": record.scenario_id,
            }
            for record in records
            if record.enabled
        ]
    }


async def sync_slots_file(session: AsyncSession, slots_file: Path) -> None:
    raw = await export_slots_document(session)
    validate_slots_document(raw, slots_file.name)
    _write_json_atomic(slots_file, raw)


def ensure_step_collection(collection: str) -> None:
    if collection not in STEP_COLLECTIONS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection d'etapes introuvable.")


def step_collection(definition: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    steps = definition.get(collection, [])
    if not isinstance(steps, list):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Collection invalide: {collection}")
    return steps


def mutable_step_collection(definition: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    if collection not in definition:
        definition[collection] = []
    return step_collection(definition, collection)


def step_at(steps: list[dict[str, Any]], index: int) -> dict[str, Any]:
    if index < 0 or index >= len(steps):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etape introuvable.")
    step = steps[index]
    if not isinstance(step, dict):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Etape invalide.")
    return step


async def save_scenario_definition(
    session: AsyncSession,
    record: ScenarioRecord,
    definition: dict[str, Any],
    scenarios_file: Path,
) -> None:
    raw = await export_scenarios_document(session, scenarios_file)
    raw["scenarios"][record.scenario_id] = definition
    try:
        validate_scenarios_document(raw, scenarios_file.name)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    record.definition = definition
    record.description = str(definition.get("description", ""))
    flag_modified(record, "definition")
    await session.commit()
    _write_json_atomic(scenarios_file, raw)


async def export_scenarios_document(session: AsyncSession, scenarios_file: Path) -> dict[str, Any]:
    current = _load_json(scenarios_file)
    current["scenarios"] = {}
    result = await session.scalars(select(ScenarioRecord).order_by(ScenarioRecord.scenario_id))
    for record in result:
        current["scenarios"][record.scenario_id] = record.definition
    return current


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: document racine invalide.")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    temp_file = path.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_file.replace(path)


def _owner_for_definition(definition: dict[str, Any]) -> str:
    raw = definition.get("user_id", definition.get("owner_user_id"))
    if raw is not None:
        return str(raw)
    raw_users = definition.get("user_ids")
    if isinstance(raw_users, list) and raw_users:
        return str(raw_users[0])
    return "default"


def _shared_users_for_definition(definition: dict[str, Any]) -> set[str]:
    raw_users = definition.get("user_ids")
    if not isinstance(raw_users, list):
        return set()
    return {str(item) for item in raw_users}


def _requires_enterprise_network(definition: dict[str, Any]) -> bool:
    return any(_step_requires_enterprise_network(step) for collection in STEP_COLLECTIONS for step in definition.get(collection, []))


def _step_requires_enterprise_network(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    if step.get("type") == "require_enterprise_network":
        return True
    if step.get("type") in {"group", "parallel", "repeat"}:
        return any(_step_requires_enterprise_network(child) for child in step.get("steps", []))
    if step.get("type") == "try":
        return any(_step_requires_enterprise_network(child) for key in ("try_steps", "catch_steps", "finally_steps") for child in step.get(key, []))
    return False

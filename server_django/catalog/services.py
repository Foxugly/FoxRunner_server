"""Catalog domain services.

Hosts the logic currently living in ``api/catalog.py``. The per-scenario
threading lock around ``save_scenario_definition`` ensures concurrent API
writes for the same scenario serialize safely (the JSON-file sync added
in Phase 4.2 needs single-writer semantics).
"""

from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from accounts.models import User
from accounts.permissions import require_user_scope, resolve_user
from django.db import transaction
from django.db.models import Q, QuerySet
from ninja.errors import HttpError
from ops.services import write_audit

from app.config import load_config
from app.main import build_runtime_services_from_catalog
from catalog.models import Scenario, ScenarioShare, Slot
from catalog.permissions import _is_scenario_owner, require_scenario_owner, scenario_role
from scenarios.loader import (
    ScenarioDefinition,
    build_scenarios_from_map,
    build_slots_from_items,
    load_scenario_data,
    validate_scenarios_document,
    validate_slots_document,
)
from scheduler.model import TimeSlot

if TYPE_CHECKING:
    from scheduler.service import SchedulerService

STEP_COLLECTIONS = frozenset({"before_steps", "steps", "on_success", "on_failure", "finally_steps"})

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_SLOTS_FILE_LOCK = threading.Lock()


def _lock_for(scenario_id: str) -> threading.Lock:
    """Return a per-scenario lock so concurrent writes for the same scenario serialize.

    A separate guard lock protects the dict from concurrent insertion races.
    """
    with _LOCKS_GUARD:
        return _LOCKS[scenario_id]


def _load_json_dict(path: Path) -> dict[str, Any]:
    """Read a JSON file as a dict; return {} when the file is missing.

    Mirrors ``api/catalog._load_json`` but tolerates a missing file (the
    Django ports run inside tests with tempdirs that may not have either
    file pre-seeded).
    """
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: document racine invalide.")
    return data


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically via a sibling .tmp file + os.replace.

    Mirrors ``api/catalog._write_json_atomic``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(path.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp_file, path)


def _build_scenarios_document(scenarios_file: Path) -> dict[str, Any]:
    """Return the full ``scenarios.json`` payload from the DB + existing file.

    Preserves any top-level keys (``schema_version``, ``data`` -- which
    nests ``default_pushover``, ``default_network``, ``pushovers``,
    ``networks``) the file may already carry; only the ``scenarios`` map
    is replaced from the DB. Falls back to a minimal but valid
    ``schema_version=1`` + empty ``data`` skeleton when the file is
    missing so the validator passes on first write.
    """
    document = _load_json_dict(scenarios_file)
    document.setdefault("schema_version", 1)
    document.setdefault("data", {})
    document["scenarios"] = {
        record.scenario_id: record.definition for record in Scenario.objects.all().order_by("scenario_id")
    }
    return document


def _write_scenarios_file() -> None:
    """Validate + atomically rewrite ``config.runtime.scenarios_file``.

    Sync mirror of ``api/catalog.sync_scenarios_file`` (the helper that
    runs after every CRUD on Scenario rows). Validates against the same
    JSON-schema as the FastAPI path -- any error becomes ``HttpError(422)``
    so the partial DB write is rolled back by the surrounding ``@transaction.atomic``.
    """
    config = load_config()
    scenarios_file = config.runtime.scenarios_file
    document = _build_scenarios_document(scenarios_file)
    try:
        validate_scenarios_document(document, scenarios_file.name)
    except Exception as exc:
        raise HttpError(422, str(exc)) from exc
    _write_json_atomic(scenarios_file, document)


def sync_slots_file() -> None:
    """Validate + atomically rewrite ``config.runtime.slots_file``.

    Mirrors ``api/catalog.sync_slots_file``: only ``enabled=True`` slots
    appear in the output. The ``_SLOTS_FILE_LOCK`` serializes concurrent
    writers since there is a single slots file.
    """
    config = load_config()
    slots_file = config.runtime.slots_file
    with _SLOTS_FILE_LOCK:
        document = {
            "slots": [
                {
                    "id": slot.slot_id,
                    "days": list(slot.days or []),
                    "start": slot.start,
                    "end": slot.end,
                    "scenario": slot.scenario_id,
                }
                for slot in Slot.objects.filter(enabled=True).order_by("slot_id")
            ]
        }
        try:
            validate_slots_document(document, slots_file.name)
        except Exception as exc:
            raise HttpError(422, str(exc)) from exc
        _write_json_atomic(slots_file, document)


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


def _requires_enterprise_network(definition: dict[str, Any]) -> bool:
    return any(_step_requires_enterprise_network(step) for collection in STEP_COLLECTIONS for step in definition.get(collection, []))


def scenario_summary_for_user(record: Scenario, user: User) -> dict[str, Any]:
    """Return ``scenario_summary(record)`` augmented with ``role`` + ``writable``.

    Mirrors ``api/routers/common.py::scenario_summary_for_user``.
    """
    role, writable = scenario_role(record, user)
    return {**scenario_summary(record), "role": role, "writable": writable}


def scenario_summary(record: Scenario) -> dict[str, Any]:
    """Return the ``ScenarioOut`` payload for a Scenario row.

    The ``owner_user_id`` field is serialized from ``record.owner_id`` (the
    UUID PK of the FK target) cast to ``str`` -- the frontend contract
    still expects a string-shaped owner identifier.
    """
    definition = record.definition or {}
    return {
        "scenario_id": record.scenario_id,
        "owner_user_id": str(record.owner_id),
        "description": record.description,
        "requires_enterprise_network": _requires_enterprise_network(definition),
        "before_steps": len(definition.get("before_steps", [])),
        "steps": len(definition.get("steps", [])),
        "on_success": len(definition.get("on_success", [])),
        "on_failure": len(definition.get("on_failure", [])),
        "finally_steps": len(definition.get("finally_steps", [])),
    }


@transaction.atomic
def save_scenario_definition(
    scenario: Scenario,
    definition: dict[str, Any],
    *,
    description: str | None = None,
) -> Scenario:
    """Persist a new definition for a scenario AND mirror it to the JSON file.

    Sync port of ``api/catalog.save_scenario_definition`` (lines 340-358 in
    the FastAPI source). The per-scenario lock serializes the read-DB +
    rewrite-JSON sequence; ``_write_scenarios_file`` validates the full
    document via ``validate_scenarios_document`` and raises ``HttpError(422)``
    on schema failure so the surrounding ``@transaction.atomic`` rolls the
    DB row back -- the JSON file is left untouched (we validate BEFORE the
    atomic replace).
    """
    with _lock_for(scenario.scenario_id):
        scenario.definition = definition
        if description is not None:
            scenario.description = description
        scenario.save()
        _write_scenarios_file()
        return scenario


# --------------------------------------------------------------------------
# Low-level CRUD (porting the SQLAlchemy helpers in api/catalog.py)
# --------------------------------------------------------------------------


def get_scenario(scenario_id: str) -> Scenario:
    try:
        return Scenario.objects.get(scenario_id=scenario_id)
    except Scenario.DoesNotExist:
        raise HttpError(404, "Scenario introuvable.") from None


def get_scenario_for_user(scenario_id: str, user: User) -> Scenario:
    """Return the Scenario if ``user`` is owner, share-recipient, or superuser.

    Raises ``HttpError(404)`` if the scenario does not exist or if the
    user has no access (the FastAPI implementation also collapses both
    cases into 404 to avoid leaking existence).
    """
    try:
        scenario = Scenario.objects.get(scenario_id=scenario_id)
    except Scenario.DoesNotExist:
        raise HttpError(404, "Scenario introuvable.") from None
    if user.is_superuser or _is_scenario_owner(scenario, user):
        return scenario
    if ScenarioShare.objects.filter(scenario=scenario, user=user).exists():
        return scenario
    raise HttpError(404, "Scenario introuvable pour cet utilisateur.")


@transaction.atomic
def create_scenario(
    *,
    scenario_id: str,
    owner: User,
    description: str = "",
    definition: dict[str, Any] | None = None,
) -> Scenario:
    if Scenario.objects.filter(scenario_id=scenario_id).exists():
        raise HttpError(409, "Scenario deja existant.")
    payload = dict(definition or {"description": description, "steps": []})
    payload.setdefault("description", description)
    payload.setdefault("steps", [])
    payload["owner_user_id"] = str(owner.id)
    return Scenario.objects.create(
        scenario_id=scenario_id,
        owner=owner,
        description=str(payload.get("description", "")),
        definition=payload,
    )


@transaction.atomic
def delete_scenario(scenario_id: str) -> None:
    """Delete a scenario row and any ScenarioShare attached to it.

    The FK from ``ScenarioShare.scenario`` to ``Scenario.scenario_id``
    cascades on delete (``on_delete=CASCADE``), but the FastAPI version
    explicitly deletes shares first; the explicit cascade is preserved
    here for parity in case the FK becomes nullable later.
    """
    record = get_scenario(scenario_id)
    ScenarioShare.objects.filter(scenario=record).delete()
    record.delete()


@transaction.atomic
def share_scenario(scenario_id: str, user_id: str) -> ScenarioShare:
    """Idempotent share. Returns the existing row when (scenario, user) already exists.

    ``user_id`` is the request-supplied identifier (UUID string or email) --
    resolved to a real ``User`` row via ``resolve_user``. The FK stores the
    UUID; the email is only an input alias.
    """
    scenario = get_scenario(scenario_id)
    user = resolve_user(user_id)
    existing = ScenarioShare.objects.filter(scenario=scenario, user=user).first()
    if existing is not None:
        return existing
    return ScenarioShare.objects.create(scenario=scenario, user=user)


@transaction.atomic
def unshare_scenario(scenario_id: str, user_id: str) -> None:
    """Silently no-op when the share does not exist (matches FastAPI).

    ``user_id`` accepts UUID-or-email like ``share_scenario``.
    """
    try:
        user = resolve_user(user_id)
    except HttpError:
        return
    ScenarioShare.objects.filter(scenario__scenario_id=scenario_id, user=user).delete()


def list_scenario_shares(scenario_id: str) -> list[str]:
    return [str(uid) for uid in ScenarioShare.objects.filter(scenario__scenario_id=scenario_id).order_by("user_id").values_list("user_id", flat=True)]


# --------------------------------------------------------------------------
# Business helpers (ported from api/services/scenarios.py)
# --------------------------------------------------------------------------


@transaction.atomic
def create_owned_scenario(*, payload, current_user: User) -> dict[str, Any]:
    require_user_scope(payload.owner_user_id, current_user)
    # ``payload.owner_user_id`` is a UUID-or-email string; the FK now
    # demands an actual User row.
    owner = current_user if payload.owner_user_id in {str(current_user.id), current_user.email} else resolve_user(payload.owner_user_id)
    record = create_scenario(
        scenario_id=payload.scenario_id,
        owner=owner,
        description=payload.description,
        definition=payload.definition,
    )
    save_scenario_definition(record, record.definition)
    result = scenario_summary(record)
    write_audit(
        actor=current_user,
        action="scenario.create",
        target_type="scenario",
        target_id=record.scenario_id,
        after=result,
    )
    return result


@transaction.atomic
def update_owned_scenario(*, scenario_id: str, payload, current_user: User) -> dict[str, Any]:
    record = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(record, current_user)
    before = scenario_summary(record)
    if payload.scenario_id and payload.scenario_id != record.scenario_id:
        if Scenario.objects.filter(scenario_id=payload.scenario_id).exists():
            raise HttpError(409, "Scenario deja existant.")
        # Cascade rename to all slots referencing this scenario.
        Slot.objects.filter(scenario=record).update(scenario_id=payload.scenario_id)
        record.scenario_id = payload.scenario_id
    if payload.owner_user_id is not None:
        require_user_scope(payload.owner_user_id, current_user)
        record.owner = current_user if payload.owner_user_id in {str(current_user.id), current_user.email} else resolve_user(payload.owner_user_id)
    definition = dict(record.definition or {})
    if payload.description is not None:
        definition["description"] = payload.description
    if payload.definition is not None:
        definition = payload.definition
    record.definition = definition
    save_scenario_definition(record, definition)
    result = scenario_summary(record)
    write_audit(
        actor=current_user,
        action="scenario.update",
        target_type="scenario",
        target_id=record.scenario_id,
        before=before,
        after=result,
    )
    return result


@transaction.atomic
def duplicate_owned_scenario(*, scenario_id: str, new_scenario_id: str, current_user: User) -> dict[str, Any]:
    source = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(source, current_user)
    record = create_scenario(
        scenario_id=new_scenario_id,
        owner=source.owner,
        description=source.description,
        definition=dict(source.definition or {}),
    )
    save_scenario_definition(record, record.definition)
    result = scenario_summary(record)
    write_audit(
        actor=current_user,
        action="scenario.duplicate",
        target_type="scenario",
        target_id=new_scenario_id,
        before={"source": scenario_id},
        after=result,
    )
    return result


@transaction.atomic
def delete_owned_scenario(*, scenario_id: str, current_user: User) -> dict[str, Any]:
    record = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(record, current_user)
    if Slot.objects.filter(scenario=record).exists():
        raise HttpError(409, "Supprime ou deplace les slots avant le scenario.")
    before = scenario_summary(record)
    delete_scenario(scenario_id)
    _write_scenarios_file()
    write_audit(
        actor=current_user,
        action="scenario.delete",
        target_type="scenario",
        target_id=scenario_id,
        before=before,
    )
    return {"deleted": scenario_id}


@transaction.atomic
def share_owned_scenario(*, scenario_id: str, share_user_id: str, current_user: User) -> dict[str, Any]:
    record = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(record, current_user)
    share = share_scenario(scenario_id, share_user_id)
    write_audit(
        actor=current_user,
        action="scenario.share",
        target_type="scenario",
        target_id=scenario_id,
        after={"user_id": str(share.user_id)},
    )
    return {"scenario_id": scenario_id, "user_id": str(share.user_id)}


@transaction.atomic
def unshare_owned_scenario(*, scenario_id: str, share_user_id: str, current_user: User) -> dict[str, Any]:
    record = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(record, current_user)
    unshare_scenario(scenario_id, share_user_id)
    write_audit(
        actor=current_user,
        action="scenario.unshare",
        target_type="scenario",
        target_id=scenario_id,
        before={"user_id": share_user_id},
    )
    return {"deleted": share_user_id}


# --------------------------------------------------------------------------
# Slot CRUD (porting api/catalog.py:263-309 + api/catalog_queries.py +
# api/services/slots.py).
# --------------------------------------------------------------------------


def slot_summary(record: Slot) -> dict[str, Any]:
    """Return the ``SlotOut`` payload for a Slot row.

    Mirrors ``api/catalog.py::slot_summary``.
    """
    return {
        "slot_id": record.slot_id,
        "days": list(record.days or []),
        "start": record.start,
        "end": record.end,
        "scenario_id": record.scenario_id,
        "enabled": record.enabled,
    }


def get_slot(slot_id: str) -> Slot:
    try:
        return Slot.objects.get(slot_id=slot_id)
    except Slot.DoesNotExist:
        raise HttpError(404, "Slot introuvable.") from None


@transaction.atomic
def create_slot(
    *,
    slot_id: str,
    scenario_id: str,
    days: list[int],
    start: str,
    end: str,
    enabled: bool = True,
) -> Slot:
    # Ensure the scenario exists -- preserves the FastAPI 404 path when a
    # client posts a slot under an unknown scenario.
    scenario = get_scenario(scenario_id)
    if Slot.objects.filter(slot_id=slot_id).exists():
        raise HttpError(409, "Slot deja existant.")
    return Slot.objects.create(
        slot_id=slot_id,
        scenario=scenario,
        days=list(days),
        start=start,
        end=end,
        enabled=enabled,
    )


def accessible_scenarios_queryset(user: User) -> QuerySet[Scenario]:
    """Return the queryset of scenarios visible to ``user``.

    Port of ``api/catalog_queries.py::accessible_scenarios_query``: a
    scenario is accessible iff the user owns it, is a share-recipient, or
    is a superuser. Post-phase-5 the comparisons are FK-only (no email
    fallback) -- the dual-stack identifier shape was normalized away by
    ``catalog/0002_normalize_owner_user_id``.
    """
    qs = Scenario.objects.all()
    if user.is_superuser:
        return qs
    shared_ids = ScenarioShare.objects.filter(user=user).values_list("scenario__scenario_id", flat=True)
    return qs.filter(Q(owner=user) | Q(scenario_id__in=shared_ids))


def list_accessible_scenarios(
    user: User,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Scenario], int]:
    base = accessible_scenarios_queryset(user)
    total = base.count()
    rows = list(base.order_by("scenario_id")[offset : offset + limit])
    return rows, total


def aggregate_scenario_data() -> dict[str, Any]:
    """Read the JSON scenarios file and aggregate pushover/network keys.

    Mirrors ``api/routers/catalog.py::user_scenario_data`` (the data
    portion -- visibility check is performed by the caller). The
    ``load_scenario_data`` import is module-level so tests can patch
    ``catalog.services.load_scenario_data`` (the bound name in this
    module).
    """
    config = load_config()
    data = load_scenario_data(config.runtime.scenarios_file)
    return {
        "default_pushover_key": data.default_pushover_key,
        "default_network_key": data.default_network_key,
        "pushovers": sorted(data.pushovers),
        "networks": sorted(data.networks),
    }


def accessible_slots_queryset(
    user: User,
    *,
    scenario_id: str | None = None,
) -> QuerySet[Slot]:
    """Return the queryset of slots the user can read.

    Port of ``api/catalog_queries.py::accessible_slots_query``: a slot is
    accessible iff the user owns or is shared on its scenario (or is a
    superuser). The optional ``scenario_id`` filter preserves the FastAPI
    behaviour. Post-phase-5: FK-only comparisons.
    """
    qs = Slot.objects.all()
    if scenario_id is not None:
        qs = qs.filter(scenario_id=scenario_id)
    if user.is_superuser:
        return qs
    shared_ids = ScenarioShare.objects.filter(user=user).values_list("scenario__scenario_id", flat=True)
    return qs.filter(Q(scenario__owner=user) | Q(scenario_id__in=shared_ids))


def list_accessible_slots(
    user: User,
    *,
    scenario_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Slot], int]:
    base = accessible_slots_queryset(user, scenario_id=scenario_id)
    total = base.count()
    rows = list(base.order_by("slot_id")[offset : offset + limit])
    return rows, total


@transaction.atomic
def create_owned_slot(*, payload, current_user: User) -> dict[str, Any]:
    """Create a slot, requiring owner rights on the target scenario."""
    scenario = get_scenario_for_user(payload.scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    record = create_slot(
        slot_id=payload.slot_id,
        scenario_id=payload.scenario_id,
        days=payload.days,
        start=payload.start,
        end=payload.end,
        enabled=payload.enabled,
    )
    sync_slots_file()
    result = slot_summary(record)
    write_audit(
        actor=current_user,
        action="slot.create",
        target_type="slot",
        target_id=record.slot_id,
        after=result,
    )
    return result


@transaction.atomic
def update_owned_slot(*, slot_id: str, payload, current_user: User) -> dict[str, Any]:
    """Patch a slot. Requires owner rights on the slot's current scenario;
    when ``scenario_id`` is reassigned, owner rights are also required on
    the target scenario (parity with ``api/services/slots.py:42-45``).
    """
    record = get_slot(slot_id)
    scenario = get_scenario_for_user(record.scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    before = slot_summary(record)
    if payload.scenario_id is not None and payload.scenario_id != record.scenario_id:
        target = get_scenario_for_user(payload.scenario_id, current_user)
        require_scenario_owner(target, current_user)
        record.scenario = target
    if payload.days is not None:
        record.days = list(payload.days)
    if payload.start is not None:
        record.start = payload.start
    if payload.end is not None:
        record.end = payload.end
    if payload.enabled is not None:
        record.enabled = payload.enabled
    record.save()
    sync_slots_file()
    result = slot_summary(record)
    write_audit(
        actor=current_user,
        action="slot.update",
        target_type="slot",
        target_id=record.slot_id,
        before=before,
        after=result,
    )
    return result


@transaction.atomic
def delete_owned_slot(*, slot_id: str, current_user: User) -> dict[str, Any]:
    record = get_slot(slot_id)
    scenario = get_scenario_for_user(record.scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    before = slot_summary(record)
    record.delete()
    sync_slots_file()
    write_audit(
        actor=current_user,
        action="slot.delete",
        target_type="slot",
        target_id=slot_id,
        before=before,
    )
    return {"deleted": slot_id}


# --------------------------------------------------------------------------
# Step collections (Phase 4.5). Ports the helpers from ``api/catalog.py``
# (``ensure_step_collection``, ``step_collection``, ``mutable_step_collection``,
# ``step_at``) and the three mutation services from ``api/services/steps.py``
# (``create_step``, ``update_step``, ``delete_step``).
# --------------------------------------------------------------------------


def ensure_step_collection(collection: str) -> None:
    """Reject collection names not in the canonical set (404 like FastAPI)."""
    if collection not in STEP_COLLECTIONS:
        raise HttpError(404, "Collection d'etapes introuvable.")


def step_collection_view(definition: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    """Read-only access to a step collection. 500 if the JSON shape is wrong."""
    steps = definition.get(collection, [])
    if not isinstance(steps, list):
        raise HttpError(500, f"Collection invalide: {collection}")
    return steps


def mutable_step_collection(definition: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    """Return the collection list, creating it as an empty array if absent."""
    if collection not in definition:
        definition[collection] = []
    return step_collection_view(definition, collection)


def step_at(steps: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Bounds-checked accessor (404 for out-of-range, mirrors FastAPI)."""
    if index < 0 or index >= len(steps):
        raise HttpError(404, "Etape introuvable.")
    step = steps[index]
    if not isinstance(step, dict):
        raise HttpError(500, "Etape invalide.")
    return step


@transaction.atomic
def create_step(
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    payload,
    insert_at: int | None,
    current_user: User,
) -> dict[str, Any]:
    """Insert a step at ``insert_at`` (clamped to len) or append by default.

    Owner-only. Routed through ``save_scenario_definition`` so the
    per-scenario lock serializes the read-modify-write sequence.
    """
    require_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition or {})
    steps = mutable_step_collection(definition, collection)
    index = len(steps) if insert_at is None else min(insert_at, len(steps))
    steps.insert(index, payload.step)
    save_scenario_definition(scenario, definition)
    write_audit(
        actor=current_user,
        action="step.create",
        target_type="scenario",
        target_id=scenario_id,
        after={"collection": collection, "index": index, "step": payload.step},
    )
    return {"index": index, "step": payload.step}


@transaction.atomic
def update_step(
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    payload,
    current_user: User,
) -> dict[str, Any]:
    """Replace the step at ``index``. Owner-only. 404 on out-of-range."""
    require_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition or {})
    steps = mutable_step_collection(definition, collection)
    before = step_at(steps, index)
    steps[index] = payload.step
    save_scenario_definition(scenario, definition)
    write_audit(
        actor=current_user,
        action="step.update",
        target_type="scenario",
        target_id=scenario_id,
        before={"collection": collection, "index": index, "step": before},
        after={"collection": collection, "index": index, "step": payload.step},
    )
    return {"index": index, "step": payload.step}


@transaction.atomic
def delete_step(
    *,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    current_user: User,
) -> dict[str, Any]:
    """Remove the step at ``index``. Owner-only. 404 on out-of-range."""
    require_user_scope(user_id, current_user)
    ensure_step_collection(collection)
    scenario = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(scenario, current_user)
    definition = dict(scenario.definition or {})
    steps = mutable_step_collection(definition, collection)
    deleted = step_at(steps, index)
    del steps[index]
    save_scenario_definition(scenario, definition)
    write_audit(
        actor=current_user,
        action="step.delete",
        target_type="scenario",
        target_id=scenario_id,
        before={"collection": collection, "index": index, "step": deleted},
    )
    return {"index": index, "deleted": deleted}


# --------------------------------------------------------------------------
# Scheduler bridge (Phase 4.7). Sync ports of
# ``api/catalog.py::load_scheduler_catalog`` and
# ``api/dependencies.py::build_service_from_db``. The DB stays the source
# of truth; the JSON catalog file is only consulted via
# ``app.main.build_runtime_services_from_catalog`` for the auxiliary
# pushover/network metadata (``scenarios.loader.load_scenario_data``).
# --------------------------------------------------------------------------


def load_scheduler_catalog() -> tuple[tuple[TimeSlot, ...], dict[str, ScenarioDefinition]]:
    """Build the scheduler-friendly ``(slots, scenarios)`` tuple from the ORM.

    Sync mirror of ``api/catalog.py::load_scheduler_catalog``. Only
    enabled slots are returned -- disabled rows must not appear in the
    runtime catalog (parity with the FastAPI behaviour).
    """
    scenario_records = list(Scenario.objects.order_by("scenario_id"))
    slot_records = list(Slot.objects.filter(enabled=True).order_by("slot_id"))
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


def build_service_from_db(*, timezone_name: str | None = None) -> SchedulerService:
    """Construct a SchedulerService from the current DB catalog.

    Sync mirror of ``api/dependencies.py::build_service_from_db``. When
    ``timezone_name`` is provided it overrides ``config.runtime.timezone_name``
    after validation through :class:`zoneinfo.ZoneInfo` (which raises if
    the name is not a known IANA timezone).
    """
    config = load_config()
    if timezone_name is not None:
        ZoneInfo(timezone_name)  # validates -- raises ZoneInfoNotFoundError on bad input
        config = replace(config, runtime=replace(config.runtime, timezone_name=timezone_name))
    slots, scenarios = load_scheduler_catalog()
    return build_runtime_services_from_catalog(config, slots, scenarios)


def scenario_ids_for_user(user: User) -> set[str]:
    """Return the set of scenario IDs visible to a user.

    Mirrors ``api/catalog.py::scenario_ids_for_user``. The query reuses
    ``accessible_scenarios_queryset`` so the visibility rules stay in a
    single place.
    """
    return set(accessible_scenarios_queryset(user).values_list("scenario_id", flat=True))

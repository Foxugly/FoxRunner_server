"""Catalog domain services.

Hosts the logic currently living in ``api/catalog.py``. The per-scenario
threading lock around ``save_scenario_definition`` ensures concurrent API
writes for the same scenario serialize safely (the JSON-file sync added
in Phase 4.2 needs single-writer semantics).
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from accounts.models import User
from accounts.permissions import require_user_scope
from django.db import transaction
from django.db.models import Q, QuerySet
from ninja.errors import HttpError
from ops.services import write_audit

from catalog.models import Scenario, ScenarioShare, Slot
from catalog.permissions import _is_scenario_owner, require_scenario_owner

STEP_COLLECTIONS = frozenset({"before_steps", "steps", "on_success", "on_failure", "finally_steps"})

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _lock_for(scenario_id: str) -> threading.Lock:
    """Return a per-scenario lock so concurrent writes for the same scenario serialize.

    A separate guard lock protects the dict from concurrent insertion races.
    """
    with _LOCKS_GUARD:
        return _LOCKS[scenario_id]


def _actor_id(user: User) -> str:
    """Audit ``actor_user_id`` matches the FastAPI ``actor_id`` shape (UUID string)."""
    return str(user.id)


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


def scenario_summary(record: Scenario) -> dict[str, Any]:
    """Return the ``ScenarioOut`` payload for a Scenario row."""
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


@transaction.atomic
def save_scenario_definition(
    scenario: Scenario,
    definition: dict[str, Any],
    *,
    description: str | None = None,
) -> Scenario:
    """Persist a new definition for a scenario.

    TODO(phase-13): port the JSON-file sync from
    ``api.catalog.save_scenario_definition``. The dual-stack window keeps
    the FastAPI app responsible for ``config/scenarios.json`` so the CLI
    keeps working; once Phase 13 deletes the FastAPI tree this helper
    must validate via ``scenarios.loader.validate_scenarios_document``
    and atomically rewrite the file under
    ``config.runtime.scenarios_file``.
    """
    with _lock_for(scenario.scenario_id):
        scenario.definition = definition
        if description is not None:
            scenario.description = description
        scenario.save()
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
    candidates = {str(user.id), user.email}
    if ScenarioShare.objects.filter(scenario=scenario, user_id__in=candidates).exists():
        return scenario
    raise HttpError(404, "Scenario introuvable pour cet utilisateur.")


@transaction.atomic
def create_scenario(
    *,
    scenario_id: str,
    owner_user_id: str,
    description: str = "",
    definition: dict[str, Any] | None = None,
) -> Scenario:
    if Scenario.objects.filter(scenario_id=scenario_id).exists():
        raise HttpError(409, "Scenario deja existant.")
    payload = dict(definition or {"description": description, "steps": []})
    payload.setdefault("description", description)
    payload.setdefault("steps", [])
    payload["owner_user_id"] = owner_user_id
    return Scenario.objects.create(
        scenario_id=scenario_id,
        owner_user_id=owner_user_id,
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
    """Idempotent share. Returns the existing row when (scenario, user) already exists."""
    scenario = get_scenario(scenario_id)
    existing = ScenarioShare.objects.filter(scenario=scenario, user_id=user_id).first()
    if existing is not None:
        return existing
    return ScenarioShare.objects.create(scenario=scenario, user_id=user_id)


@transaction.atomic
def unshare_scenario(scenario_id: str, user_id: str) -> None:
    """Silently no-op when the share does not exist (matches FastAPI)."""
    ScenarioShare.objects.filter(scenario__scenario_id=scenario_id, user_id=user_id).delete()


def list_scenario_shares(scenario_id: str) -> list[str]:
    return list(ScenarioShare.objects.filter(scenario__scenario_id=scenario_id).order_by("user_id").values_list("user_id", flat=True))


# --------------------------------------------------------------------------
# Business helpers (ported from api/services/scenarios.py)
# --------------------------------------------------------------------------


@transaction.atomic
def create_owned_scenario(*, payload, current_user: User) -> dict[str, Any]:
    require_user_scope(payload.owner_user_id, current_user)
    record = create_scenario(
        scenario_id=payload.scenario_id,
        owner_user_id=payload.owner_user_id,
        description=payload.description,
        definition=payload.definition,
    )
    save_scenario_definition(record, record.definition)
    result = scenario_summary(record)
    write_audit(
        actor_user_id=_actor_id(current_user),
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
        record.owner_user_id = payload.owner_user_id
    definition = dict(record.definition or {})
    if payload.description is not None:
        definition["description"] = payload.description
    if payload.definition is not None:
        definition = payload.definition
    record.definition = definition
    save_scenario_definition(record, definition)
    result = scenario_summary(record)
    write_audit(
        actor_user_id=_actor_id(current_user),
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
        owner_user_id=source.owner_user_id,
        description=source.description,
        definition=dict(source.definition or {}),
    )
    save_scenario_definition(record, record.definition)
    result = scenario_summary(record)
    write_audit(
        actor_user_id=_actor_id(current_user),
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
    write_audit(
        actor_user_id=_actor_id(current_user),
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
        actor_user_id=_actor_id(current_user),
        action="scenario.share",
        target_type="scenario",
        target_id=scenario_id,
        after={"user_id": share.user_id},
    )
    return {"scenario_id": scenario_id, "user_id": share.user_id}


@transaction.atomic
def unshare_owned_scenario(*, scenario_id: str, share_user_id: str, current_user: User) -> dict[str, Any]:
    record = get_scenario_for_user(scenario_id, current_user)
    require_scenario_owner(record, current_user)
    unshare_scenario(scenario_id, share_user_id)
    write_audit(
        actor_user_id=_actor_id(current_user),
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


def accessible_slots_queryset(
    user: User,
    *,
    scenario_id: str | None = None,
) -> QuerySet[Slot]:
    """Return the queryset of slots the user can read.

    Port of ``api/catalog_queries.py::accessible_slots_query``: a slot is
    accessible iff the user owns or is shared on its scenario (or is a
    superuser). The optional ``scenario_id`` filter preserves the FastAPI
    behaviour.
    """
    qs = Slot.objects.all()
    if scenario_id is not None:
        qs = qs.filter(scenario_id=scenario_id)
    if user.is_superuser:
        return qs
    candidates = {str(user.id), user.email}
    shared_ids = ScenarioShare.objects.filter(user_id__in=candidates).values_list("scenario__scenario_id", flat=True)
    return qs.filter(Q(scenario__owner_user_id__in=candidates) | Q(scenario_id__in=shared_ids))


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
    # TODO(phase-13): port the JSON-file sync from
    # ``api.catalog.sync_slots_file``. The dual-stack window keeps the
    # FastAPI app responsible for ``config/slots.json`` so the CLI keeps
    # working; once Phase 13 deletes the FastAPI tree this helper must
    # call the equivalent of ``sync_slots_file`` via
    # ``config.runtime.slots_file``.
    result = slot_summary(record)
    write_audit(
        actor_user_id=_actor_id(current_user),
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
    # TODO(phase-13): port sync_slots_file (see create_owned_slot).
    result = slot_summary(record)
    write_audit(
        actor_user_id=_actor_id(current_user),
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
    # TODO(phase-13): port sync_slots_file (see create_owned_slot).
    write_audit(
        actor_user_id=_actor_id(current_user),
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
        actor_user_id=_actor_id(current_user),
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
        actor_user_id=_actor_id(current_user),
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
        actor_user_id=_actor_id(current_user),
        action="step.delete",
        target_type="scenario",
        target_id=scenario_id,
        before={"collection": collection, "index": index, "step": deleted},
    )
    return {"index": index, "deleted": deleted}

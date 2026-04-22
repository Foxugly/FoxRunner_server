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

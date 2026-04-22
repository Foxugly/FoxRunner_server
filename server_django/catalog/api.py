"""Ninja router for catalog endpoints (scenarios, slots, shares, steps).

Populated during migration phases 3 and 4. Each endpoint replaces the
corresponding FastAPI route in ``api/routers/catalog.py`` with 1:1
behavior.
"""

from __future__ import annotations

from foxrunner.idempotency import get_idempotent_response, store_idempotent_response
from ninja import Query, Router

from catalog import services as scenario_services
from catalog.schemas import (
    DeletedOut,
    ScenarioIn,
    ScenarioOut,
    ScenarioPatchIn,
    ShareIn,
    ShareList,
    ShareOut,
    SlotIn,
    SlotOut,
    SlotPage,
    SlotPatchIn,
)

router = Router(tags=["scenarios"])


@router.post("/scenarios", response={201: ScenarioOut})
def create_scenario_endpoint(request, payload: ScenarioIn):
    """Create a scenario.

    The ``Idempotency-Key`` header is honored: a replay with the same
    payload returns the stored response, a replay with a different
    payload returns 409 (raised inside ``get_idempotent_response``).
    The user_id used for the idempotency partition is the email -- this
    matches ``api/routers/catalog.py:67`` which passes
    ``str(current_user.email)`` rather than the UUID.
    """
    current_user = request.auth
    payload_dump = payload.model_dump()
    cached = get_idempotent_response(request, user_id=str(current_user.email), payload=payload_dump)
    if cached is not None:
        return 201, cached
    result = scenario_services.create_owned_scenario(payload=payload, current_user=current_user)
    store_idempotent_response(
        request,
        user_id=str(current_user.email),
        payload=payload_dump,
        response=result,
        status_code=201,
    )
    return 201, result


@router.patch("/scenarios/{scenario_id}", response=ScenarioOut)
def update_scenario_endpoint(request, scenario_id: str, payload: ScenarioPatchIn):
    return scenario_services.update_owned_scenario(
        scenario_id=scenario_id,
        payload=payload,
        current_user=request.auth,
    )


@router.post("/scenarios/{scenario_id}/duplicate", response={201: ScenarioOut})
def duplicate_scenario_endpoint(
    request,
    scenario_id: str,
    new_scenario_id: str = Query(...),
):
    result = scenario_services.duplicate_owned_scenario(
        scenario_id=scenario_id,
        new_scenario_id=new_scenario_id,
        current_user=request.auth,
    )
    return 201, result


@router.delete("/scenarios/{scenario_id}", response=DeletedOut)
def delete_scenario_endpoint(request, scenario_id: str):
    return scenario_services.delete_owned_scenario(
        scenario_id=scenario_id,
        current_user=request.auth,
    )


@router.get("/scenarios/{scenario_id}/shares", response=ShareList)
def list_scenario_shares_endpoint(request, scenario_id: str):
    # ``get_scenario_for_user`` enforces the owner-or-shared visibility
    # check (raises 404 if neither). Shares list is then returned.
    scenario_services.get_scenario_for_user(scenario_id, request.auth)
    return {
        "scenario_id": scenario_id,
        "user_ids": scenario_services.list_scenario_shares(scenario_id),
    }


@router.post("/scenarios/{scenario_id}/shares", response={201: ShareOut})
def share_scenario_endpoint(request, scenario_id: str, payload: ShareIn):
    result = scenario_services.share_owned_scenario(
        scenario_id=scenario_id,
        share_user_id=payload.user_id,
        current_user=request.auth,
    )
    return 201, result


@router.delete("/scenarios/{scenario_id}/shares/{share_user_id}", response=DeletedOut)
def unshare_scenario_endpoint(request, scenario_id: str, share_user_id: str):
    return scenario_services.unshare_owned_scenario(
        scenario_id=scenario_id,
        share_user_id=share_user_id,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# Slots (Phase 4.4). Five endpoints under /api/v1/slots. The same router
# is reused; per-handler ``tags=["slots"]`` keeps the OpenAPI grouping
# consistent with the FastAPI app.
# --------------------------------------------------------------------------


@router.get("/slots", response=SlotPage, tags=["slots"])
def list_slots_endpoint(
    request,
    scenario_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    current_user = request.auth
    records, total = scenario_services.list_accessible_slots(
        current_user,
        scenario_id=scenario_id,
        limit=limit,
        offset=offset,
    )
    # Quirk: when filtering by scenario_id and the user is not a superuser,
    # an empty result must distinguish "no slots" from "scenario not
    # visible". Calling ``get_scenario_for_user`` surfaces the 404 in the
    # latter case (mirrors api/routers/catalog.py:148-149).
    if scenario_id is not None and total == 0 and not current_user.is_superuser:
        scenario_services.get_scenario_for_user(scenario_id, current_user)
    return {
        "items": [scenario_services.slot_summary(record) for record in records],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/slots", response={201: SlotOut}, tags=["slots"])
def create_slot_endpoint(request, payload: SlotIn):
    """Create a slot. Owner-only on the target scenario.

    The ``Idempotency-Key`` header is honored. The user_id used for the
    idempotency partition is the email -- same shape as the scenarios
    endpoint (Phase 4.2).
    """
    current_user = request.auth
    payload_dump = payload.model_dump()
    cached = get_idempotent_response(request, user_id=str(current_user.email), payload=payload_dump)
    if cached is not None:
        return 201, cached
    result = scenario_services.create_owned_slot(payload=payload, current_user=current_user)
    store_idempotent_response(
        request,
        user_id=str(current_user.email),
        payload=payload_dump,
        response=result,
        status_code=201,
    )
    return 201, result


@router.get("/slots/{slot_id}", response=SlotOut, tags=["slots"])
def get_slot_endpoint(request, slot_id: str):
    record = scenario_services.get_slot(slot_id)
    # Visibility is enforced via the slot's scenario (404 if no read access).
    scenario_services.get_scenario_for_user(record.scenario_id, request.auth)
    return scenario_services.slot_summary(record)


@router.patch("/slots/{slot_id}", response=SlotOut, tags=["slots"])
def update_slot_endpoint(request, slot_id: str, payload: SlotPatchIn):
    return scenario_services.update_owned_slot(
        slot_id=slot_id,
        payload=payload,
        current_user=request.auth,
    )


@router.delete("/slots/{slot_id}", response=DeletedOut, tags=["slots"])
def delete_slot_endpoint(request, slot_id: str):
    return scenario_services.delete_owned_slot(
        slot_id=slot_id,
        current_user=request.auth,
    )

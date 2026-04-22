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

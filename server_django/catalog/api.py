"""Ninja router for catalog endpoints (scenarios, slots, shares, steps).

Populated during migration phases 3 and 4. Each endpoint replaces the
corresponding FastAPI route in ``api/routers/catalog.py`` with 1:1
behavior.
"""

from __future__ import annotations

from typing import Any

from accounts.permissions import require_user_scope
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
    StepDeleteOut,
    StepIn,
    StepMutationOut,
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


# --------------------------------------------------------------------------
# Step collections (Phase 4.5). Six endpoints under
# /api/v1/users/{user_id}/scenarios/{scenario_id}/. Read endpoints accept
# owner OR shared visibility; mutations require owner. ``{user_id}`` accepts
# UUID or email via ``require_user_scope``.
#
# Quirks preserved verbatim:
#   * GET on a collection returns the RAW array (no envelope).
#   * GET on the parent returns ALL 5 collections in alphabetical order.
#   * ``insert_at`` is clamped to ``len(steps)``; negative values are rejected
#     by Query(ge=0) (-> 422).
# --------------------------------------------------------------------------


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections", tags=["steps"])
def list_step_collections_endpoint(request, user_id: str, scenario_id: str) -> dict[str, list[dict[str, Any]]]:
    current_user = request.auth
    require_user_scope(user_id, current_user)
    scenario = scenario_services.get_scenario_for_user(scenario_id, current_user)
    definition = scenario.definition or {}
    return {collection: scenario_services.step_collection_view(definition, collection) for collection in sorted(scenario_services.STEP_COLLECTIONS)}


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}", tags=["steps"])
def list_steps_endpoint(request, user_id: str, scenario_id: str, collection: str) -> list[dict[str, Any]]:
    current_user = request.auth
    require_user_scope(user_id, current_user)
    scenario_services.ensure_step_collection(collection)
    scenario = scenario_services.get_scenario_for_user(scenario_id, current_user)
    return scenario_services.step_collection_view(scenario.definition or {}, collection)


@router.get("/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}", tags=["steps"])
def get_step_endpoint(request, user_id: str, scenario_id: str, collection: str, index: int) -> dict[str, Any]:
    current_user = request.auth
    require_user_scope(user_id, current_user)
    scenario_services.ensure_step_collection(collection)
    scenario = scenario_services.get_scenario_for_user(scenario_id, current_user)
    return scenario_services.step_at(scenario_services.step_collection_view(scenario.definition or {}, collection), index)


@router.post(
    "/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}",
    response={201: StepMutationOut},
    tags=["steps"],
)
def create_step_endpoint(
    request,
    user_id: str,
    scenario_id: str,
    collection: str,
    payload: StepIn,
    insert_at: int | None = Query(default=None, ge=0),
):
    result = scenario_services.create_step(
        user_id=user_id,
        scenario_id=scenario_id,
        collection=collection,
        payload=payload,
        insert_at=insert_at,
        current_user=request.auth,
    )
    return 201, result


@router.put(
    "/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}",
    response=StepMutationOut,
    tags=["steps"],
)
def update_step_endpoint(
    request,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
    payload: StepIn,
):
    return scenario_services.update_step(
        user_id=user_id,
        scenario_id=scenario_id,
        collection=collection,
        index=index,
        payload=payload,
        current_user=request.auth,
    )


@router.delete(
    "/users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}",
    response=StepDeleteOut,
    tags=["steps"],
)
def delete_step_endpoint(
    request,
    user_id: str,
    scenario_id: str,
    collection: str,
    index: int,
):
    return scenario_services.delete_step(
        user_id=user_id,
        scenario_id=scenario_id,
        collection=collection,
        index=index,
        current_user=request.auth,
    )

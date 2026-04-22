"""Ninja router for operational endpoints: jobs, history, audit, settings,
artifacts, Graph subscriptions, monitoring.

Phase 6 lands the six jobs endpoints under ``/api/v1``; Phases 7 and 8
will extend this router with admin / monitoring / audit / settings /
Graph routes.

The ``/health`` readiness probe remains tagged ``runtime`` and
unauthenticated -- it's the only route hit before the auth layer boots.
"""

from __future__ import annotations

from accounts.permissions import require_user_scope
from foxrunner.idempotency import get_idempotent_response, store_idempotent_response
from ninja import Query, Router

from ops import services as ops_services
from ops.schemas import JobEventOut, JobOut, JobPage

router = Router()


@router.get("/health", auth=None, tags=["runtime"], summary="Readiness probe")
def health(request):
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Jobs (Phase 6). Six endpoints under /api/v1. Mirrors
# ``api/routers/jobs.py`` with sync Django-ORM plumbing.
#
# Quirks preserved verbatim:
#   * POST /users/{user_id}/scenarios/{scenario_id}/jobs returns 202 on
#     success (NOT 201); Idempotency-Key is partitioned on the current
#     user's email (mirrors FastAPI) to preserve replay semantics across
#     user_id path variations (UUID vs email alias).
#   * GET /jobs auto-scopes non-admins to their own ``user_id`` and
#     rejects with 403 when a conflicting ``user_id`` query is supplied.
#   * GET /jobs/{job_id}/events returns a RAW array -- no page envelope.
#     The FastAPI contract does the same so the frontend streams events
#     without reshaping.
#   * POST /jobs/{job_id}/cancel returns 409 when the job is not queued
#     or running (409 bubbled from ``mark_job_cancelled``).
#   * POST /jobs/{job_id}/retry returns 409 when the source job is not
#     ``kind="run_scenario"`` -- other kinds don't have a replayable
#     payload shape.
# --------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/scenarios/{scenario_id}/jobs",
    response={202: JobOut},
    tags=["jobs"],
)
def enqueue_user_scenario_endpoint(
    request,
    user_id: str,
    scenario_id: str,
    dry_run: bool = Query(default=True),
):
    """Enqueue a scenario run as a Celery job.

    The ``Idempotency-Key`` header is honored: a replay with the same
    payload returns the stored response, a replay with a different
    payload returns 409 (raised inside ``get_idempotent_response``).
    """
    current_user = request.auth
    require_user_scope(user_id, current_user)
    idem_payload = {"user_id": user_id, "scenario_id": scenario_id, "dry_run": dry_run}
    cached = get_idempotent_response(request, user_id=current_user.id, payload=idem_payload)
    if cached is not None:
        return 202, cached
    result = ops_services.enqueue_scenario_job(
        user_id_str=user_id,
        scenario_id=scenario_id,
        dry_run=dry_run,
        current_user=current_user,
    )
    store_idempotent_response(
        request,
        user_id=current_user.id,
        payload=idem_payload,
        response=result,
        status_code=202,
    )
    return 202, result


@router.get("/jobs", response=JobPage, tags=["jobs"])
def list_jobs_endpoint(
    request,
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    scenario_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    current_user = request.auth
    target_user = None
    if current_user.is_superuser:
        # Admin may supply any ``user_id`` (UUID or email) or omit it for
        # unscoped listing.
        if user_id is not None:
            from accounts.permissions import resolve_user

            target_user = resolve_user(user_id)
    else:
        # Non-admin: force the filter to ``current_user`` and reject
        # mismatched ``user_id`` with 403 (parity with FastAPI).
        if user_id is not None and user_id not in {str(current_user.id), current_user.email}:
            from ninja.errors import HttpError

            raise HttpError(403, "Acces jobs refuse.")
        target_user = current_user
    rows, total = ops_services.list_jobs(
        user=target_user,
        status_filter=status,
        scenario_id=scenario_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [ops_services.serialize_job(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}", response=JobOut, tags=["jobs"])
def get_job_endpoint(request, job_id: str, user_id: str = Query(...)):
    current_user = request.auth
    require_user_scope(user_id, current_user)
    from accounts.permissions import resolve_user

    target_user = resolve_user(user_id)
    record = ops_services.get_job_for_user(job_id, target_user, is_superuser=current_user.is_superuser)
    return ops_services.serialize_job(record)


@router.get("/jobs/{job_id}/events", response=list[JobEventOut], tags=["jobs"])
def get_job_events_endpoint(request, job_id: str, user_id: str = Query(...)):
    current_user = request.auth
    require_user_scope(user_id, current_user)
    from accounts.permissions import resolve_user

    target_user = resolve_user(user_id)
    # Reuse ``get_job_for_user`` to enforce the 404 / 403 gate before
    # returning the event list.
    ops_services.get_job_for_user(job_id, target_user, is_superuser=current_user.is_superuser)
    return [ops_services.serialize_job_event(event) for event in ops_services.list_job_events(job_id)]


@router.post("/jobs/{job_id}/cancel", response=JobOut, tags=["jobs"])
def cancel_job_endpoint(request, job_id: str, user_id: str = Query(...)):
    current_user = request.auth
    require_user_scope(user_id, current_user)
    return ops_services.cancel_job_for_user(
        job_id=job_id,
        user_id_str=user_id,
        current_user=current_user,
    )


@router.post("/jobs/{job_id}/retry", response={202: JobOut}, tags=["jobs"])
def retry_job_endpoint(request, job_id: str, user_id: str = Query(...)):
    current_user = request.auth
    require_user_scope(user_id, current_user)
    result = ops_services.retry_job_for_user(
        job_id=job_id,
        user_id_str=user_id,
        current_user=current_user,
    )
    return 202, result

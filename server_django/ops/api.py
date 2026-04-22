"""Ninja router for operational endpoints: jobs, history, audit, settings,
artifacts, Graph subscriptions, monitoring.

Populated in phases 5–8.

The scaffold provides a single ``/health`` endpoint so the NinjaAPI boots
and returns something useful before any other logic is in place.
"""

from __future__ import annotations

from ninja import Router

router = Router()


@router.get("/health", auth=None, tags=["runtime"], summary="Readiness probe")
def health(request):
    return {"status": "ok"}

"""Ninja router for Microsoft Graph endpoints (Phase 8).

Seven endpoints under ``/api/v1/graph/`` -- five superuser-only management
routes plus the two unauthenticated webhook callbacks Microsoft hits to
deliver subscription notifications:

    POST   /graph/subscriptions                  (superuser, 201)
    GET    /graph/subscriptions                  (superuser, paginated)
    PATCH  /graph/subscriptions/{subscription_id} (superuser, audit)
    DELETE /graph/subscriptions/{subscription_id} (superuser, audit)
    GET    /graph/notifications                  (superuser, paginated, filter)
    POST   /graph/webhook                        (auth=None, validation echo OR persist)
    POST   /graph/lifecycle                      (auth=None, lifecycle=True flag)

The webhook routes MUST stay unauthenticated -- Microsoft does not carry
a Bearer token. They authenticate themselves via the per-subscription
``clientState`` shared secret validated inside
:func:`ops.graph.save_graph_notifications`. The two routes also support
the Microsoft validation handshake: a query string ``?validationToken=X``
returns ``X`` as ``text/plain`` so Graph can confirm the endpoint is
reachable.
"""

from __future__ import annotations

from typing import Any

from accounts.permissions import require_superuser
from django.http import HttpResponse
from foxrunner.pagination import page_response
from ninja import Body, Query, Router

from ops import graph as graph_module
from ops import services as ops_services
from ops.schemas import (
    AcceptedOut,
    DeletedOut,
    GraphNotificationPage,
    GraphRenewIn,
    GraphSubscriptionIn,
    GraphSubscriptionOut,
    GraphSubscriptionPage,
)

router = Router(tags=["graph"])


# --------------------------------------------------------------------------
# Subscriptions (superuser)
# --------------------------------------------------------------------------


@router.post(
    "/graph/subscriptions",
    response={201: GraphSubscriptionOut},
    tags=["graph"],
)
def create_subscription_endpoint(request, payload: GraphSubscriptionIn):
    """POST /graph/subscriptions -- create on Graph + persist locally + audit."""
    require_superuser(request.auth)
    result = ops_services.create_graph_subscription_service(
        resource=payload.resource,
        change_type=payload.change_type,
        notification_url=payload.notification_url,
        expiration_datetime=payload.expiration_datetime,
        lifecycle_notification_url=payload.lifecycle_notification_url,
        current_user=request.auth,
    )
    return 201, result


@router.get("/graph/subscriptions", response=GraphSubscriptionPage, tags=["graph"])
def list_subscriptions_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """GET /graph/subscriptions -- paginated, superuser only."""
    require_superuser(request.auth)
    items, total = ops_services.list_graph_subscriptions(limit=limit, offset=offset)
    return page_response(items, total=total, limit=limit, offset=offset)


@router.patch(
    "/graph/subscriptions/{subscription_id}",
    response=GraphSubscriptionOut,
    tags=["graph"],
)
def renew_subscription_endpoint(request, subscription_id: str, payload: GraphRenewIn):
    """PATCH /graph/subscriptions/{subscription_id} -- renew on Graph + audit."""
    require_superuser(request.auth)
    return ops_services.renew_graph_subscription_service(
        subscription_id=subscription_id,
        expiration_datetime=payload.expiration_datetime,
        current_user=request.auth,
    )


@router.delete(
    "/graph/subscriptions/{subscription_id}",
    response=DeletedOut,
    tags=["graph"],
)
def delete_subscription_endpoint(request, subscription_id: str):
    """DELETE /graph/subscriptions/{subscription_id} -- delete on Graph + audit."""
    require_superuser(request.auth)
    return ops_services.delete_graph_subscription_service(
        subscription_id=subscription_id,
        current_user=request.auth,
    )


# --------------------------------------------------------------------------
# Notifications (superuser, read-only)
# --------------------------------------------------------------------------


@router.get("/graph/notifications", response=GraphNotificationPage, tags=["graph"])
def list_notifications_endpoint(
    request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    subscription_id: str | None = Query(default=None),
):
    """GET /graph/notifications -- paginated, optional ``subscription_id`` filter."""
    require_superuser(request.auth)
    items, total = ops_services.list_graph_notifications(
        limit=limit,
        offset=offset,
        subscription_id=subscription_id,
    )
    return page_response(items, total=total, limit=limit, offset=offset)


# --------------------------------------------------------------------------
# Webhooks (auth=None) -- Microsoft hits these without a Bearer token
# --------------------------------------------------------------------------


@router.post("/graph/webhook", auth=None, tags=["graph"])
def graph_webhook_endpoint(
    request,
    payload: dict[str, Any] | None = Body(default=None),  # noqa: B008
    validationToken: str | None = Query(default=None),
):
    """POST /graph/webhook -- Microsoft validation echo OR notification persist.

    When ``?validationToken=X`` is present we return ``X`` as ``text/plain``
    with status 200 (Microsoft's required handshake). Otherwise we persist
    the notification body and return ``{"accepted": <count>}``. The
    ``clientState`` validation lives inside
    :func:`ops.graph.save_graph_notifications`.
    """
    if validationToken is not None:
        return HttpResponse(validationToken, content_type="text/plain")
    count = graph_module.save_graph_notifications(payload or {}, lifecycle=False)
    return AcceptedOut(accepted=count)


@router.post("/graph/lifecycle", auth=None, tags=["graph"])
def graph_lifecycle_endpoint(
    request,
    payload: dict[str, Any] | None = Body(default=None),  # noqa: B008
    validationToken: str | None = Query(default=None),
):
    """POST /graph/lifecycle -- same as ``/graph/webhook`` but ``lifecycle=True``.

    Lifecycle deliveries carry a ``lifecycleEvent`` field (``reauthorizationRequired``,
    ``subscriptionRemoved``, ``missed``) that surfaces on the persisted
    :class:`ops.models.GraphNotification` row via the ``lifecycle_event``
    column.
    """
    if validationToken is not None:
        return HttpResponse(validationToken, content_type="text/plain")
    count = graph_module.save_graph_notifications(payload or {}, lifecycle=True)
    return AcceptedOut(accepted=count)

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User, current_active_user
from api.db import get_async_session
from api.dependencies import require_superuser
from api.graph import save_graph_notifications
from api.pagination import page_response
from api.schemas import (
    AcceptedPayload,
    DeletedPayload,
    GraphNotificationPagePayload,
    GraphRenewPayload,
    GraphSubscriptionPagePayload,
    GraphSubscriptionPayload,
    GraphSubscriptionResponsePayload,
)
from api.services.graph import create_subscription as create_graph_subscription_service
from api.services.graph import delete_subscription, list_notifications, list_subscriptions, renew_subscription

router = APIRouter(tags=["graph"])


@router.post("/graph/subscriptions", status_code=status.HTTP_201_CREATED, response_model=GraphSubscriptionResponsePayload)
async def create_subscription(
    payload: GraphSubscriptionPayload,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await create_graph_subscription_service(session, payload=payload)


@router.get("/graph/subscriptions", response_model=GraphSubscriptionPagePayload)
async def graph_subscriptions_endpoint(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    items, total = await list_subscriptions(session, limit=limit, offset=offset)
    return page_response(items, total=total, limit=limit, offset=offset)


@router.patch("/graph/subscriptions/{subscription_id}", response_model=GraphSubscriptionResponsePayload)
async def renew_subscription_endpoint(
    subscription_id: str,
    payload: GraphRenewPayload,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await renew_subscription(session, subscription_id=subscription_id, payload=payload, current_user=current_user)


@router.delete("/graph/subscriptions/{subscription_id}", response_model=DeletedPayload)
async def delete_subscription_endpoint(
    subscription_id: str,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await delete_subscription(session, subscription_id=subscription_id, current_user=current_user)


@router.get("/graph/notifications", response_model=GraphNotificationPagePayload)
async def graph_notifications_endpoint(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    subscription_id: str | None = None,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    items, total = await list_notifications(session, limit=limit, offset=offset, subscription_id=subscription_id)
    return page_response(items, total=total, limit=limit, offset=offset)


@router.post("/graph/webhook", response_model=AcceptedPayload)
async def graph_webhook(
    payload: dict[str, Any] | None = None,
    validation_token: str | None = Query(default=None, alias="validationToken"),
    session: AsyncSession = Depends(get_async_session),
):
    if validation_token is not None:
        return PlainTextResponse(validation_token)
    count = await save_graph_notifications(session, payload or {}, lifecycle=False)
    return {"accepted": count}


@router.post("/graph/lifecycle", response_model=AcceptedPayload)
async def graph_lifecycle_webhook(
    payload: dict[str, Any] | None = None,
    validation_token: str | None = Query(default=None, alias="validationToken"),
    session: AsyncSession = Depends(get_async_session),
):
    if validation_token is not None:
        return PlainTextResponse(validation_token)
    count = await save_graph_notifications(session, payload or {}, lifecycle=True)
    return {"accepted": count}

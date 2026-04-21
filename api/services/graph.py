from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import write_audit
from api.auth import User
from api.dependencies import actor_id
from api.graph import create_graph_subscription, delete_graph_subscription, renew_graph_subscription, save_graph_subscription, serialize_graph_subscription
from api.models import GraphNotificationRecord, GraphSubscriptionRecord
from api.routers.common import parse_graph_datetime, serialize_graph_notification
from api.schemas import GraphRenewPayload, GraphSubscriptionPayload
from api.time_utils import isoformat_utc


async def create_subscription(session: AsyncSession, *, payload: GraphSubscriptionPayload) -> dict[str, object]:
    subscription = await create_graph_subscription(
        resource=payload.resource,
        change_type=payload.change_type,
        notification_url=payload.notification_url,
        expiration_datetime=isoformat_utc(payload.expiration_datetime) or "",
        lifecycle_notification_url=payload.lifecycle_notification_url,
    )
    record = await save_graph_subscription(
        session,
        subscription=subscription,
        resource=payload.resource,
        change_type=payload.change_type,
        notification_url=payload.notification_url,
        lifecycle_notification_url=payload.lifecycle_notification_url,
    )
    return serialize_graph_subscription(record)


async def list_subscriptions(session: AsyncSession, *, limit: int, offset: int) -> tuple[list[dict[str, object]], int]:
    records = await session.scalars(select(GraphSubscriptionRecord).order_by(GraphSubscriptionRecord.id.desc()).offset(offset).limit(limit))
    total = await session.scalar(select(func.count(GraphSubscriptionRecord.id)))
    return [serialize_graph_subscription(record) for record in records], total or 0


async def renew_subscription(session: AsyncSession, *, subscription_id: str, payload: GraphRenewPayload, current_user: User) -> dict[str, object]:
    record = await _get_subscription(session, subscription_id)
    before = serialize_graph_subscription(record)
    expiration_datetime = isoformat_utc(payload.expiration_datetime) or ""
    raw = await renew_graph_subscription(subscription_id, expiration_datetime)
    record.expiration_datetime = parse_graph_datetime(raw.get("expirationDateTime", expiration_datetime))
    record.raw_payload = raw
    await session.commit()
    await session.refresh(record)
    result = serialize_graph_subscription(record)
    await write_audit(
        session, actor_user_id=actor_id(current_user), action="graph.subscription_renew", target_type="graph_subscription", target_id=subscription_id, before=before, after=result
    )
    return result


async def delete_subscription(session: AsyncSession, *, subscription_id: str, current_user: User) -> dict[str, object]:
    record = await _get_subscription(session, subscription_id)
    before = serialize_graph_subscription(record)
    await delete_graph_subscription(subscription_id)
    await session.delete(record)
    await session.commit()
    await write_audit(session, actor_user_id=actor_id(current_user), action="graph.subscription_delete", target_type="graph_subscription", target_id=subscription_id, before=before)
    return {"deleted": subscription_id}


async def list_notifications(session: AsyncSession, *, limit: int, offset: int, subscription_id: str | None = None) -> tuple[list[dict[str, object]], int]:
    query = select(GraphNotificationRecord).order_by(GraphNotificationRecord.id.desc()).offset(offset).limit(limit)
    total_query = select(func.count(GraphNotificationRecord.id))
    if subscription_id:
        query = query.where(GraphNotificationRecord.subscription_id == subscription_id)
        total_query = total_query.where(GraphNotificationRecord.subscription_id == subscription_id)
    total = await session.scalar(total_query)
    return [serialize_graph_notification(record) for record in await session.scalars(query)], total or 0


async def _get_subscription(session: AsyncSession, subscription_id: str) -> GraphSubscriptionRecord:
    from fastapi import HTTPException, status

    record = await session.scalar(select(GraphSubscriptionRecord).where(GraphSubscriptionRecord.subscription_id == subscription_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription Graph introuvable.")
    return record

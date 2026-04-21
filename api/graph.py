from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import GraphNotificationRecord, GraphSubscriptionRecord
from api.redaction import redact
from api.serializers import serialize_graph_subscription as serialize_graph_subscription
from api.time_utils import db_utc, parse_utc

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()

GRAPH_BASE_URL = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "")
GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "")
GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
GRAPH_MAIL_SENDER = os.getenv("GRAPH_MAIL_SENDER", "")
GRAPH_WEBHOOK_CLIENT_STATE = os.getenv("GRAPH_WEBHOOK_CLIENT_STATE", "")


def is_graph_configured() -> bool:
    return bool(GRAPH_TENANT_ID and GRAPH_CLIENT_ID and GRAPH_CLIENT_SECRET)


def get_graph_access_token() -> str:
    if not is_graph_configured():
        raise RuntimeError("Configuration Microsoft Graph incomplete.")
    response = httpx.post(
        f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": GRAPH_CLIENT_ID,
            "client_secret": GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def get_graph_access_token_async() -> str:
    if not is_graph_configured():
        raise RuntimeError("Configuration Microsoft Graph incomplete.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{GRAPH_TENANT_ID}/oauth2/v2.0/token",
            data={
                "client_id": GRAPH_CLIENT_ID,
                "client_secret": GRAPH_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
    response.raise_for_status()
    return response.json()["access_token"]


def send_graph_mail(*, to: str, subject: str, body: str, sender: str | None = None) -> None:
    sender_address = sender or GRAPH_MAIL_SENDER
    if not sender_address:
        raise RuntimeError("GRAPH_MAIL_SENDER doit etre configure.")
    token = get_graph_access_token()
    response = httpx.post(
        f"{GRAPH_BASE_URL}/users/{sender_address}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            },
            "saveToSentItems": True,
        },
        timeout=20,
    )
    response.raise_for_status()


async def create_graph_subscription(
    *,
    resource: str,
    change_type: str,
    notification_url: str,
    expiration_datetime: str,
    lifecycle_notification_url: str | None = None,
    client_state: str | None = None,
) -> dict[str, Any]:
    token = await get_graph_access_token_async()
    payload: dict[str, Any] = {
        "changeType": change_type,
        "notificationUrl": notification_url,
        "resource": resource,
        "expirationDateTime": expiration_datetime,
        "clientState": client_state or GRAPH_WEBHOOK_CLIENT_STATE,
    }
    if lifecycle_notification_url:
        payload["lifecycleNotificationUrl"] = lifecycle_notification_url
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{GRAPH_BASE_URL}/subscriptions", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload)
    response.raise_for_status()
    return response.json()


async def renew_graph_subscription(subscription_id: str, expiration_datetime: str) -> dict[str, Any]:
    token = await get_graph_access_token_async()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.patch(
            f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"expirationDateTime": expiration_datetime},
        )
    response.raise_for_status()
    return response.json()


async def delete_graph_subscription(subscription_id: str) -> None:
    token = await get_graph_access_token_async()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.delete(f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}", headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()


async def save_graph_subscription(
    session: AsyncSession,
    *,
    subscription: dict[str, Any],
    resource: str,
    change_type: str,
    notification_url: str,
    lifecycle_notification_url: str | None,
) -> GraphSubscriptionRecord:
    subscription_id = subscription["id"]
    record = await session.scalar(select(GraphSubscriptionRecord).where(GraphSubscriptionRecord.subscription_id == subscription_id))
    if record is None:
        record = GraphSubscriptionRecord(subscription_id=subscription_id)
        session.add(record)
    record.resource = resource
    record.change_type = change_type
    record.notification_url = notification_url
    record.lifecycle_notification_url = lifecycle_notification_url
    record.client_state = subscription.get("clientState", GRAPH_WEBHOOK_CLIENT_STATE)
    raw_expiration = subscription.get("expirationDateTime")
    record.expiration_datetime = _parse_graph_datetime(raw_expiration) if raw_expiration else None
    record.raw_payload = redact(subscription)
    await session.commit()
    await session.refresh(record)
    return record


async def save_graph_notifications(session: AsyncSession, payload: dict[str, Any], *, lifecycle: bool = False) -> int:
    notifications = payload.get("value", [])
    if not isinstance(notifications, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload Graph invalide.")
    count = 0
    require_known = await _requires_known_subscription()
    app_env_prod = os.getenv("APP_ENV", "development").lower() in {"production", "prod"}
    if app_env_prod and not GRAPH_WEBHOOK_CLIENT_STATE:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GRAPH_WEBHOOK_CLIENT_STATE doit etre configure en production.")
    for item in notifications:
        if not isinstance(item, dict):
            continue
        subscription_id = str(item.get("subscriptionId", ""))
        change_type = str(item.get("changeType", item.get("lifecycleEvent", "")))
        resource = str(item.get("resource", ""))
        lifecycle_event = item.get("lifecycleEvent") if lifecycle else None
        client_state = item.get("clientState")
        await _validate_client_state(
            session,
            subscription_id=subscription_id,
            received_state=client_state,
            require_known_subscription=require_known,
        )
        if require_known and not await _subscription_exists(session, subscription_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription Graph inconnue.")
        existing = await session.scalar(
            select(GraphNotificationRecord.id).where(
                GraphNotificationRecord.subscription_id == subscription_id,
                GraphNotificationRecord.change_type == change_type,
                GraphNotificationRecord.resource == resource,
                GraphNotificationRecord.lifecycle_event == lifecycle_event,
            )
        )
        if existing is not None:
            continue
        session.add(
            GraphNotificationRecord(
                subscription_id=subscription_id,
                change_type=change_type,
                resource=resource,
                tenant_id=item.get("tenantId"),
                client_state=client_state,
                lifecycle_event=lifecycle_event,
                raw_payload=redact(item),
            )
        )
        count += 1
    await session.commit()
    return count


async def _validate_client_state(
    session: AsyncSession,
    *,
    subscription_id: str,
    received_state: Any,
    require_known_subscription: bool,
) -> None:
    # Microsoft Graph authenticates webhook deliveries via the per-subscription
    # clientState shared secret. Accept either the value saved at subscription
    # time OR the current global default — this allows rotating the global
    # without invalidating already-registered subscriptions, and vice versa.
    expected_states: set[str] = set()
    if subscription_id:
        record_state = await session.scalar(
            select(GraphSubscriptionRecord.client_state).where(GraphSubscriptionRecord.subscription_id == subscription_id)
        )
        if record_state:
            expected_states.add(record_state)
    if GRAPH_WEBHOOK_CLIENT_STATE:
        expected_states.add(GRAPH_WEBHOOK_CLIENT_STATE)
    if not expected_states:
        # No expected value anywhere. Require the subscription to exist in prod
        # so unauthenticated webhook deliveries cannot persist anything.
        if require_known_subscription:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="clientState Graph absent.")
        return
    if not isinstance(received_state, str) or received_state not in expected_states:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="clientState Graph invalide.")


async def _requires_known_subscription() -> bool:
    raw = os.getenv("GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION")
    if raw is not None:
        return raw.lower() == "true"
    return os.getenv("APP_ENV", "development").lower() in {"production", "prod"}


async def _subscription_exists(session: AsyncSession, subscription_id: str) -> bool:
    if not subscription_id:
        return False
    return bool(await session.scalar(select(GraphSubscriptionRecord.id).where(GraphSubscriptionRecord.subscription_id == subscription_id)))


def _parse_graph_datetime(value: str):
    return db_utc(parse_utc(value))

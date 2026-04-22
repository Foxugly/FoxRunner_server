"""Microsoft Graph low-level client + persistence helpers.

Sync port of ``api/graph.py``. Django views run synchronously so the
``httpx.AsyncClient`` blocks of the FastAPI version collapse into plain
``httpx.Client`` calls -- no ``asyncio.run()`` wrappers needed.

The clientState validation logic in :func:`_validate_client_state` is a
byte-for-byte port of the FastAPI helper -- accepts either the
per-subscription value saved at registration time OR the global
``GRAPH_WEBHOOK_CLIENT_STATE`` env var, so a global rotation does not
invalidate already-registered subscriptions and vice versa. In
production (or whenever ``GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION=true``)
the global env var MUST be configured -- :func:`save_graph_notifications`
raises 503 BEFORE iterating notifications when it is empty.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from ninja.errors import HttpError

from api.redaction import redact  # reused until phase 13 swap
from ops.models import GraphNotification, GraphSubscription

GRAPH_BASE_URL = os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")


def _env_tenant() -> str:
    return os.getenv("GRAPH_TENANT_ID", "")


def _env_client_id() -> str:
    return os.getenv("GRAPH_CLIENT_ID", "")


def _env_client_secret() -> str:
    return os.getenv("GRAPH_CLIENT_SECRET", "")


def _env_mail_sender() -> str:
    return os.getenv("GRAPH_MAIL_SENDER", "")


def _env_global_client_state() -> str:
    return os.getenv("GRAPH_WEBHOOK_CLIENT_STATE", "")


def is_graph_configured() -> bool:
    """Return True when all three Graph credentials env vars are set."""
    return bool(_env_tenant() and _env_client_id() and _env_client_secret())


def get_graph_access_token() -> str:
    """Fetch a client-credentials access token from Azure AD (sync)."""
    if not is_graph_configured():
        raise RuntimeError("Configuration Microsoft Graph incomplete.")
    response = httpx.post(
        f"https://login.microsoftonline.com/{_env_tenant()}/oauth2/v2.0/token",
        data={
            "client_id": _env_client_id(),
            "client_secret": _env_client_secret(),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def send_graph_mail(*, to: str, subject: str, body: str, sender: str | None = None) -> None:
    """Send a single mail via Graph (sync). Mirrors ``api/graph.py::send_graph_mail``."""
    sender_address = sender or _env_mail_sender()
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


def create_graph_subscription(
    *,
    resource: str,
    change_type: str,
    notification_url: str,
    expiration_datetime: str,
    lifecycle_notification_url: str | None = None,
    client_state: str | None = None,
) -> dict[str, Any]:
    """POST /subscriptions on Microsoft Graph. Returns the raw JSON body."""
    token = get_graph_access_token()
    payload: dict[str, Any] = {
        "changeType": change_type,
        "notificationUrl": notification_url,
        "resource": resource,
        "expirationDateTime": expiration_datetime,
        "clientState": client_state or _env_global_client_state(),
    }
    if lifecycle_notification_url:
        payload["lifecycleNotificationUrl"] = lifecycle_notification_url
    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"{GRAPH_BASE_URL}/subscriptions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


def renew_graph_subscription(subscription_id: str, expiration_datetime: str) -> dict[str, Any]:
    """PATCH /subscriptions/{id} with a new expirationDateTime."""
    token = get_graph_access_token()
    with httpx.Client(timeout=20) as client:
        response = client.patch(
            f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"expirationDateTime": expiration_datetime},
        )
    response.raise_for_status()
    return response.json()


def delete_graph_subscription(subscription_id: str) -> None:
    """DELETE /subscriptions/{id} -- no body returned."""
    token = get_graph_access_token()
    with httpx.Client(timeout=20) as client:
        response = client.delete(
            f"{GRAPH_BASE_URL}/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    response.raise_for_status()


# --------------------------------------------------------------------------
# Persistence helpers
# --------------------------------------------------------------------------


def _parse_graph_datetime(value: str | None) -> datetime | None:
    """Parse Microsoft's ISO 8601 timestamp into a naive UTC datetime.

    Mirrors ``api/routers/common.py::parse_graph_datetime`` -- the DB
    stores naive datetimes (UTC by convention).
    """
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _utc_iso(value: datetime | None) -> str | None:
    """Serialise as ISO 8601 UTC with ``Z`` suffix. Mirrors ``api/time_utils.py::isoformat_utc``."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def save_graph_subscription(
    *,
    subscription: dict[str, Any],
    resource: str,
    change_type: str,
    notification_url: str,
    lifecycle_notification_url: str | None,
) -> GraphSubscription:
    """Upsert a :class:`GraphSubscription` row from the Graph response.

    ``client_state`` is taken from the response when present (Graph echoes
    the value back); otherwise we fall back to the current global env var
    so the dedupe + validation logic still has a value to compare against.
    """
    subscription_id = subscription["id"]
    record = GraphSubscription.objects.filter(subscription_id=subscription_id).first()
    if record is None:
        record = GraphSubscription(subscription_id=subscription_id)
    record.resource = resource
    record.change_type = change_type
    record.notification_url = notification_url
    record.lifecycle_notification_url = lifecycle_notification_url
    record.client_state = subscription.get("clientState", _env_global_client_state())
    raw_expiration = subscription.get("expirationDateTime")
    record.expiration_datetime = _parse_graph_datetime(raw_expiration) if raw_expiration else None
    record.raw_payload = redact(subscription)
    record.save()
    record.refresh_from_db()
    return record


def save_graph_notifications(payload: dict[str, Any], *, lifecycle: bool = False) -> int:
    """Persist a Microsoft webhook delivery. Returns the count of NEWLY inserted rows.

    Notifications are deduplicated by the
    ``(subscription_id, change_type, resource, lifecycle_event)`` tuple
    -- matches the ``uq_graph_notification_dedupe`` unique constraint
    declared on :class:`GraphNotification`. Existing rows are skipped.

    Production-only enforcement (BEFORE any iteration): the global
    ``GRAPH_WEBHOOK_CLIENT_STATE`` env var MUST be set, otherwise the
    helper raises 503 -- it would otherwise be impossible to authenticate
    deliveries that don't carry a per-subscription value.
    """
    notifications = payload.get("value", [])
    if not isinstance(notifications, list):
        raise HttpError(400, "Payload Graph invalide.")
    require_known = _requires_known_subscription()
    app_env_prod = os.getenv("APP_ENV", "development").lower() in {"production", "prod"}
    if app_env_prod and not _env_global_client_state():
        raise HttpError(503, "GRAPH_WEBHOOK_CLIENT_STATE doit etre configure en production.")
    count = 0
    for item in notifications:
        if not isinstance(item, dict):
            continue
        subscription_id = str(item.get("subscriptionId", ""))
        change_type = str(item.get("changeType", item.get("lifecycleEvent", "")))
        resource = str(item.get("resource", ""))
        lifecycle_event = item.get("lifecycleEvent") if lifecycle else None
        client_state = item.get("clientState")
        _validate_client_state(
            subscription_id=subscription_id,
            received_state=client_state,
            require_known_subscription=require_known,
        )
        if require_known and not _subscription_exists(subscription_id):
            raise HttpError(404, "Subscription Graph inconnue.")
        existing = GraphNotification.objects.filter(
            subscription_id=subscription_id,
            change_type=change_type,
            resource=resource,
            lifecycle_event=lifecycle_event,
        ).exists()
        if existing:
            continue
        GraphNotification.objects.create(
            subscription_id=subscription_id,
            change_type=change_type,
            resource=resource,
            tenant_id=item.get("tenantId"),
            client_state=client_state,
            lifecycle_event=lifecycle_event,
            raw_payload=redact(item),
        )
        count += 1
    return count


def _validate_client_state(
    *,
    subscription_id: str,
    received_state: Any,
    require_known_subscription: bool,
) -> None:
    """Validate the webhook ``clientState`` shared secret.

    Byte-for-byte port of ``api/graph.py::_validate_client_state``:

    1. Build ``expected_states`` from the per-subscription value AND the
       global env var -- rotating the global without invalidating
       already-registered subscriptions, and vice versa.
    2. Both empty -> 403 in production (or when REQUIRE_SUBSCRIPTION=true);
       pass-through otherwise (dev mode without webhook secret).
    3. Neither empty -> received MUST be a string AND match one of the
       expected values; 403 otherwise.
    """
    expected_states: set[str] = set()
    if subscription_id:
        record_state = GraphSubscription.objects.filter(subscription_id=subscription_id).values_list("client_state", flat=True).first()
        if record_state:
            expected_states.add(record_state)
    global_state = _env_global_client_state()
    if global_state:
        expected_states.add(global_state)
    if not expected_states:
        if require_known_subscription:
            raise HttpError(403, "clientState Graph absent.")
        return
    if not isinstance(received_state, str) or received_state not in expected_states:
        raise HttpError(403, "clientState Graph invalide.")


def _requires_known_subscription() -> bool:
    """Return True when unauthenticated webhook deliveries must hit a known subscription_id.

    Reads ``GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION`` first (``"true"`` -> True,
    anything else -> False); falls back to ``APP_ENV in {"production",
    "prod"}`` when unset.
    """
    raw = os.getenv("GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION")
    if raw is not None:
        return raw.lower() == "true"
    return os.getenv("APP_ENV", "development").lower() in {"production", "prod"}


def _subscription_exists(subscription_id: str) -> bool:
    """Return True when a :class:`GraphSubscription` row matches ``subscription_id``."""
    if not subscription_id:
        return False
    return GraphSubscription.objects.filter(subscription_id=subscription_id).exists()


# --------------------------------------------------------------------------
# Serializers (mirrors ``api/serializers.py::serialize_graph_*``)
# --------------------------------------------------------------------------


def serialize_graph_subscription(record: GraphSubscription) -> dict[str, Any]:
    return {
        "subscription_id": record.subscription_id,
        "resource": record.resource,
        "change_type": record.change_type,
        "notification_url": record.notification_url,
        "lifecycle_notification_url": record.lifecycle_notification_url,
        "expiration_datetime": _utc_iso(record.expiration_datetime),
        "created_at": _utc_iso(record.created_at),
        "updated_at": _utc_iso(record.updated_at),
    }


def serialize_graph_notification(record: GraphNotification) -> dict[str, Any]:
    return {
        "id": record.id,
        "subscription_id": record.subscription_id,
        "change_type": record.change_type,
        "resource": record.resource,
        "tenant_id": record.tenant_id,
        "client_state": record.client_state,
        "lifecycle_event": record.lifecycle_event,
        "raw_payload": record.raw_payload or {},
        "created_at": _utc_iso(record.created_at),
    }

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from api.models import GraphNotificationRecord, GraphSubscriptionRecord
from api.schemas import GraphRenewPayload, GraphSubscriptionPayload
from api.services.graph import create_subscription, delete_subscription, list_notifications, list_subscriptions, renew_subscription
from tests.helpers import fake_user, temp_service_db


class GraphServiceTests(unittest.TestCase):
    def test_create_subscription_persists_redacted_payload(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    with patch(
                        "api.services.graph.create_graph_subscription",
                        new=AsyncMock(return_value={"id": "sub1", "expirationDateTime": "2026-04-23T10:00:00Z", "clientState": "secret"}),
                    ):
                        result = await create_subscription(
                            session,
                            payload=GraphSubscriptionPayload(
                                resource="users/a/messages",
                                change_type="created",
                                notification_url="https://example.com/webhook",
                                expiration_datetime="2026-04-23T10:00:00Z",
                            ),
                        )
                    record = await session.get(GraphSubscriptionRecord, 1)
                    return result, record

            result, record = asyncio.run(run())

        self.assertEqual(result["subscription_id"], "sub1")
        self.assertEqual(record.raw_payload["clientState"], "***redacted***")

    def test_renew_and_delete_subscription_write_audit(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    session.add(GraphSubscriptionRecord(subscription_id="sub1", resource="users/a/messages", change_type="created", notification_url="https://example.com/webhook"))
                    await session.commit()
                    with patch("api.services.graph.renew_graph_subscription", new=AsyncMock(return_value={"expirationDateTime": "2026-04-24T10:00:00Z"})):
                        renewed = await renew_subscription(
                            session,
                            subscription_id="sub1",
                            payload=GraphRenewPayload(expiration_datetime="2026-04-24T10:00:00Z"),
                            current_user=fake_user("admin", superuser=True),
                        )
                    with patch("api.services.graph.delete_graph_subscription", new=AsyncMock(return_value=None)):
                        deleted = await delete_subscription(session, subscription_id="sub1", current_user=fake_user("admin", superuser=True))
                    return renewed, deleted

            renewed, deleted = asyncio.run(run())

        self.assertEqual(renewed["expiration_datetime"], "2026-04-24T10:00:00Z")
        self.assertEqual(deleted, {"deleted": "sub1"})

    def test_list_subscriptions_and_notifications(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    session.add(GraphSubscriptionRecord(subscription_id="sub1", resource="users/a/messages", change_type="created", notification_url="https://example.com/webhook"))
                    session.add(GraphNotificationRecord(subscription_id="sub1", change_type="created", resource="users/a/messages/1"))
                    await session.commit()
                    subscriptions = await list_subscriptions(session, limit=10, offset=0)
                    notifications = await list_notifications(session, limit=10, offset=0, subscription_id="sub1")
                    return subscriptions, notifications

            subscriptions, notifications = asyncio.run(run())

        self.assertEqual(subscriptions[1], 1)
        self.assertEqual(notifications[1], 1)


if __name__ == "__main__":
    unittest.main()

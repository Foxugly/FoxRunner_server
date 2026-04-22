"""Integration tests for the Phase 8 Microsoft Graph endpoints.

Seven endpoints under ``/api/v1/graph/`` -- five superuser-only
management routes plus the two unauthenticated webhook callbacks. The
``httpx.Client`` HTTP calls are mocked at the ``ops.graph`` boundary
(via ``unittest.mock.patch``) so the suite never touches Microsoft.

The clientState validation chain is exercised end-to-end here -- it is
the critical security boundary for the unauthenticated webhook routes
(see ``api/graph.py::_validate_client_state`` for the reference).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest import mock

from accounts.models import User
from django.test import Client, TestCase

from ops.models import AuditEntry, GraphNotification, GraphSubscription


def _login(client: Client, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/jwt/login",
        data=f"username={email}&password={password}",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class _BaseGraphApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")


# --------------------------------------------------------------------------
# CREATE / LIST / PATCH / DELETE -- superuser-only
# --------------------------------------------------------------------------


class CreateSubscriptionTest(_BaseGraphApiTest):
    def test_create_subscription_calls_graph_and_persists(self):
        future = datetime.now(UTC) + timedelta(hours=2)
        fake_response = {
            "id": "sub-001",
            "resource": "/me/messages",
            "changeType": "created,updated",
            "notificationUrl": "https://callbacks.example/webhook",
            "expirationDateTime": future.isoformat().replace("+00:00", "Z"),
            "clientState": "abc",
        }
        with mock.patch("ops.graph.create_graph_subscription", return_value=fake_response) as mocked:
            response = self.client.post(
                "/api/v1/graph/subscriptions",
                data=json.dumps(
                    {
                        "resource": "/me/messages",
                        "change_type": "created,updated",
                        "notification_url": "https://callbacks.example/webhook",
                        "expiration_datetime": future.isoformat().replace("+00:00", "Z"),
                    }
                ),
                content_type="application/json",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["subscription_id"], "sub-001")
        self.assertTrue(GraphSubscription.objects.filter(subscription_id="sub-001").exists())
        # Audit row written
        self.assertTrue(
            AuditEntry.objects.filter(
                action="graph.subscription_create",
                target_id="sub-001",
            ).exists()
        )
        # Forwarded with normalised UTC string
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["resource"], "/me/messages")
        self.assertTrue(kwargs["expiration_datetime"].endswith("Z"))

    def test_create_subscription_requires_superuser(self):
        future = datetime.now(UTC) + timedelta(hours=2)
        response = self.client.post(
            "/api/v1/graph/subscriptions",
            data=json.dumps(
                {
                    "resource": "/me/messages",
                    "change_type": "created",
                    "notification_url": "https://callbacks.example/webhook",
                    "expiration_datetime": future.isoformat().replace("+00:00", "Z"),
                }
            ),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class ListSubscriptionsTest(_BaseGraphApiTest):
    def test_list_subscriptions_paginates(self):
        for i in range(5):
            GraphSubscription.objects.create(
                subscription_id=f"sub-{i:03d}",
                resource=f"/me/folder-{i}",
                change_type="created",
            )
        response = self.client.get(
            "/api/v1/graph/subscriptions?limit=2&offset=0",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(len(body["items"]), 2)
        # Ordering: descending id -> last-inserted first
        self.assertEqual(body["items"][0]["subscription_id"], "sub-004")

        page2 = self.client.get(
            "/api/v1/graph/subscriptions?limit=2&offset=4",
            **_auth(self.admin_token),
        )
        self.assertEqual(page2.json()["items"][0]["subscription_id"], "sub-000")


class RenewSubscriptionTest(_BaseGraphApiTest):
    def test_renew_subscription_audit(self):
        GraphSubscription.objects.create(
            subscription_id="sub-renew",
            resource="/me/messages",
            change_type="created",
            notification_url="https://callbacks.example/webhook",
            client_state="abc",
            expiration_datetime=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        new_expiration = datetime.now(UTC) + timedelta(hours=12)
        new_iso = new_expiration.isoformat().replace("+00:00", "Z")
        with mock.patch(
            "ops.graph.renew_graph_subscription",
            return_value={"id": "sub-renew", "expirationDateTime": new_iso},
        ) as mocked:
            response = self.client.patch(
                "/api/v1/graph/subscriptions/sub-renew",
                data=json.dumps({"expiration_datetime": new_iso}),
                content_type="application/json",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        mocked.assert_called_once()
        self.assertTrue(
            AuditEntry.objects.filter(
                action="graph.subscription_renew",
                target_id="sub-renew",
            ).exists()
        )

    def test_renew_subscription_404_when_unknown(self):
        new_iso = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        # No HTTP call should be issued for an unknown subscription -- guard
        # the boundary so we don't accidentally hit Microsoft in CI.
        with mock.patch("ops.graph.renew_graph_subscription") as mocked:
            response = self.client.patch(
                "/api/v1/graph/subscriptions/nope",
                data=json.dumps({"expiration_datetime": new_iso}),
                content_type="application/json",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 404, response.content)
        mocked.assert_not_called()


class DeleteSubscriptionTest(_BaseGraphApiTest):
    def test_delete_subscription_audit(self):
        GraphSubscription.objects.create(
            subscription_id="sub-del",
            resource="/me/messages",
            change_type="created",
            client_state="abc",
        )
        with mock.patch("ops.graph.delete_graph_subscription") as mocked:
            response = self.client.delete(
                "/api/v1/graph/subscriptions/sub-del",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"deleted": "sub-del"})
        mocked.assert_called_once_with("sub-del")
        self.assertFalse(GraphSubscription.objects.filter(subscription_id="sub-del").exists())
        self.assertTrue(
            AuditEntry.objects.filter(
                action="graph.subscription_delete",
                target_id="sub-del",
            ).exists()
        )


# --------------------------------------------------------------------------
# Notifications listing
# --------------------------------------------------------------------------


class ListNotificationsTest(_BaseGraphApiTest):
    def test_list_notifications_filter_by_subscription_id(self):
        GraphNotification.objects.create(
            subscription_id="sub-1",
            change_type="created",
            resource="/me/messages/1",
        )
        GraphNotification.objects.create(
            subscription_id="sub-1",
            change_type="updated",
            resource="/me/messages/2",
        )
        GraphNotification.objects.create(
            subscription_id="sub-2",
            change_type="created",
            resource="/me/messages/3",
        )
        response = self.client.get(
            "/api/v1/graph/notifications?subscription_id=sub-1",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual({item["subscription_id"] for item in body["items"]}, {"sub-1"})


# --------------------------------------------------------------------------
# Webhook -- validation echo + persist + dedupe + clientState
# --------------------------------------------------------------------------


class WebhookValidationTokenTest(_BaseGraphApiTest):
    def test_webhook_validation_token_returns_plain_text(self):
        response = self.client.post(
            "/api/v1/graph/webhook?validationToken=abc",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content.decode(), "abc")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))


@mock.patch.dict(
    "os.environ",
    {"GRAPH_WEBHOOK_CLIENT_STATE": "global-secret", "APP_ENV": "development"},
    clear=False,
)
class WebhookPersistTest(_BaseGraphApiTest):
    def test_webhook_persists_notification(self):
        GraphSubscription.objects.create(
            subscription_id="sub-1",
            resource="/me/messages",
            change_type="created",
            client_state="global-secret",
        )
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "resource": "/me/messages/AAA",
                    "tenantId": "tenant-1",
                    "clientState": "global-secret",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"accepted": 1})
        self.assertEqual(GraphNotification.objects.count(), 1)

    def test_webhook_dedupe(self):
        GraphSubscription.objects.create(
            subscription_id="sub-1",
            resource="/me/messages",
            change_type="created",
            client_state="global-secret",
        )
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "resource": "/me/messages/BBB",
                    "clientState": "global-secret",
                }
            ]
        }
        for _ in range(2):
            response = self.client.post(
                "/api/v1/graph/webhook",
                data=json.dumps(body),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(GraphNotification.objects.count(), 1)

    def test_webhook_invalid_client_state_returns_403(self):
        GraphSubscription.objects.create(
            subscription_id="sub-1",
            resource="/me/messages",
            change_type="created",
            client_state="global-secret",
        )
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "resource": "/me/messages/CCC",
                    "clientState": "wrong",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(GraphNotification.objects.count(), 0)

    def test_webhook_value_field_must_be_list(self):
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps({"value": "not-a-list"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)


@mock.patch.dict(
    "os.environ",
    {
        "APP_ENV": "production",
        "GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION": "true",
        "GRAPH_WEBHOOK_CLIENT_STATE": "prod-secret",
    },
    clear=False,
)
class WebhookProductionTest(_BaseGraphApiTest):
    def test_webhook_missing_client_state_in_prod_returns_403(self):
        # No subscription_id -> no per-sub state. ``clientState`` not provided
        # but the global env var IS set -> _validate_client_state checks
        # received_state against the {global} expected_states set. A missing
        # clientState fails the isinstance(str) check and yields 403
        # ("clientState Graph invalide.").
        body = {
            "value": [
                {
                    "subscriptionId": "sub-unknown",
                    "changeType": "created",
                    "resource": "/me/messages/DDD",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_webhook_unknown_subscription_in_prod_returns_404(self):
        body = {
            "value": [
                {
                    "subscriptionId": "sub-unknown",
                    "changeType": "created",
                    "resource": "/me/messages/EEE",
                    "clientState": "prod-secret",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404, response.content)


@mock.patch.dict(
    "os.environ",
    {
        "APP_ENV": "production",
        "GRAPH_WEBHOOK_CLIENT_STATE": "",
    },
    clear=False,
)
class WebhookProdMissingGlobalSecretTest(_BaseGraphApiTest):
    def test_webhook_missing_global_secret_in_prod_returns_503(self):
        body = {
            "value": [
                {
                    "subscriptionId": "sub-x",
                    "changeType": "created",
                    "resource": "/me/messages/FFF",
                    "clientState": "anything",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503, response.content)


# --------------------------------------------------------------------------
# clientState parity edge cases (per-sub vs global precedence)
# --------------------------------------------------------------------------


@mock.patch.dict(
    "os.environ",
    {"GRAPH_WEBHOOK_CLIENT_STATE": "xyz", "APP_ENV": "development"},
    clear=False,
)
class WebhookPerSubscriptionPrecedenceTest(_BaseGraphApiTest):
    def test_per_subscription_client_state_acceptance(self):
        # Per-sub secret is "abc", global is "xyz". A delivery with "abc"
        # MUST be accepted because either value is acceptable -- this is
        # the rotation-friendly behaviour from api/graph.py.
        GraphSubscription.objects.create(
            subscription_id="sub-1",
            resource="/me/messages",
            change_type="created",
            client_state="abc",
        )
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "resource": "/me/messages/GGG",
                    "clientState": "abc",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(GraphNotification.objects.count(), 1)


@mock.patch.dict(
    "os.environ",
    {"GRAPH_WEBHOOK_CLIENT_STATE": "global-only", "APP_ENV": "development"},
    clear=False,
)
class WebhookGlobalFallbackTest(_BaseGraphApiTest):
    def test_global_fallback_when_per_subscription_empty(self):
        GraphSubscription.objects.create(
            subscription_id="sub-1",
            resource="/me/messages",
            change_type="created",
            client_state=None,  # no per-sub secret
        )
        body = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "changeType": "created",
                    "resource": "/me/messages/HHH",
                    "clientState": "global-only",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/webhook",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(GraphNotification.objects.count(), 1)


# --------------------------------------------------------------------------
# Lifecycle endpoint
# --------------------------------------------------------------------------


@mock.patch.dict(
    "os.environ",
    {"GRAPH_WEBHOOK_CLIENT_STATE": "lc-secret", "APP_ENV": "development"},
    clear=False,
)
class LifecycleEndpointTest(_BaseGraphApiTest):
    def test_lifecycle_endpoint_uses_lifecycle_flag(self):
        body = {
            "value": [
                {
                    "subscriptionId": "sub-lc",
                    "lifecycleEvent": "reauthorizationRequired",
                    "resource": "/me/messages",
                    "clientState": "lc-secret",
                }
            ]
        }
        response = self.client.post(
            "/api/v1/graph/lifecycle",
            data=json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"accepted": 1})
        record = GraphNotification.objects.get(subscription_id="sub-lc")
        self.assertEqual(record.lifecycle_event, "reauthorizationRequired")

    def test_lifecycle_validation_token_echo(self):
        response = self.client.post(
            "/api/v1/graph/lifecycle?validationToken=lc-token",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.content.decode(), "lc-token")
        self.assertTrue(response["Content-Type"].startswith("text/plain"))

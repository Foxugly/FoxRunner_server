"""Tests for the per-user PushIT targets CRUD (``accounts.pushit_api``)
and the owner-config bridge (``accounts.services.load_pushit_config_for_owner``).

The config is per-user and never shared: a user only ever sees their own
targets, and the scheduler resolves a scenario owner's config from the DB.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase

from accounts.models import PushItTarget, User
from accounts.services import load_pushit_config_for_owner


class _AuthMixin:
    def login(self, client: Client, email: str, password: str) -> str:
        response = client.post(
            "/api/v1/auth/jwt/login",
            data=f"username={email}&password={password}",
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 200, response.content
        return response.json()["access_token"]

    def auth(self, token: str) -> dict[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class PushItTargetCrudTest(_AuthMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.alice_token = self.login(self.client, "alice@example.com", "password123!")
        self.bob_token = self.login(self.client, "bob@example.com", "password123!")

    def test_create_and_list_scoped_to_owner(self):
        response = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "phone", "app_token": "apt_alice"},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["name"], "phone")
        self.assertEqual(body["app_token"], "apt_alice")
        self.assertEqual(body["base_url"], "https://pushit-api.foxugly.com/api/v1")

        # Alice sees her target; Bob sees nothing.
        alice_list = self.client.get("/api/v1/pushit-targets", **self.auth(self.alice_token)).json()
        self.assertEqual(len(alice_list), 1)
        bob_list = self.client.get("/api/v1/pushit-targets", **self.auth(self.bob_token)).json()
        self.assertEqual(bob_list, [])

    def test_duplicate_name_per_owner_conflicts(self):
        self.client.post("/api/v1/pushit-targets", data={"name": "phone", "app_token": "a"}, content_type="application/json", **self.auth(self.alice_token))
        dup = self.client.post("/api/v1/pushit-targets", data={"name": "phone", "app_token": "b"}, content_type="application/json", **self.auth(self.alice_token))
        self.assertEqual(dup.status_code, 409, dup.content)
        # Same name is fine for a different owner.
        bob = self.client.post("/api/v1/pushit-targets", data={"name": "phone", "app_token": "c"}, content_type="application/json", **self.auth(self.bob_token))
        self.assertEqual(bob.status_code, 201, bob.content)

    def test_single_default_per_owner(self):
        first = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "phone", "app_token": "a", "is_default": True},
            content_type="application/json",
            **self.auth(self.alice_token),
        ).json()
        second = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "tablet", "app_token": "b", "is_default": True},
            content_type="application/json",
            **self.auth(self.alice_token),
        ).json()
        # The second default unsets the first.
        self.assertFalse(PushItTarget.objects.get(id=first["id"]).is_default)
        self.assertTrue(PushItTarget.objects.get(id=second["id"]).is_default)

    def test_update_and_delete(self):
        created = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "phone", "app_token": "a"},
            content_type="application/json",
            **self.auth(self.alice_token),
        ).json()
        patched = self.client.patch(
            f"/api/v1/pushit-targets/{created['id']}",
            data={"app_token": "apt_new", "title": "Custom"},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["app_token"], "apt_new")
        self.assertEqual(patched.json()["title"], "Custom")

        deleted = self.client.delete(f"/api/v1/pushit-targets/{created['id']}", **self.auth(self.alice_token))
        self.assertEqual(deleted.status_code, 204, deleted.content)
        self.assertFalse(PushItTarget.objects.filter(id=created["id"]).exists())

    def test_cannot_touch_other_users_target(self):
        created = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "phone", "app_token": "a"},
            content_type="application/json",
            **self.auth(self.alice_token),
        ).json()
        # Bob cannot patch or delete Alice's target -> 404 (existence hidden).
        self.assertEqual(
            self.client.patch(f"/api/v1/pushit-targets/{created['id']}", data={"title": "x"}, content_type="application/json", **self.auth(self.bob_token)).status_code,
            404,
        )
        self.assertEqual(self.client.delete(f"/api/v1/pushit-targets/{created['id']}", **self.auth(self.bob_token)).status_code, 404)

    def test_test_endpoint_sends_via_notifier(self):
        created = self.client.post(
            "/api/v1/pushit-targets",
            data={"name": "phone", "app_token": "apt_test"},
            content_type="application/json",
            **self.auth(self.alice_token),
        ).json()
        with patch("app.notifier.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.raise_for_status = lambda: None
            response = self.client.post(f"/api/v1/pushit-targets/{created['id']}/test", **self.auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["sent"])
        self.assertEqual(post.call_args[0][0], "https://pushit-api.foxugly.com/api/v1/notifications/app/send/")

    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/v1/pushit-targets").status_code, 401)


class LoadPushItForOwnerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="carol@example.com", password="password123!")

    def test_returns_none_without_target(self):
        self.assertIsNone(load_pushit_config_for_owner(self.user.id))
        self.assertIsNone(load_pushit_config_for_owner(None))

    def test_prefers_default_target(self):
        PushItTarget.objects.create(owner=self.user, name="aaa", app_token="apt_a")
        PushItTarget.objects.create(owner=self.user, name="zzz", app_token="apt_z", is_default=True)
        config = load_pushit_config_for_owner(self.user.id)
        self.assertIsNotNone(config)
        self.assertEqual(config.app_token, "apt_z")

    def test_falls_back_to_first_when_no_default(self):
        PushItTarget.objects.create(owner=self.user, name="aaa", app_token="apt_a")
        PushItTarget.objects.create(owner=self.user, name="bbb", app_token="apt_b")
        config = load_pushit_config_for_owner(self.user.id)
        self.assertEqual(config.app_token, "apt_a")

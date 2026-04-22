"""Integration tests for the Phase 4.6 user-scoped catalog views.

Three GET endpoints under ``/api/v1/users/{user_id}/``:

    GET /scenarios               (paginated; per-item role + writable)
    GET /scenarios/{scenario_id} (detail with role/writable + full DSL JSON)
    GET /scenario-data           (aggregated pushover/network keys)

The list/detail endpoints add ``role`` (``superuser`` | ``owner`` |
``reader``) and ``writable`` (= role != "reader") to the standard
``ScenarioOut`` shape. ``/scenario-data`` aggregates the JSON-only
catalog metadata via ``scenarios.loader.load_scenario_data`` and 404s
when the user has no accessible scenarios.

``require_user_scope`` gates access -- non-admin users can only inspect
their own catalog (UUID or email both accepted as ``{user_id}``).
"""

from __future__ import annotations

from unittest.mock import patch

from accounts.models import User
from django.test import Client, TestCase

from catalog.models import Scenario, ScenarioShare
from scenarios.loader import ScenarioData


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


class _BaseUserScenariosApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")
        # Two scenarios for Alice + one for Bob (used by share tests).
        self.alice_scenario_a = Scenario.objects.create(
            scenario_id="sc-alice-a",
            owner_user_id=str(self.alice.id),
            description="alice A",
            definition={"steps": [{"type": "sleep", "seconds": 1}]},
        )
        self.alice_scenario_b = Scenario.objects.create(
            scenario_id="sc-alice-b",
            owner_user_id=str(self.alice.id),
            description="alice B",
            definition={"steps": []},
        )
        self.bob_scenario = Scenario.objects.create(
            scenario_id="sc-bob",
            owner_user_id=str(self.bob.id),
            description="bob",
            definition={"steps": []},
        )


class ListUserScenariosTest(_BaseUserScenariosApiTest):
    def test_list_user_scenarios_owner_sees_owned(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 2)
        ids = {item["scenario_id"] for item in body["items"]}
        self.assertEqual(ids, {"sc-alice-a", "sc-alice-b"})
        for item in body["items"]:
            self.assertEqual(item["role"], "owner")
            self.assertTrue(item["writable"])

    def test_list_user_scenarios_owner_sees_shared(self):
        # Bob shares his scenario with Alice -> Alice sees 3 total, the
        # shared one with role=reader / writable=False.
        ScenarioShare.objects.create(scenario=self.bob_scenario, user_id=str(self.alice.id))
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 3)
        items_by_id = {item["scenario_id"]: item for item in body["items"]}
        self.assertEqual(items_by_id["sc-alice-a"]["role"], "owner")
        self.assertTrue(items_by_id["sc-alice-a"]["writable"])
        self.assertEqual(items_by_id["sc-bob"]["role"], "reader")
        self.assertFalse(items_by_id["sc-bob"]["writable"])

    def test_list_user_scenarios_paginates(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios?limit=1&offset=0",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 1)
        self.assertEqual(body["offset"], 0)

    def test_list_user_scenarios_other_user_403(self):
        # Bob tries to list Alice's scenarios -> require_user_scope -> 403.
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_list_user_scenarios_admin_lists_other(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        # Admin is superuser -> sees everything (3 scenarios across DB).
        self.assertEqual(body["total"], 3)
        for item in body["items"]:
            self.assertEqual(item["role"], "superuser")
            self.assertTrue(item["writable"])

    def test_list_user_scenarios_email_alias(self):
        # ``{user_id}`` accepts the email form -- mirrors the FastAPI
        # dual-stack identifier shape until Phase 5 normalizes to UUIDs.
        # ``require_user_scope`` recognizes both the UUID and the email,
        # so the request returns 200. The candidate set used by the
        # service is ``{user_id, current_user.email}``; when called via
        # the email URL both collapse to the email, and only scenarios
        # whose ``owner_user_id`` was seeded with the email form are
        # listed (matches the FastAPI behaviour -- Phase 5 normalizes
        # this once owner_user_id is a UUID FK).
        Scenario.objects.create(
            scenario_id="sc-alice-email-owned",
            owner_user_id=self.alice.email,
            description="seeded via email",
            definition={"steps": []},
        )
        response = self.client.get(
            f"/api/v1/users/{self.alice.email}/scenarios",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        ids = {item["scenario_id"] for item in body["items"]}
        self.assertEqual(ids, {"sc-alice-email-owned"})
        self.assertEqual(body["total"], 1)


class GetUserScenarioTest(_BaseUserScenariosApiTest):
    def test_get_user_scenario_owner(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios/{self.alice_scenario_a.scenario_id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "sc-alice-a")
        self.assertEqual(body["role"], "owner")
        self.assertTrue(body["writable"])
        # The detail endpoint returns the full DSL definition.
        self.assertIn("definition", body)
        self.assertEqual(body["definition"], {"steps": [{"type": "sleep", "seconds": 1}]})

    def test_get_user_scenario_shared(self):
        ScenarioShare.objects.create(scenario=self.bob_scenario, user_id=str(self.alice.id))
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios/{self.bob_scenario.scenario_id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "sc-bob")
        self.assertEqual(body["role"], "reader")
        self.assertFalse(body["writable"])
        self.assertIn("definition", body)

    def test_get_user_scenario_not_visible_404(self):
        # Alice has access to her own scope, but cannot see Bob's
        # non-shared scenario -> 404 (collapses with not-found to avoid
        # leaking existence).
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios/{self.bob_scenario.scenario_id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_get_user_scenario_other_user_403(self):
        # Bob hits Alice's scope -> require_user_scope -> 403 BEFORE the
        # scenario lookup happens (verifies ordering of checks).
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/scenarios/{self.alice_scenario_a.scenario_id}",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class ScenarioDataTest(_BaseUserScenariosApiTest):
    def test_scenario_data_404_when_no_scenarios(self):
        # Bob owns sc-bob; create a fresh user with zero scenarios.
        carol = User.objects.create_user(email="carol@example.com", password="password123!")
        carol_token = _login(self.client, "carol@example.com", "password123!")
        response = self.client.get(
            f"/api/v1/users/{carol.id}/scenario-data",
            **_auth(carol_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_scenario_data_returns_aggregated_keys(self):
        fake = ScenarioData(
            pushovers={"a": object(), "b": object()},  # values irrelevant -- only keys are sorted
            networks={"x": object(), "y": object()},
            default_pushover_key="dpk",
            default_network_key="dnk",
        )
        # Patch the bound name in catalog.services (the import path the
        # endpoint actually uses), per the standard "patch where it's
        # used" rule.
        with patch("catalog.services.load_scenario_data", return_value=fake):
            response = self.client.get(
                f"/api/v1/users/{self.alice.id}/scenario-data",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["default_pushover_key"], "dpk")
        self.assertEqual(body["default_network_key"], "dnk")
        self.assertEqual(body["pushovers"], ["a", "b"])
        self.assertEqual(body["networks"], ["x", "y"])

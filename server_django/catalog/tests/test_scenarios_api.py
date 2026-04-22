"""Integration tests for the Phase 4.2/4.3 scenarios + shares endpoints.

Each test logs in via the JWT form-login wrapper (mirroring how the
Angular client authenticates), exercises the endpoint, then asserts the
HTTP response shape, the resulting DB state, and the audit row written
by ``ops.services.write_audit``.
"""

from __future__ import annotations

import json

from accounts.models import User
from django.test import Client, TestCase
from ops.models import AuditEntry

from catalog.models import Scenario, ScenarioShare, Slot


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


class _BaseScenarioApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")


class CreateScenarioTest(_BaseScenarioApiTest):
    def _payload(self, owner_user_id: str | None = None, scenario_id: str = "demo") -> dict:
        return {
            "scenario_id": scenario_id,
            "owner_user_id": owner_user_id or str(self.alice.id),
            "description": "demo description",
            "definition": {"steps": [{"type": "sleep", "seconds": 1}]},
        }

    def test_create_scenario_returns_201_and_summary(self):
        response = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(self._payload()),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "demo")
        self.assertEqual(body["owner_user_id"], str(self.alice.id))
        self.assertEqual(body["description"], "demo description")
        self.assertEqual(body["steps"], 1)
        self.assertFalse(body["requires_enterprise_network"])
        self.assertTrue(Scenario.objects.filter(scenario_id="demo").exists())
        self.assertTrue(
            AuditEntry.objects.filter(
                action="scenario.create",
                target_type="scenario",
                target_id="demo",
                actor=self.alice,
            ).exists()
        )

    def test_create_scenario_idempotent_replay_returns_same_body(self):
        payload = self._payload()
        first = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-1",
            **_auth(self.alice_token),
        )
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-1",
            **_auth(self.alice_token),
        )
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(first.json(), replay.json())
        # Only one DB row for the scenario.
        self.assertEqual(Scenario.objects.filter(scenario_id="demo").count(), 1)
        # Only one audit row for the create.
        self.assertEqual(AuditEntry.objects.filter(action="scenario.create").count(), 1)

    def test_create_scenario_idempotent_with_different_payload_returns_409(self):
        first = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(self._payload(scenario_id="a")),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-2",
            **_auth(self.alice_token),
        )
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(self._payload(scenario_id="b")),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-2",
            **_auth(self.alice_token),
        )
        self.assertEqual(replay.status_code, 409, replay.content)

    def test_create_scenario_owner_mismatch_returns_403(self):
        # Alice tries to create a scenario owned by Bob -> 403
        response = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(self._payload(owner_user_id=str(self.bob.id))),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(Scenario.objects.filter(scenario_id="demo").exists())


class UpdateScenarioTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(
            scenario_id="s1",
            owner=self.alice,
            description="initial",
            definition={"steps": []},
        )

    def test_patch_scenario_as_owner_updates_description(self):
        response = self.client.patch(
            "/api/v1/scenarios/s1",
            data=json.dumps({"description": "updated"}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.definition.get("description"), "updated")
        self.assertTrue(AuditEntry.objects.filter(action="scenario.update", target_id="s1").exists())

    def test_patch_scenario_as_non_owner_returns_403(self):
        response = self.client.patch(
            "/api/v1/scenarios/s1",
            data=json.dumps({"description": "hijack"}),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        # Bob cannot see the scenario at all (not owner, not shared) -> 404.
        # When the scenario is shared with Bob (read-only), patching is
        # forbidden -> 403. The test here checks the unshared path.
        self.assertEqual(response.status_code, 404, response.content)

    def test_patch_scenario_renames_and_cascades_to_slots(self):
        Slot.objects.create(slot_id="slot1", scenario=self.scenario, days=[0], start="08:00", end="09:00")
        Slot.objects.create(slot_id="slot2", scenario=self.scenario, days=[1], start="09:00", end="10:00")
        response = self.client.patch(
            "/api/v1/scenarios/s1",
            data=json.dumps({"scenario_id": "s1-renamed"}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["scenario_id"], "s1-renamed")
        self.assertFalse(Scenario.objects.filter(scenario_id="s1").exists())
        self.assertTrue(Scenario.objects.filter(scenario_id="s1-renamed").exists())
        self.assertEqual(Slot.objects.filter(scenario_id="s1-renamed").count(), 2)
        self.assertEqual(Slot.objects.filter(scenario_id="s1").count(), 0)

    def test_patch_scenario_rename_to_existing_id_returns_409(self):
        Scenario.objects.create(scenario_id="other", owner=self.alice)
        response = self.client.patch(
            "/api/v1/scenarios/s1",
            data=json.dumps({"scenario_id": "other"}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 409, response.content)


class DeleteScenarioTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(scenario_id="del", owner=self.alice, definition={"steps": []})

    def test_delete_scenario_as_owner_no_slots(self):
        response = self.client.delete("/api/v1/scenarios/del", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"deleted": "del"})
        self.assertFalse(Scenario.objects.filter(scenario_id="del").exists())
        self.assertTrue(AuditEntry.objects.filter(action="scenario.delete", target_id="del").exists())

    def test_delete_scenario_with_slots_returns_409(self):
        Slot.objects.create(slot_id="slot-del", scenario=self.scenario, days=[0], start="08:00", end="09:00")
        response = self.client.delete("/api/v1/scenarios/del", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 409, response.content)
        body = response.json()
        # The French error message is preserved verbatim.
        self.assertIn("Supprime", body["message"])
        self.assertTrue(Scenario.objects.filter(scenario_id="del").exists())

    def test_delete_scenario_as_non_owner_returns_404(self):
        # Bob has no read access -> 404 (consistent with FastAPI behaviour
        # which collapses non-visibility into 404 to avoid leaking
        # existence).
        response = self.client.delete("/api/v1/scenarios/del", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 404, response.content)


class DuplicateScenarioTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(
            scenario_id="src",
            owner=self.alice,
            description="src description",
            definition={"steps": [{"type": "sleep", "seconds": 1}]},
        )

    def test_duplicate_scenario_as_owner(self):
        response = self.client.post(
            "/api/v1/scenarios/src/duplicate?new_scenario_id=copy",
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "copy")
        self.assertEqual(body["owner_user_id"], str(self.alice.id))
        self.assertEqual(body["steps"], 1)
        self.assertTrue(Scenario.objects.filter(scenario_id="copy").exists())
        self.assertTrue(AuditEntry.objects.filter(action="scenario.duplicate", target_id="copy").exists())

    def test_duplicate_scenario_with_existing_target_returns_409(self):
        Scenario.objects.create(scenario_id="copy", owner=self.alice)
        response = self.client.post(
            "/api/v1/scenarios/src/duplicate?new_scenario_id=copy",
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 409, response.content)


class ListSharesTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(scenario_id="shared", owner=self.alice, definition={"steps": []})
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)

    def test_list_shares_as_owner(self):
        response = self.client.get("/api/v1/scenarios/shared/shares", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "shared")
        self.assertEqual(body["user_ids"], [str(self.bob.id)])

    def test_list_shares_as_shared_user(self):
        # Bob is shared on the scenario -> read access OK
        response = self.client.get("/api/v1/scenarios/shared/shares", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["user_ids"], [str(self.bob.id)])

    def test_list_shares_as_random_user_returns_404(self):
        carol = User.objects.create_user(email="carol@example.com", password="password123!")
        carol_token = _login(self.client, "carol@example.com", "password123!")
        response = self.client.get("/api/v1/scenarios/shared/shares", **_auth(carol_token))
        self.assertEqual(response.status_code, 404, response.content)
        self.assertIsNotNone(carol)


class ShareScenarioTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(scenario_id="s2", owner=self.alice, definition={"steps": []})

    def test_share_scenario_as_owner_returns_201(self):
        response = self.client.post(
            "/api/v1/scenarios/s2/shares",
            data=json.dumps({"user_id": str(self.bob.id)}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            response.json(),
            {"scenario_id": "s2", "user_id": str(self.bob.id)},
        )
        self.assertEqual(ScenarioShare.objects.filter(scenario=self.scenario).count(), 1)
        self.assertTrue(AuditEntry.objects.filter(action="scenario.share", target_id="s2").exists())

    def test_share_scenario_idempotent_for_same_user(self):
        body = json.dumps({"user_id": str(self.bob.id)})
        first = self.client.post(
            "/api/v1/scenarios/s2/shares",
            data=body,
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post(
            "/api/v1/scenarios/s2/shares",
            data=body,
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(second.status_code, 201, second.content)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(ScenarioShare.objects.filter(scenario=self.scenario).count(), 1)

    def test_share_scenario_as_non_owner_returns_404_or_403(self):
        # Bob isn't owner and isn't shared yet -> 404 (visibility check first).
        response = self.client.post(
            "/api/v1/scenarios/s2/shares",
            data=json.dumps({"user_id": str(self.bob.id)}),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_share_scenario_as_shared_user_returns_403(self):
        # Once shared, Bob can read but cannot write -> 403 from
        # require_scenario_owner.
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        carol = User.objects.create_user(email="carol2@example.com", password="password123!")
        response = self.client.post(
            "/api/v1/scenarios/s2/shares",
            data=json.dumps({"user_id": str(carol.id)}),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class UnshareScenarioTest(_BaseScenarioApiTest):
    def setUp(self):
        super().setUp()
        self.scenario = Scenario.objects.create(scenario_id="s3", owner=self.alice, definition={"steps": []})
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)

    def test_unshare_scenario_as_owner(self):
        response = self.client.delete(
            f"/api/v1/scenarios/s3/shares/{self.bob.id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"deleted": str(self.bob.id)})
        self.assertFalse(ScenarioShare.objects.filter(scenario=self.scenario).exists())
        self.assertTrue(AuditEntry.objects.filter(action="scenario.unshare", target_id="s3").exists())

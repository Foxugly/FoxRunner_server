"""Integration tests for the Phase 4.4 slots endpoints.

Each test logs in via the JWT form-login wrapper, exercises the endpoint,
then asserts the HTTP response shape, the resulting DB state, and the
audit row written by ``ops.services.write_audit``.
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


class _BaseSlotsApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")
        # Default scenario owned by Alice. Tests that need richer fixtures
        # create more scenarios in their own setUp.
        self.scenario = Scenario.objects.create(
            scenario_id="sc-alice",
            owner=self.alice,
            description="alice scenario",
            definition={"steps": []},
        )


class CreateSlotTest(_BaseSlotsApiTest):
    def _payload(
        self,
        *,
        slot_id: str = "slot-1",
        scenario_id: str | None = None,
        days: list[int] | None = None,
        start: str = "08:00",
        end: str = "09:00",
        enabled: bool = True,
    ) -> dict:
        return {
            "slot_id": slot_id,
            "scenario_id": scenario_id or self.scenario.scenario_id,
            "days": days if days is not None else [0, 1, 2],
            "start": start,
            "end": end,
            "enabled": enabled,
        }

    def test_create_slot_201(self):
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload()),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["slot_id"], "slot-1")
        self.assertEqual(body["scenario_id"], self.scenario.scenario_id)
        self.assertEqual(body["days"], [0, 1, 2])
        self.assertEqual(body["start"], "08:00")
        self.assertEqual(body["end"], "09:00")
        self.assertTrue(body["enabled"])
        self.assertTrue(Slot.objects.filter(slot_id="slot-1").exists())
        self.assertTrue(
            AuditEntry.objects.filter(
                action="slot.create",
                target_type="slot",
                target_id="slot-1",
                actor=self.alice,
            ).exists()
        )

    def test_create_slot_invalid_days(self):
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload(days=[7])),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 422, response.content)
        self.assertFalse(Slot.objects.filter(slot_id="slot-1").exists())

    def test_create_slot_bad_time_format(self):
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload(start="8:00")),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 422, response.content)
        self.assertFalse(Slot.objects.filter(slot_id="slot-1").exists())

    def test_create_slot_idempotent_same_payload(self):
        payload = self._payload()
        first = self.client.post(
            "/api/v1/slots",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-1",
            **_auth(self.alice_token),
        )
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.client.post(
            "/api/v1/slots",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-1",
            **_auth(self.alice_token),
        )
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(Slot.objects.filter(slot_id="slot-1").count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="slot.create").count(), 1)

    def test_create_slot_idempotent_different_payload(self):
        first = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload(slot_id="a")),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-2",
            **_auth(self.alice_token),
        )
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload(slot_id="b")),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="key-2",
            **_auth(self.alice_token),
        )
        self.assertEqual(replay.status_code, 409, replay.content)

    def test_create_slot_scenario_not_owner_returns_403(self):
        # Bob is shared on Alice's scenario (read access) but not owner.
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload()),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(Slot.objects.filter(slot_id="slot-1").exists())

    def test_create_slot_scenario_not_visible_returns_404(self):
        # Bob cannot even see the scenario -> 404 (visibility check first).
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(self._payload()),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(Slot.objects.filter(slot_id="slot-1").exists())


class ListSlotsTest(_BaseSlotsApiTest):
    def setUp(self):
        super().setUp()
        # Five slots under Alice's scenario.
        for index in range(5):
            Slot.objects.create(
                slot_id=f"alice-slot-{index}",
                scenario=self.scenario,
                days=[index % 7],
                start="08:00",
                end="09:00",
            )
        # A second scenario owned by Bob, with one slot.
        self.bob_scenario = Scenario.objects.create(
            scenario_id="sc-bob",
            owner=self.bob,
            definition={"steps": []},
        )
        Slot.objects.create(
            slot_id="bob-slot-1",
            scenario=self.bob_scenario,
            days=[0],
            start="10:00",
            end="11:00",
        )

    def test_list_slots_paginates(self):
        # Alice owns 5 slots. limit=2 -> items=2, total=5.
        response = self.client.get("/api/v1/slots?limit=2&offset=0", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)

    def test_list_slots_filter_by_scenario_id(self):
        response = self.client.get(
            f"/api/v1/slots?scenario_id={self.scenario.scenario_id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 5)
        for item in body["items"]:
            self.assertEqual(item["scenario_id"], self.scenario.scenario_id)

    def test_list_slots_filter_unknown_scenario_for_non_admin_returns_404(self):
        # Alice cannot see Bob's scenario -> filtering on it surfaces 404
        # (matches FastAPI quirk #1 in the task spec).
        response = self.client.get(
            f"/api/v1/slots?scenario_id={self.bob_scenario.scenario_id}",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_list_slots_admin_sees_all(self):
        response = self.client.get("/api/v1/slots", **_auth(self.admin_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        # 5 (alice) + 1 (bob) = 6
        self.assertEqual(body["total"], 6)


class GetSlotTest(_BaseSlotsApiTest):
    def setUp(self):
        super().setUp()
        self.slot = Slot.objects.create(
            slot_id="slot-x",
            scenario=self.scenario,
            days=[0, 1],
            start="08:00",
            end="09:00",
        )

    def test_get_slot_owned(self):
        response = self.client.get(f"/api/v1/slots/{self.slot.slot_id}", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["slot_id"], "slot-x")
        self.assertEqual(body["days"], [0, 1])
        self.assertEqual(body["start"], "08:00")
        self.assertEqual(body["end"], "09:00")
        self.assertEqual(body["scenario_id"], self.scenario.scenario_id)

    def test_get_slot_not_visible(self):
        # Bob has no read access to Alice's scenario -> 404 on the slot.
        response = self.client.get(f"/api/v1/slots/{self.slot.slot_id}", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 404, response.content)

    def test_get_slot_shared_user(self):
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.get(f"/api/v1/slots/{self.slot.slot_id}", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["slot_id"], "slot-x")


class PatchSlotTest(_BaseSlotsApiTest):
    def setUp(self):
        super().setUp()
        self.slot = Slot.objects.create(
            slot_id="slot-y",
            scenario=self.scenario,
            days=[0],
            start="08:00",
            end="09:00",
        )
        # Second scenario owned by Alice (for the reassignment test).
        self.alice_scenario_2 = Scenario.objects.create(
            scenario_id="sc-alice-2",
            owner=self.alice,
            definition={"steps": []},
        )
        # Scenario owned by Bob (for the unowned-target reassignment test).
        self.bob_scenario = Scenario.objects.create(
            scenario_id="sc-bob",
            owner=self.bob,
            definition={"steps": []},
        )

    def test_patch_slot_owner(self):
        response = self.client.patch(
            f"/api/v1/slots/{self.slot.slot_id}",
            data=json.dumps({"days": [3, 4], "enabled": False}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["days"], [3, 4])
        self.assertFalse(body["enabled"])
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.days, [3, 4])
        self.assertFalse(self.slot.enabled)
        self.assertTrue(AuditEntry.objects.filter(action="slot.update", target_id="slot-y").exists())

    def test_patch_slot_reassign_scenario_owner_to_target(self):
        response = self.client.patch(
            f"/api/v1/slots/{self.slot.slot_id}",
            data=json.dumps({"scenario_id": self.alice_scenario_2.scenario_id}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], self.alice_scenario_2.scenario_id)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.scenario_id, self.alice_scenario_2.scenario_id)

    def test_patch_slot_reassign_to_unowned_scenario_403(self):
        # Bob's scenario is visible to Alice only if shared; we share it
        # so the visibility check passes and the owner check is the one
        # that returns 403 (otherwise the test would assert 404 instead).
        ScenarioShare.objects.create(scenario=self.bob_scenario, user=self.alice)
        response = self.client.patch(
            f"/api/v1/slots/{self.slot.slot_id}",
            data=json.dumps({"scenario_id": self.bob_scenario.scenario_id}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.scenario_id, self.scenario.scenario_id)

    def test_patch_slot_non_owner_403(self):
        # Bob is shared on the scenario (read access) but not owner.
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.patch(
            f"/api/v1/slots/{self.slot.slot_id}",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class DeleteSlotTest(_BaseSlotsApiTest):
    def setUp(self):
        super().setUp()
        self.slot = Slot.objects.create(
            slot_id="slot-z",
            scenario=self.scenario,
            days=[0],
            start="08:00",
            end="09:00",
        )

    def test_delete_slot_owner(self):
        response = self.client.delete(f"/api/v1/slots/{self.slot.slot_id}", **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"deleted": "slot-z"})
        self.assertFalse(Slot.objects.filter(slot_id="slot-z").exists())
        self.assertTrue(AuditEntry.objects.filter(action="slot.delete", target_id="slot-z").exists())

    def test_delete_slot_non_owner_403(self):
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.delete(f"/api/v1/slots/{self.slot.slot_id}", **_auth(self.bob_token))
        self.assertEqual(response.status_code, 403, response.content)
        self.assertTrue(Slot.objects.filter(slot_id="slot-z").exists())

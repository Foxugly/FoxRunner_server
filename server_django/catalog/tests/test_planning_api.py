"""Integration tests for the Phase 4.7 planning + sync run endpoints.

Four endpoints under ``/api/v1/users/{user_id}/``:

    GET  /plan                          (404 if no scenarios)
    GET  /slots                         (paginated, user-scoped)
    POST /scenarios/{scenario_id}/run   (sync, dry_run query)
    POST /run-next                      (sync, dry_run query)

The two run endpoints are intentionally blocking -- they bypass Celery
for the admin/debug path. The async Job-queueing path lives in Phase 6.

These tests patch ``catalog.services.build_service_from_db`` to return a
``MagicMock`` SchedulerService so the engine wiring (config files,
network guard, scenario data JSON) is sidestepped. The DB visibility
rules are exercised against real ORM rows.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from accounts.models import User
from django.test import Client, TestCase

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


class _BasePlanningApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(
            email="alice@example.com",
            password="password123!",
            timezone_name="Europe/Brussels",
        )
        self.bob = User.objects.create_user(
            email="bob@example.com",
            password="password123!",
        )
        self.admin = User.objects.create_superuser(
            email="admin@example.com",
            password="password123!",
        )
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")
        self.alice_scenario = Scenario.objects.create(
            scenario_id="sc-alice",
            owner_user_id=str(self.alice.id),
            description="alice scenario",
            definition={"steps": [{"type": "sleep", "seconds": 1}]},
        )
        self.alice_slot = Slot.objects.create(
            slot_id="slot-alice-1",
            scenario=self.alice_scenario,
            days=[0, 1, 2],
            start="08:00",
            end="09:00",
            enabled=True,
        )
        self.bob_scenario = Scenario.objects.create(
            scenario_id="sc-bob",
            owner_user_id=str(self.bob.id),
            description="bob scenario",
            definition={"steps": []},
        )


# --------------------------------------------------------------------------
# /users/{user_id}/plan
# --------------------------------------------------------------------------


class UserPlanTest(_BasePlanningApiTest):
    def test_user_plan_returns_plan(self):
        plan_payload = {
            "generated_at": "2026-04-22T08:00:00+00:00",
            "timezone": "Europe/Brussels",
            "slot_key": "0_08:00",
            "slot_id": "slot-alice-1",
            "scenario_id": "sc-alice",
            "scheduled_for": "2026-04-22T08:00:00+00:00",
            "requires_enterprise_network": False,
            "before_steps": 0,
            "steps": 1,
            "on_success": 0,
            "on_failure": 0,
            "finally_steps": 0,
            "default_pushover_key": None,
            "default_network_key": None,
            "default_network_available": True,
        }
        mock_service = MagicMock()
        mock_service.describe_plan_for_scenarios.return_value = plan_payload
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.get(
                f"/api/v1/users/{self.alice.id}/plan",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body, plan_payload)
        # The set passed in is ``scenario_ids_for_user`` -> {"sc-alice"}
        called_arg = mock_service.describe_plan_for_scenarios.call_args.args[0]
        self.assertEqual(called_arg, {"sc-alice"})

    def test_user_plan_no_scenarios_404(self):
        carol = User.objects.create_user(email="carol@example.com", password="password123!")
        carol_token = _login(self.client, "carol@example.com", "password123!")
        response = self.client.get(
            f"/api/v1/users/{carol.id}/plan",
            **_auth(carol_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_user_plan_other_user_403(self):
        # Bob requests Alice's plan -- require_user_scope -> 403.
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/plan",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_user_plan_scheduler_runtime_error_404(self):
        # The scheduler raises RuntimeError when a scenario is missing
        # from its catalog -- the endpoint must surface it as 404 with
        # the original message preserved (parity with FastAPI 215-216).
        mock_service = MagicMock()
        mock_service.describe_plan_for_scenarios.side_effect = RuntimeError("scenario X introuvable")
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.get(
                f"/api/v1/users/{self.alice.id}/plan",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 404, response.content)
        self.assertIn("scenario X introuvable", response.content.decode())

    def test_user_plan_uses_target_user_timezone(self):
        # When admin pulls Alice's plan, we want Alice's timezone, not
        # admin's. Patch ``timezone_for_user`` to assert the wiring.
        mock_service = MagicMock()
        mock_service.describe_plan_for_scenarios.return_value = {}
        with (
            patch("catalog.api.timezone_for_user", return_value="Europe/Paris") as tz_patch,
            patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service) as build_patch,
        ):
            response = self.client.get(
                f"/api/v1/users/{self.alice.id}/plan",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        tz_patch.assert_called_once()
        build_patch.assert_called_once_with(timezone_name="Europe/Paris")


# --------------------------------------------------------------------------
# /users/{user_id}/slots
# --------------------------------------------------------------------------


class UserSlotsTest(_BasePlanningApiTest):
    def test_user_slots_lists_owned(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/slots",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["slot_id"], "slot-alice-1")
        self.assertEqual(body["limit"], 100)
        self.assertEqual(body["offset"], 0)

    def test_user_slots_paginates(self):
        # Add 4 more slots for Alice, then request limit=2 to verify the
        # envelope numbers.
        for index in range(4):
            scenario = Scenario.objects.create(
                scenario_id=f"sc-alice-extra-{index}",
                owner_user_id=str(self.alice.id),
                description="",
                definition={"steps": []},
            )
            Slot.objects.create(
                slot_id=f"slot-alice-extra-{index}",
                scenario=scenario,
                days=[0],
                start="10:00",
                end="11:00",
                enabled=True,
            )
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/slots?limit=2&offset=0",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)

    def test_user_slots_other_user_403(self):
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/slots",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_user_slots_admin_lists_other(self):
        # Admin sees all slots regardless of owner -- because
        # ``current_user.is_superuser`` short-circuits the visibility filter.
        Slot.objects.create(
            slot_id="slot-bob-1",
            scenario=self.bob_scenario,
            days=[3],
            start="14:00",
            end="15:00",
            enabled=True,
        )
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/slots",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        # 2 slots in the DB total (alice's + bob's).
        self.assertEqual(body["total"], 2)


# --------------------------------------------------------------------------
# /users/{user_id}/scenarios/{sid}/run
# --------------------------------------------------------------------------


class RunUserScenarioTest(_BasePlanningApiTest):
    def test_run_scenario_dry_run_true(self):
        mock_service = MagicMock()
        mock_service.run_scenario.return_value = 0
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/{self.alice_scenario.scenario_id}/run?dry_run=true",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["scenario_id"], "sc-alice")
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["exit_code"], 0)
        self.assertTrue(body["success"])
        mock_service.run_scenario.assert_called_once_with("sc-alice", dry_run=True)

    def test_run_scenario_dry_run_false_failure_exit_code(self):
        mock_service = MagicMock()
        mock_service.run_scenario.return_value = 2
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/scenarios/{self.alice_scenario.scenario_id}/run?dry_run=false",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["exit_code"], 2)
        self.assertFalse(body["success"])
        mock_service.run_scenario.assert_called_once_with("sc-alice", dry_run=False)

    def test_run_scenario_invisible_returns_404(self):
        # Bob is the actor on his own scope, but tries to run Alice's
        # non-shared scenario -- the visibility check returns 404 (404
        # vs 403 collapses to avoid leaking existence).
        response = self.client.post(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.alice_scenario.scenario_id}/run",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_run_scenario_other_user_403(self):
        response = self.client.post(
            f"/api/v1/users/{self.alice.id}/scenarios/{self.alice_scenario.scenario_id}/run",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_run_scenario_shared_visibility_allows_run(self):
        # Bob is granted access to Alice's scenario via a share -- he can
        # run it (the visibility check allows shared readers; the engine
        # is mocked so we assert the wiring, not the side effects).
        ScenarioShare.objects.create(scenario=self.alice_scenario, user_id=str(self.bob.id))
        mock_service = MagicMock()
        mock_service.run_scenario.return_value = 0
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.post(
                f"/api/v1/users/{self.bob.id}/scenarios/{self.alice_scenario.scenario_id}/run",
                **_auth(self.bob_token),
            )
        self.assertEqual(response.status_code, 200, response.content)


# --------------------------------------------------------------------------
# /users/{user_id}/run-next
# --------------------------------------------------------------------------


class RunUserNextTest(_BasePlanningApiTest):
    def test_run_next_no_scenarios_404(self):
        carol = User.objects.create_user(email="carol@example.com", password="password123!")
        carol_token = _login(self.client, "carol@example.com", "password123!")
        response = self.client.post(
            f"/api/v1/users/{carol.id}/run-next",
            **_auth(carol_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_run_next_returns_exit_code(self):
        mock_service = MagicMock()
        mock_service.run_next_for_scenarios.return_value = 0
        with patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/run-next?dry_run=true",
                **_auth(self.alice_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        # /run-next omits scenario_id -- the schema accepts it as None
        # and Ninja serializes it as null. The shape must NOT carry an
        # actual scenario_id value.
        self.assertIsNone(body.get("scenario_id"))
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["exit_code"], 0)
        self.assertTrue(body["success"])
        called_args = mock_service.run_next_for_scenarios.call_args
        self.assertEqual(called_args.args[0], {"sc-alice"})
        self.assertEqual(called_args.kwargs, {"dry_run": True})

    def test_run_next_uses_target_user_timezone(self):
        mock_service = MagicMock()
        mock_service.run_next_for_scenarios.return_value = 0
        with (
            patch("catalog.api.timezone_for_user", return_value="Europe/Paris") as tz_patch,
            patch("catalog.api.scenario_services.build_service_from_db", return_value=mock_service) as build_patch,
        ):
            response = self.client.post(
                f"/api/v1/users/{self.alice.id}/run-next",
                **_auth(self.admin_token),
            )
        self.assertEqual(response.status_code, 200, response.content)
        tz_patch.assert_called_once()
        build_patch.assert_called_once_with(timezone_name="Europe/Paris")

    def test_run_next_other_user_403(self):
        response = self.client.post(
            f"/api/v1/users/{self.alice.id}/run-next",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

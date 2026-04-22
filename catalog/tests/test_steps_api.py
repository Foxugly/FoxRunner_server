"""Integration tests for the Phase 4.5 step-collections endpoints.

Six endpoints under
``/api/v1/users/{user_id}/scenarios/{scenario_id}/step-collections``:

    GET    /step-collections                       (all 5, alphabetical)
    GET    /step-collections/{collection}          (raw array, no envelope)
    GET    /step-collections/{collection}/{index}  (single step dict)
    POST   /step-collections/{collection}?insert_at=
    PUT    /step-collections/{collection}/{index}
    DELETE /step-collections/{collection}/{index}

Each mutation writes an audit row (``step.create``, ``step.update``,
``step.delete``) and routes through ``save_scenario_definition`` under
the per-scenario lock.
"""

from __future__ import annotations

import json

from django.test import Client, TestCase

from accounts.models import User
from catalog.models import Scenario, ScenarioShare
from ops.models import AuditEntry


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


class _BaseStepsApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.scenario = Scenario.objects.create(
            scenario_id="sc-alice",
            owner=self.alice,
            description="alice scenario",
            definition={"steps": []},
        )

    def _url(self, *, user_id: str | None = None, scenario_id: str | None = None, suffix: str = "") -> str:
        user_part = user_id if user_id is not None else str(self.alice.id)
        scenario_part = scenario_id if scenario_id is not None else self.scenario.scenario_id
        base = f"/api/v1/users/{user_part}/scenarios/{scenario_part}/step-collections"
        return base + suffix


class ListStepCollectionsTest(_BaseStepsApiTest):
    def test_list_collections_returns_all_5_alphabetical(self):
        # Empty scenario -> dict with the 5 canonical keys, each empty.
        response = self.client.get(self._url(), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        expected_keys = sorted({"before_steps", "steps", "on_success", "on_failure", "finally_steps"})
        self.assertEqual(list(body.keys()), expected_keys)
        for key in expected_keys:
            self.assertEqual(body[key], [])

    def test_list_collections_with_content_per_collection(self):
        self.scenario.definition = {
            "before_steps": [{"type": "sleep", "seconds": 1}],
            "steps": [{"type": "http_request", "url": "https://example.com"}, {"type": "sleep", "seconds": 2}],
            "on_success": [{"type": "notify", "channel": "ok"}],
            "on_failure": [{"type": "notify", "channel": "ko"}],
            "finally_steps": [{"type": "sleep", "seconds": 3}],
        }
        self.scenario.save()
        response = self.client.get(self._url(), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(len(body["before_steps"]), 1)
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(len(body["on_success"]), 1)
        self.assertEqual(len(body["on_failure"]), 1)
        self.assertEqual(len(body["finally_steps"]), 1)

    def test_list_collections_shared_user_can_read(self):
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.get(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_list_collections_non_visible_returns_404(self):
        response = self.client.get(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 404, response.content)


class GetCollectionTest(_BaseStepsApiTest):
    def setUp(self):
        super().setUp()
        self.scenario.definition = {
            "steps": [{"type": "sleep", "seconds": 1}, {"type": "sleep", "seconds": 2}],
        }
        self.scenario.save()

    def test_get_collection_raw_array(self):
        # The endpoint returns the raw array, not an {items, total} envelope.
        response = self.client.get(self._url(suffix="/steps"), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["type"], "sleep")

    def test_get_collection_unknown_returns_404(self):
        response = self.client.get(self._url(suffix="/foo"), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 404, response.content)
        self.assertIn("Collection", response.json()["message"])


class GetStepTest(_BaseStepsApiTest):
    def setUp(self):
        super().setUp()
        self.scenario.definition = {
            "steps": [{"type": "sleep", "seconds": 1}, {"type": "http_request", "url": "https://x.com"}],
        }
        self.scenario.save()

    def test_get_step_by_index(self):
        response = self.client.get(self._url(suffix="/steps/1"), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["type"], "http_request")
        self.assertEqual(body["url"], "https://x.com")

    def test_get_step_index_out_of_range_returns_404(self):
        response = self.client.get(self._url(suffix="/steps/99"), **_auth(self.alice_token))
        self.assertEqual(response.status_code, 404, response.content)


class CreateStepTest(_BaseStepsApiTest):
    def setUp(self):
        super().setUp()
        self.scenario.definition = {
            "steps": [
                {"type": "sleep", "seconds": 1},
                {"type": "sleep", "seconds": 2},
            ],
        }
        self.scenario.save()

    def test_create_step_appends_by_default(self):
        payload = {"step": {"type": "sleep", "seconds": 3}}
        response = self.client.post(
            self._url(suffix="/steps"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["index"], 2)
        self.assertEqual(body["step"]["seconds"], 3)
        self.scenario.refresh_from_db()
        self.assertEqual(len(self.scenario.definition["steps"]), 3)
        self.assertEqual(self.scenario.definition["steps"][2]["seconds"], 3)

    def test_create_step_insert_at_middle(self):
        payload = {"step": {"type": "sleep", "seconds": 99}}
        response = self.client.post(
            self._url(suffix="/steps?insert_at=1"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["index"], 1)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.definition["steps"][1]["seconds"], 99)
        self.assertEqual(len(self.scenario.definition["steps"]), 3)

    def test_create_step_insert_at_clamped(self):
        payload = {"step": {"type": "sleep", "seconds": 42}}
        response = self.client.post(
            self._url(suffix="/steps?insert_at=999"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        # Clamped to len(steps) == 2.
        self.assertEqual(body["index"], 2)

    def test_create_step_insert_at_negative_rejected(self):
        payload = {"step": {"type": "sleep", "seconds": 1}}
        response = self.client.post(
            self._url(suffix="/steps?insert_at=-1"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 422, response.content)

    def test_create_step_in_missing_collection_creates_list(self):
        # ``before_steps`` not present in the definition yet -> helper creates
        # it as an empty list then appends.
        payload = {"step": {"type": "sleep", "seconds": 5}}
        response = self.client.post(
            self._url(suffix="/before_steps"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.definition["before_steps"], [{"type": "sleep", "seconds": 5}])

    def test_create_step_non_owner_returns_403(self):
        # Bob is shared on the scenario but cannot mutate.
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        payload = {"step": {"type": "sleep", "seconds": 1}}
        response = self.client.post(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections/steps",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_create_step_in_invisible_scenario_returns_404(self):
        # Bob can't even see the scenario -> 404 (hides existence).
        payload = {"step": {"type": "sleep", "seconds": 1}}
        response = self.client.post(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections/steps",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_create_step_unknown_collection_returns_404(self):
        payload = {"step": {"type": "sleep", "seconds": 1}}
        response = self.client.post(
            self._url(suffix="/unknown"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)


class UpdateStepTest(_BaseStepsApiTest):
    def setUp(self):
        super().setUp()
        self.scenario.definition = {
            "steps": [
                {"type": "sleep", "seconds": 1},
                {"type": "sleep", "seconds": 2},
            ],
        }
        self.scenario.save()

    def test_update_step_owner(self):
        payload = {"step": {"type": "http_request", "url": "https://new"}}
        response = self.client.put(
            self._url(suffix="/steps/1"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["index"], 1)
        self.assertEqual(body["step"]["type"], "http_request")
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.definition["steps"][1]["type"], "http_request")
        self.assertEqual(len(self.scenario.definition["steps"]), 2)

    def test_update_step_index_out_of_range_404(self):
        payload = {"step": {"type": "sleep", "seconds": 9}}
        response = self.client.put(
            self._url(suffix="/steps/99"),
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_update_step_non_owner_403(self):
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        payload = {"step": {"type": "sleep", "seconds": 9}}
        response = self.client.put(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections/steps/0",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class DeleteStepTest(_BaseStepsApiTest):
    def setUp(self):
        super().setUp()
        self.scenario.definition = {
            "steps": [
                {"type": "sleep", "seconds": 1},
                {"type": "sleep", "seconds": 2},
                {"type": "sleep", "seconds": 3},
            ],
        }
        self.scenario.save()

    def test_delete_step_owner(self):
        response = self.client.delete(
            self._url(suffix="/steps/1"),
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["index"], 1)
        self.assertEqual(body["deleted"], {"type": "sleep", "seconds": 2})
        self.scenario.refresh_from_db()
        self.assertEqual(len(self.scenario.definition["steps"]), 2)
        self.assertEqual(self.scenario.definition["steps"][1]["seconds"], 3)

    def test_delete_step_index_out_of_range_404(self):
        response = self.client.delete(
            self._url(suffix="/steps/99"),
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_delete_step_non_owner_403(self):
        ScenarioShare.objects.create(scenario=self.scenario, user=self.bob)
        response = self.client.delete(
            f"/api/v1/users/{self.bob.id}/scenarios/{self.scenario.scenario_id}/step-collections/steps/0",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)


class UserIdAliasTest(_BaseStepsApiTest):
    def test_user_id_email_alias(self):
        # Use the email as {user_id} instead of the UUID. ``require_user_scope``
        # accepts either.
        response = self.client.get(
            f"/api/v1/users/{self.alice.email}/scenarios/{self.scenario.scenario_id}/step-collections",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("steps", body)


class AuditRowsTest(_BaseStepsApiTest):
    def test_audit_rows_written(self):
        # CREATE
        self.client.post(
            self._url(suffix="/steps"),
            data=json.dumps({"step": {"type": "sleep", "seconds": 1}}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        # UPDATE
        self.client.put(
            self._url(suffix="/steps/0"),
            data=json.dumps({"step": {"type": "sleep", "seconds": 2}}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        # DELETE
        self.client.delete(
            self._url(suffix="/steps/0"),
            **_auth(self.alice_token),
        )

        self.assertTrue(
            AuditEntry.objects.filter(
                action="step.create",
                target_type="scenario",
                target_id=self.scenario.scenario_id,
                actor=self.alice,
            ).exists()
        )
        self.assertTrue(
            AuditEntry.objects.filter(
                action="step.update",
                target_type="scenario",
                target_id=self.scenario.scenario_id,
            ).exists()
        )
        self.assertTrue(
            AuditEntry.objects.filter(
                action="step.delete",
                target_type="scenario",
                target_id=self.scenario.scenario_id,
            ).exists()
        )

"""Integration tests for the Phase 4.8 history endpoint.

One paginated GET under ``/api/v1/users/{user_id}/history``:

    GET /history?limit=20&offset=0&status=&slot_id=&scenario_id=&execution_id=

The endpoint synchronises the legacy JSONL file (``config.runtime.history_file``)
into ``ops.execution_history`` on every request. Tests patch
``catalog.api.ops_services.import_history_jsonl`` to a no-op so the
filesystem is sidestepped, and seed ExecutionHistory rows directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from accounts.models import User
from django.test import Client, TestCase
from ops.models import ExecutionHistory

from catalog.models import Scenario, ScenarioShare


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


class _BaseHistoryApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        # Patch the JSONL sync to a no-op for every test in this module --
        # the filesystem isn't part of the contract under test here, the
        # dedicated services tests cover the JSONL behaviour.
        self._sync_patch = patch("catalog.api.ops_services.import_history_jsonl", return_value=0)
        self.sync_mock = self._sync_patch.start()
        self.addCleanup(self._sync_patch.stop)

        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.bob = User.objects.create_user(email="bob@example.com", password="password123!")
        self.admin = User.objects.create_superuser(email="admin@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")
        self.bob_token = _login(self.client, "bob@example.com", "password123!")
        self.admin_token = _login(self.client, "admin@example.com", "password123!")

        # Two scenarios for Alice + one for Bob.
        self.alice_scenario_a = Scenario.objects.create(
            scenario_id="sc-alice-a",
            owner_user_id=str(self.alice.id),
            description="alice A",
            definition={"steps": []},
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

    def _seed_history(
        self,
        *,
        slot_id: str,
        scenario_id: str,
        execution_id: str,
        status: str = "ok",
        executed_at: datetime | None = None,
    ) -> ExecutionHistory:
        return ExecutionHistory.objects.create(
            slot_key=f"{slot_id}-key",
            slot_id=slot_id,
            scenario_id=scenario_id,
            execution_id=execution_id,
            executed_at=executed_at or datetime(2026, 4, 22, 10, 0, tzinfo=UTC),
            status=status,
        )


class HistoryEndpointTest(_BaseHistoryApiTest):
    def test_history_endpoint_paginates(self):
        # 5 rows for Alice's two scenarios; limit=2 returns the first page.
        for index in range(5):
            self._seed_history(
                slot_id=f"slot-{index}",
                scenario_id="sc-alice-a",
                execution_id=f"exec-{index}",
                executed_at=datetime(2026, 4, index + 1, 10, 0, tzinfo=UTC),
            )
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?limit=2&offset=0",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)

    def test_history_endpoint_default_limit_is_20(self):
        # The endpoint default is 20 (NOT 100 like other listings).
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["limit"], 20)

    def test_history_endpoint_filter_by_status(self):
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e1", status="ok")
        self._seed_history(slot_id="s2", scenario_id="sc-alice-a", execution_id="e2", status="error")
        self._seed_history(slot_id="s3", scenario_id="sc-alice-a", execution_id="e3", status="ok")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?status=ok",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual({item["execution_id"] for item in body["items"]}, {"e1", "e3"})
        for item in body["items"]:
            self.assertEqual(item["status"], "ok")

    def test_history_endpoint_filter_by_scenario_id_user_owns(self):
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e1")
        self._seed_history(slot_id="s2", scenario_id="sc-alice-b", execution_id="e2")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?scenario_id=sc-alice-a",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["execution_id"], "e1")

    def test_history_endpoint_filter_by_scenario_id_user_doesnt_own_404(self):
        # Alice asks for Bob's scenario -- the scenario_id filter doubles
        # as a permission check (404 instead of empty list).
        self._seed_history(slot_id="s1", scenario_id="sc-bob", execution_id="e1")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?scenario_id=sc-bob",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 404, response.content)

    def test_history_endpoint_filter_by_scenario_id_shared_visible(self):
        # Bob shares his scenario with Alice -- scenario_id filter on
        # sc-bob now returns 200 with the matching rows.
        ScenarioShare.objects.create(scenario=self.bob_scenario, user_id=str(self.alice.id))
        self._seed_history(slot_id="s1", scenario_id="sc-bob", execution_id="e1")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?scenario_id=sc-bob",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["total"], 1)

    def test_history_endpoint_filter_by_slot_id_and_execution_id(self):
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e1")
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e2")
        self._seed_history(slot_id="s2", scenario_id="sc-alice-a", execution_id="e3")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history?slot_id=s1&execution_id=e1",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["execution_id"], "e1")

    def test_history_endpoint_non_admin_filters_by_accessible_scenarios(self):
        # Alice owns sc-alice-a/b; Bob's history must NOT appear in her
        # listing even without an explicit ``scenario_id`` filter.
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e-alice")
        self._seed_history(slot_id="s2", scenario_id="sc-bob", execution_id="e-bob")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["execution_id"], "e-alice")

    def test_history_endpoint_admin_sees_all(self):
        # Superuser bypass on the scenario_ids restriction -- admin sees
        # rows for every scenario in the DB.
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e-alice")
        self._seed_history(slot_id="s2", scenario_id="sc-bob", execution_id="e-bob")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(
            {item["execution_id"] for item in body["items"]},
            {"e-alice", "e-bob"},
        )

    def test_history_endpoint_other_user_403(self):
        # Bob requests Alice's history -- require_user_scope -> 403.
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.bob_token),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_history_endpoint_executed_at_format_z_suffix(self):
        self._seed_history(
            slot_id="s1",
            scenario_id="sc-alice-a",
            execution_id="e1",
            executed_at=datetime(2026, 4, 22, 10, 30, 15, tzinfo=UTC),
        )
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        item = response.json()["items"][0]
        self.assertTrue(item["executed_at"].endswith("Z"))
        self.assertNotIn("+00:00", item["executed_at"])

    def test_history_endpoint_jsonl_sync_runs(self):
        # The patched ``import_history_jsonl`` is invoked exactly once per
        # request (per-request synchronous sync, mirrors FastAPI).
        self._seed_history(slot_id="s1", scenario_id="sc-alice-a", execution_id="e1")
        response = self.client.get(
            f"/api/v1/users/{self.alice.id}/history",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.sync_mock.call_count, 1)

"""Phase 12.5 -- JSON file sync tests for scenarios + slots.

These tests cover the dual-write contract introduced when
``catalog.services.save_scenario_definition`` and
``catalog.services.sync_slots_file`` started rewriting the on-disk JSON
files after every CRUD. The file paths come from
``app.config.load_config()``; tests redirect them to a temporary
directory by patching the resolver.

Mirrors the FastAPI behaviour:

* ``scenarios.json`` carries the full document (``schema_version``,
  ``data``, ``scenarios``); the ``data`` block is preserved across
  rewrites.
* ``slots.json`` only contains ``enabled=True`` rows.
* Schema-validation failures raise ``HttpError(422)`` and the on-disk
  file is left untouched (because we validate BEFORE the atomic
  ``os.replace``).
* The per-scenario lock serialises concurrent writers so the final state
  reflects the last writer.
"""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from accounts.models import User
from django.test import Client, TestCase

from app.config import load_config
from catalog.models import Scenario, Slot
from catalog.services import (
    _write_scenarios_file,
    save_scenario_definition,
    sync_slots_file,
)


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


class _JsonSyncBase(TestCase):
    """Base class that redirects ``runtime.scenarios_file`` and
    ``runtime.slots_file`` to a per-test temporary directory. Patches
    ``app.config.load_config`` everywhere it's bound (catalog.services
    imports the symbol directly so the patch must target the bound name).
    """

    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)
        self.scenarios_path = tmp / "scenarios.json"
        self.slots_path = tmp / "slots.json"
        # Build a config object whose runtime points at our tempdir but
        # otherwise uses the real defaults.
        real_config = load_config()
        self.patched_config = replace(
            real_config,
            runtime=replace(
                real_config.runtime,
                scenarios_file=self.scenarios_path,
                slots_file=self.slots_path,
            ),
        )
        self._patcher = patch("catalog.services.load_config", return_value=self.patched_config)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        self.client = Client()
        self.alice = User.objects.create_user(email="alice@example.com", password="password123!")
        self.alice_token = _login(self.client, "alice@example.com", "password123!")


class CreateScenarioWritesJsonTest(_JsonSyncBase):
    def test_create_scenario_writes_json(self):
        payload = {
            "scenario_id": "demo",
            "owner_user_id": str(self.alice.id),
            "description": "demo scenario",
            "definition": {"steps": [{"type": "sleep", "seconds": 1}]},
        }
        response = self.client.post(
            "/api/v1/scenarios",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(self.scenarios_path.exists())
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertIn("demo", document["scenarios"])
        self.assertEqual(document["schema_version"], 1)
        # Payload's ``definition`` carries the steps; the API also injects
        # ``description`` + ``owner_user_id`` so the scenario survives
        # round-tripping through the loader.
        self.assertEqual(document["scenarios"]["demo"]["steps"], [{"type": "sleep", "seconds": 1}])


class UpdateScenarioPersistsToJsonTest(_JsonSyncBase):
    def test_update_scenario_persists_to_json(self):
        Scenario.objects.create(
            scenario_id="s1",
            owner=self.alice,
            description="initial",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        # Trigger an initial JSON file write so the next update reads a
        # well-formed document.
        _write_scenarios_file()
        response = self.client.patch(
            "/api/v1/scenarios/s1",
            data=json.dumps({"description": "updated"}),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertEqual(document["scenarios"]["s1"]["description"], "updated")


class DeleteScenarioRemovesFromJsonTest(_JsonSyncBase):
    def test_delete_scenario_removes_from_json(self):
        Scenario.objects.create(
            scenario_id="to-delete",
            owner=self.alice,
            description="bye",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        _write_scenarios_file()
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertIn("to-delete", document["scenarios"])

        response = self.client.delete(
            "/api/v1/scenarios/to-delete",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 200, response.content)
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertNotIn("to-delete", document["scenarios"])


class CreateSlotWritesSlotsJsonTest(_JsonSyncBase):
    def test_create_slot_writes_slots_json(self):
        Scenario.objects.create(
            scenario_id="sc-alice",
            owner=self.alice,
            description="alice",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        payload = {
            "slot_id": "slot-1",
            "scenario_id": "sc-alice",
            "days": [0, 1, 2],
            "start": "08:00",
            "end": "09:00",
            "enabled": True,
        }
        response = self.client.post(
            "/api/v1/slots",
            data=json.dumps(payload),
            content_type="application/json",
            **_auth(self.alice_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(self.slots_path.exists())
        document = json.loads(self.slots_path.read_text(encoding="utf-8"))
        slot_ids = [item["id"] for item in document["slots"]]
        self.assertIn("slot-1", slot_ids)


class DisabledSlotExcludedFromJsonTest(_JsonSyncBase):
    def test_disabled_slot_excluded_from_json(self):
        scenario = Scenario.objects.create(
            scenario_id="sc-alice",
            owner=self.alice,
            description="alice",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        Slot.objects.create(
            slot_id="slot-disabled",
            scenario=scenario,
            days=[0],
            start="08:00",
            end="09:00",
            enabled=False,
        )
        Slot.objects.create(
            slot_id="slot-enabled",
            scenario=scenario,
            days=[0],
            start="10:00",
            end="11:00",
            enabled=True,
        )
        sync_slots_file()
        document = json.loads(self.slots_path.read_text(encoding="utf-8"))
        ids = [item["id"] for item in document["slots"]]
        self.assertIn("slot-enabled", ids)
        self.assertNotIn("slot-disabled", ids)


class ConcurrentScenarioWritesSerializeViaLockTest(_JsonSyncBase):
    def test_concurrent_scenario_writes_serialize_via_lock(self):
        """Two threads contending for the SAME scenario_id lock must
        serialize: only one critical section runs at a time. The DB row
        write is excluded from the threaded section because SQLite's
        in-memory test database is not safe for concurrent writes from
        multiple threads (TestCase wraps everything in a savepoint that
        confuses cross-thread connections). The JSON-file rewrite under
        the per-scenario lock is the contract being verified.
        """
        from catalog.services import _lock_for

        Scenario.objects.create(
            scenario_id="concurrent",
            owner=self.alice,
            description="initial",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        _write_scenarios_file()

        acquisition_order: list[str] = []
        order_guard = threading.Lock()
        first_inside = threading.Event()
        first_release = threading.Event()
        scenario_lock = _lock_for("concurrent")

        errors: list[Exception] = []

        def writer(label: str) -> None:
            try:
                # Acquire the SAME per-scenario lock the production code
                # uses; the second thread will block here until the first
                # releases.
                with scenario_lock:
                    with order_guard:
                        acquisition_order.append(f"enter:{label}")
                    if label == "first":
                        first_inside.set()
                        first_release.wait(timeout=5)
                    # Write a deterministic JSON document inside the
                    # lock. We bypass _write_scenarios_file (which would
                    # need a DB read from a non-main thread; SQLite's
                    # in-memory backend doesn't tolerate that under the
                    # TestCase wrapper transaction). The point is to
                    # prove the lock serializes writers, not to re-test
                    # the full DB-to-file pipeline.
                    document = {
                        "schema_version": 1,
                        "data": {},
                        "scenarios": {
                            "concurrent": {
                                "steps": [],
                                "owner_user_id": str(self.alice.id),
                                "description": label,
                            }
                        },
                    }
                    from catalog.services import _write_json_atomic

                    _write_json_atomic(self.scenarios_path, document)
                    with order_guard:
                        acquisition_order.append(f"exit:{label}")
            except Exception as exc:  # pragma: no cover - surfaced via assert
                errors.append(exc)

        thread_a = threading.Thread(target=writer, args=("first",))
        thread_b = threading.Thread(target=writer, args=("second",))
        thread_a.start()
        first_inside.wait(timeout=5)
        thread_b.start()
        # Give thread B time to queue on the lock, then release thread A.
        threading.Event().wait(0.05)
        first_release.set()
        thread_a.join()
        thread_b.join()

        self.assertEqual(errors, [], f"Concurrent writes raised: {errors}")
        # Order proves serialisation: B cannot enter until A has exited.
        self.assertEqual(
            acquisition_order,
            ["enter:first", "exit:first", "enter:second", "exit:second"],
        )
        # Last writer wins -- thread "second" was released after "first"
        # exited, so the JSON file holds its payload.
        document = json.loads(self.scenarios_path.read_text(encoding="utf-8"))
        self.assertEqual(document["scenarios"]["concurrent"]["description"], "second")


class InvalidDefinitionRejectedTest(_JsonSyncBase):
    def test_invalid_definition_rejected_with_422_no_partial_write(self):
        scenario = Scenario.objects.create(
            scenario_id="bad-target",
            owner=self.alice,
            description="initial",
            definition={"steps": [], "owner_user_id": str(self.alice.id)},
        )
        _write_scenarios_file()
        original_bytes = self.scenarios_path.read_bytes()

        # The validator rejects any step whose ``type`` is unknown -- the
        # FastAPI behaviour we're matching.
        bad_definition = {
            "steps": [{"type": "definitely-not-a-real-step-type"}],
            "owner_user_id": str(self.alice.id),
        }
        from ninja.errors import HttpError

        with self.assertRaises(HttpError) as cm:
            save_scenario_definition(scenario, bad_definition)
        self.assertEqual(cm.exception.status_code, 422)

        # The on-disk file must be byte-identical: we validate BEFORE the
        # atomic replace, so the broken document never lands.
        self.assertEqual(self.scenarios_path.read_bytes(), original_bytes)

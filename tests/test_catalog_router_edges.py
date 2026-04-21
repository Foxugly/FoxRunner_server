from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers import catalog
from api.schemas import SharePayload, SlotUpdatePayload, StepPayload
from tests.helpers import fake_user


class CatalogRouterEdgeTests(unittest.TestCase):
    def test_scenario_mutation_and_share_endpoints_delegate_to_services(self):
        async def run():
            with (
                patch("api.routers.catalog.duplicate_owned_scenario", new=AsyncMock(return_value={"scenario_id": "copy"})) as duplicate,
                patch("api.routers.catalog.delete_owned_scenario", new=AsyncMock(return_value={"deleted": "scenario"})) as delete,
                patch("api.routers.catalog.get_scenario_for_user", new=AsyncMock(return_value=SimpleNamespace(definition={"steps": []}))) as get_scenario,
                patch("api.routers.catalog.list_scenario_shares", new=AsyncMock(return_value=["bob"])) as list_shares,
                patch("api.routers.catalog.share_owned_scenario", new=AsyncMock(return_value={"scenario_id": "scenario", "user_id": "bob"})) as share,
                patch("api.routers.catalog.unshare_owned_scenario", new=AsyncMock(return_value={"deleted": "bob"})) as unshare,
            ):
                current_user = fake_user("alice")
                duplicated = await catalog.duplicate_scenario_endpoint("scenario", "copy", config=object(), session=object(), current_user=current_user)
                deleted = await catalog.delete_scenario_endpoint("scenario", config=object(), session=object(), current_user=current_user)
                shares = await catalog.scenario_shares_endpoint("scenario", session=object(), current_user=current_user)
                shared = await catalog.share_scenario_endpoint("scenario", SharePayload(user_id="bob"), session=object(), current_user=current_user)
                unshared = await catalog.unshare_scenario_endpoint("scenario", "bob", session=object(), current_user=current_user)
                return duplicated, deleted, shares, shared, unshared, duplicate, delete, get_scenario, list_shares, share, unshare

        duplicated, deleted, shares, shared, unshared, duplicate, delete, get_scenario, list_shares, share, unshare = asyncio.run(run())

        self.assertEqual(duplicated["scenario_id"], "copy")
        self.assertEqual(deleted["deleted"], "scenario")
        self.assertEqual(shares, {"scenario_id": "scenario", "user_ids": ["bob"]})
        self.assertEqual(shared["user_id"], "bob")
        self.assertEqual(unshared["deleted"], "bob")
        duplicate.assert_awaited_once()
        delete.assert_awaited_once()
        get_scenario.assert_awaited_once()
        list_shares.assert_awaited_once()
        share.assert_awaited_once()
        unshare.assert_awaited_once()

    def test_slot_endpoints_cover_empty_filter_get_update_and_delete(self):
        async def run():
            slot = SimpleNamespace(slot_id="slot1", scenario_id="scenario", days=[0], start="08:00", end="09:00", enabled=True)
            with (
                patch("api.routers.catalog.list_accessible_slots", new=AsyncMock(return_value=([], 0))) as list_slots,
                patch("api.routers.catalog.get_scenario_for_user", new=AsyncMock(return_value=SimpleNamespace())) as get_scenario,
                patch("api.routers.catalog.get_slot", new=AsyncMock(return_value=slot)) as get_slot,
                patch("api.routers.catalog.update_owned_slot", new=AsyncMock(return_value={"slot_id": "slot1"})) as update_slot,
                patch("api.routers.catalog.delete_owned_slot", new=AsyncMock(return_value={"deleted": "slot1"})) as delete_slot,
            ):
                current_user = fake_user("alice")
                listed = await catalog.list_slots_endpoint(scenario_id="scenario", limit=5, offset=0, session=object(), current_user=current_user)
                fetched = await catalog.get_slot_endpoint("slot1", session=object(), current_user=current_user)
                updated = await catalog.update_slot_endpoint("slot1", SlotUpdatePayload(enabled=False), config=object(), session=object(), current_user=current_user)
                deleted = await catalog.delete_slot_endpoint("slot1", config=object(), session=object(), current_user=current_user)
                return listed, fetched, updated, deleted, list_slots, get_scenario, get_slot, update_slot, delete_slot

        listed, fetched, updated, deleted, list_slots, get_scenario, get_slot, update_slot, delete_slot = asyncio.run(run())

        self.assertEqual(listed["items"], [])
        self.assertEqual(fetched["slot_id"], "slot1")
        self.assertEqual(updated["slot_id"], "slot1")
        self.assertEqual(deleted["deleted"], "slot1")
        list_slots.assert_awaited_once()
        self.assertEqual(get_scenario.await_count, 2)
        get_slot.assert_awaited_once()
        update_slot.assert_awaited_once()
        delete_slot.assert_awaited_once()

    def test_user_plan_slots_scenario_steps_and_data_edges(self):
        async def run():
            scenario = SimpleNamespace(scenario_id="scenario", owner_user_id="alice", description="Demo", definition={"steps": [{"type": "sleep", "seconds": 1}]})
            service = SimpleNamespace(
                describe_plan_for_scenarios=Mock(side_effect=[RuntimeError("no plan"), {"scenario_id": "scenario"}]),
                run_scenario=Mock(return_value=2),
                run_next_for_scenarios=Mock(return_value=0),
            )
            with (
                patch("api.routers.catalog.scenario_ids_for_user", new=AsyncMock(side_effect=[set(), {"scenario"}, {"scenario"}, {"scenario"}, {"scenario"}])) as scenario_ids,
                patch("api.routers.catalog.timezone_for_user", new=AsyncMock(return_value="Europe/Brussels")),
                patch("api.routers.catalog.build_service_from_db", new=AsyncMock(return_value=service)) as build_service,
                patch(
                    "api.routers.catalog.list_accessible_slots",
                    new=AsyncMock(return_value=([SimpleNamespace(slot_id="slot1", scenario_id="scenario", days=[0], start="08:00", end="09:00", enabled=True)], 1)),
                ) as slots,
                patch("api.routers.catalog.get_scenario_for_user", new=AsyncMock(return_value=scenario)) as get_scenario,
                patch(
                    "api.routers.catalog.load_scenario_data",
                    return_value=SimpleNamespace(default_pushover_key="ops", default_network_key="office", pushovers={"ops": object()}, networks={"office": object()}),
                ) as load_data,
            ):
                current_user = fake_user("alice")
                with self.assertRaises(HTTPException) as no_plan:
                    await catalog.user_plan("alice", config=object(), session=object(), current_user=current_user)
                with self.assertRaises(HTTPException) as runtime_error:
                    await catalog.user_plan("alice", config=object(), session=object(), current_user=current_user)
                planned = await catalog.user_plan("alice", config=object(), session=object(), current_user=current_user)
                user_slots = await catalog.user_slots("alice", limit=10, offset=0, session=object(), current_user=current_user)
                detail = await catalog.user_scenario("alice", "scenario", session=object(), current_user=current_user)
                collections = await catalog.scenario_step_collections("alice", "scenario", session=object(), current_user=current_user)
                listed_steps = await catalog.list_scenario_steps("alice", "scenario", "steps", session=object(), current_user=current_user)
                first_step = await catalog.get_scenario_step("alice", "scenario", "steps", 0, session=object(), current_user=current_user)
                run_scenario = await catalog.run_user_scenario("alice", "scenario", dry_run=True, config=object(), session=object(), current_user=current_user)
                run_next = await catalog.run_user_next("alice", dry_run=True, config=object(), session=object(), current_user=current_user)
                scenario_data = await catalog.user_scenario_data(
                    "alice", session=object(), config=SimpleNamespace(runtime=SimpleNamespace(scenarios_file="scenarios.json")), current_user=current_user
                )
                return (
                    no_plan.exception,
                    runtime_error.exception,
                    planned,
                    user_slots,
                    detail,
                    collections,
                    listed_steps,
                    first_step,
                    run_scenario,
                    run_next,
                    scenario_data,
                    scenario_ids,
                    build_service,
                    slots,
                    get_scenario,
                    load_data,
                )

        (
            no_plan,
            runtime_error,
            planned,
            user_slots,
            detail,
            collections,
            listed_steps,
            first_step,
            run_scenario,
            run_next,
            scenario_data,
            scenario_ids,
            build_service,
            slots,
            get_scenario,
            load_data,
        ) = asyncio.run(run())

        self.assertEqual(no_plan.status_code, 404)
        self.assertEqual(runtime_error.status_code, 404)
        self.assertEqual(planned["scenario_id"], "scenario")
        self.assertEqual(user_slots["total"], 1)
        self.assertEqual(detail["definition"], {"steps": [{"type": "sleep", "seconds": 1}]})
        self.assertIn("steps", collections)
        self.assertEqual(listed_steps, [{"type": "sleep", "seconds": 1}])
        self.assertEqual(first_step, {"type": "sleep", "seconds": 1})
        self.assertEqual(run_scenario["exit_code"], 2)
        self.assertFalse(run_scenario["success"])
        self.assertTrue(run_next["success"])
        self.assertEqual(scenario_data["pushovers"], ["ops"])
        self.assertEqual(scenario_data["networks"], ["office"])
        self.assertGreaterEqual(scenario_ids.await_count, 4)
        self.assertGreaterEqual(build_service.await_count, 3)
        slots.assert_awaited_once()
        self.assertGreaterEqual(get_scenario.await_count, 5)
        load_data.assert_called_once()

    def test_step_mutation_endpoints_delegate_to_step_service(self):
        async def run():
            with (
                patch("api.routers.catalog.create_step", new=AsyncMock(return_value={"index": 0})) as create_step,
                patch("api.routers.catalog.update_step", new=AsyncMock(return_value={"index": 1})) as update_step,
                patch("api.routers.catalog.delete_step", new=AsyncMock(return_value={"deleted_index": 1})) as delete_step,
            ):
                current_user = fake_user("alice")
                payload = StepPayload(step={"type": "sleep", "seconds": 1})
                created = await catalog.create_scenario_step("alice", "scenario", "steps", payload, insert_at=0, config=object(), session=object(), current_user=current_user)
                updated = await catalog.update_scenario_step("alice", "scenario", "steps", 1, payload, config=object(), session=object(), current_user=current_user)
                deleted = await catalog.delete_scenario_step("alice", "scenario", "steps", 1, config=object(), session=object(), current_user=current_user)
                return created, updated, deleted, create_step, update_step, delete_step

        created, updated, deleted, create_step, update_step, delete_step = asyncio.run(run())

        self.assertEqual(created["index"], 0)
        self.assertEqual(updated["index"], 1)
        self.assertEqual(deleted["deleted_index"], 1)
        create_step.assert_awaited_once()
        update_step.assert_awaited_once()
        delete_step.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

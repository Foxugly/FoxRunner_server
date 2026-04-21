from __future__ import annotations

import asyncio
import unittest

from fastapi import HTTPException

from api.models import SlotRecord
from api.schemas import ScenarioPayload, ScenarioUpdatePayload
from api.services.scenarios import (
    create_owned_scenario,
    delete_owned_scenario,
    duplicate_owned_scenario,
    share_owned_scenario,
    unshare_owned_scenario,
    update_owned_scenario,
)
from tests.helpers import fake_user, temp_service_db


class ScenarioServiceTests(unittest.TestCase):
    def test_create_update_duplicate_share_unshare_delete_scenario(self):
        with temp_service_db() as (_, service, session_maker, _):

            async def run():
                async with session_maker() as session:
                    user = fake_user("alice")
                    created = await create_owned_scenario(
                        session,
                        payload=ScenarioPayload(scenario_id="new_scenario", owner_user_id="alice", description="New", definition={"description": "New", "steps": []}),
                        config=service.config,
                        current_user=user,
                    )
                    updated = await update_owned_scenario(
                        session,
                        scenario_id="new_scenario",
                        payload=ScenarioUpdatePayload(description="Updated"),
                        config=service.config,
                        current_user=user,
                    )
                    duplicated = await duplicate_owned_scenario(session, scenario_id="new_scenario", new_scenario_id="copy_scenario", config=service.config, current_user=user)
                    shared = await share_owned_scenario(session, scenario_id="new_scenario", share_user_id="bob", current_user=user)
                    unshared = await unshare_owned_scenario(session, scenario_id="new_scenario", share_user_id="bob", current_user=user)
                    deleted_copy = await delete_owned_scenario(session, scenario_id="copy_scenario", config=service.config, current_user=user)
                    return created, updated, duplicated, shared, unshared, deleted_copy

            created, updated, duplicated, shared, unshared, deleted_copy = asyncio.run(run())

        self.assertEqual(created["scenario_id"], "new_scenario")
        self.assertEqual(updated["description"], "Updated")
        self.assertEqual(duplicated["scenario_id"], "copy_scenario")
        self.assertEqual(shared["user_id"], "bob")
        self.assertEqual(unshared["deleted"], "bob")
        self.assertEqual(deleted_copy, {"deleted": "copy_scenario"})

    def test_delete_scenario_with_slot_is_rejected(self):
        with temp_service_db() as (_, service, session_maker, _):

            async def run():
                async with session_maker() as session:
                    session.add(SlotRecord(slot_id="blocking", scenario_id="bob_scenario", days=[0], start="08:00", end="08:15"))
                    await session.commit()
                    await delete_owned_scenario(session, scenario_id="bob_scenario", config=service.config, current_user=fake_user("bob"))

            with self.assertRaises(HTTPException):
                asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

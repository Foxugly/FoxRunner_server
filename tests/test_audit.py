from __future__ import annotations

import asyncio
import unittest

from api.audit import count_audit, list_audit, serialize_audit, write_audit
from tests.helpers import temp_service_db


class AuditServiceTests(unittest.TestCase):
    def test_write_audit_defaults_and_serializes_record(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    record = await write_audit(
                        session,
                        actor_user_id="admin@example.com",
                        action="scenario.create",
                        target_type="scenario",
                        target_id="scenario-1",
                    )
                    return serialize_audit(record)

            result = asyncio.run(run())

        self.assertEqual(result["actor_user_id"], "admin@example.com")
        self.assertEqual(result["action"], "scenario.create")
        self.assertEqual(result["target_type"], "scenario")
        self.assertEqual(result["target_id"], "scenario-1")
        self.assertEqual(result["before"], {})
        self.assertEqual(result["after"], {})
        self.assertIsNotNone(result["created_at"])

    def test_list_and_count_audit_support_filters_and_pagination(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    await write_audit(
                        session,
                        actor_user_id="alice@example.com",
                        action="scenario.create",
                        target_type="scenario",
                        target_id="scenario-1",
                        after={"name": "Scenario 1"},
                    )
                    await write_audit(
                        session,
                        actor_user_id="bob@example.com",
                        action="slot.update",
                        target_type="slot",
                        target_id="slot-1",
                        before={"enabled": False},
                        after={"enabled": True},
                    )
                    await write_audit(
                        session,
                        actor_user_id="alice@example.com",
                        action="scenario.delete",
                        target_type="scenario",
                        target_id="scenario-2",
                    )

                    all_items = await list_audit(session, limit=10, offset=0)
                    paged_items = await list_audit(session, limit=1, offset=1)
                    alice_items = await list_audit(session, actor_user_id="alice@example.com")
                    scenario_items = await list_audit(session, target_type="scenario")
                    exact_items = await list_audit(session, target_id="slot-1")
                    exact_count = await count_audit(session, actor_user_id="bob@example.com", target_type="slot", target_id="slot-1")
                    missing_count = await count_audit(session, target_id="missing")
                    return all_items, paged_items, alice_items, scenario_items, exact_items, exact_count, missing_count

            all_items, paged_items, alice_items, scenario_items, exact_items, exact_count, missing_count = asyncio.run(run())

        self.assertEqual([item.target_id for item in all_items], ["scenario-2", "slot-1", "scenario-1"])
        self.assertEqual([item.target_id for item in paged_items], ["slot-1"])
        self.assertEqual({item.target_id for item in alice_items}, {"scenario-1", "scenario-2"})
        self.assertEqual({item.target_id for item in scenario_items}, {"scenario-1", "scenario-2"})
        self.assertEqual(exact_items[0].before, {"enabled": False})
        self.assertEqual(exact_items[0].after, {"enabled": True})
        self.assertEqual(exact_count, 1)
        self.assertEqual(missing_count, 0)


if __name__ == "__main__":
    unittest.main()

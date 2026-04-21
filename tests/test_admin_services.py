from __future__ import annotations

import asyncio
import unittest

from api.models import User
from api.schemas import AdminUserUpdatePayload
from api.services.admin import db_stats, export_catalog, import_catalog, prune_records, remove_setting, save_setting, update_user
from tests.helpers import fake_user, temp_service_db


class AdminServiceTests(unittest.TestCase):
    def test_db_stats_counts_catalog_records(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    return await db_stats(session)

            result = asyncio.run(run())

        self.assertEqual(result["tables"]["scenarios"], 2)
        self.assertEqual(result["tables"]["slots"], 1)

    def test_export_catalog_returns_scenarios_and_slots(self):
        with temp_service_db() as (_, service, session_maker, _):

            async def run():
                async with session_maker() as session:
                    return await export_catalog(session, config=service.config)

            result = asyncio.run(run())

        self.assertIn("alice_scenario", result["scenarios"]["scenarios"])
        self.assertEqual(result["slots"]["slots"][0]["id"], "alice_slot")

    def test_update_user_updates_timezone_and_flags(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    user = User(email="alice@example.com", hashed_password="x", is_active=True, is_superuser=False, is_verified=False)
                    session.add(user)
                    await session.commit()
                    return await update_user(
                        session,
                        target_user_id="alice@example.com",
                        payload=AdminUserUpdatePayload(is_verified=True, timezone_name="America/New_York"),
                        current_user=fake_user("admin@example.com", superuser=True),
                    )

            result = asyncio.run(run())

        self.assertTrue(result["is_verified"])
        self.assertEqual(result["timezone_name"], "America/New_York")

    def test_import_catalog_dry_run_and_apply(self):
        payload = {
            "scenarios": {"schema_version": 1, "data": {}, "scenarios": {"imported": {"user_id": "alice", "description": "Imported", "steps": []}}},
            "slots": {"slots": [{"id": "imported_slot", "days": [0], "start": "08:00", "end": "08:15", "scenario": "imported"}]},
        }
        with temp_service_db() as (_, service, session_maker, _):

            async def run():
                async with session_maker() as session:
                    dry = await import_catalog(session, payload=payload, dry_run=True, config=service.config, current_user=fake_user("admin", superuser=True))
                    applied = await import_catalog(session, payload=payload, dry_run=False, config=service.config, current_user=fake_user("admin", superuser=True))
                    stats = await db_stats(session)
                    return dry, applied, stats

            dry, applied, stats = asyncio.run(run())

        self.assertEqual(dry["scenarios"], 1)
        self.assertTrue(applied["imported"])
        self.assertEqual(stats["tables"]["scenarios"], 1)

    def test_settings_save_delete_and_prune_records(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    saved = await save_setting(session, key="feature.demo", value={"enabled": True}, description="Demo", current_user=fake_user("admin", superuser=True))
                    deleted = await remove_setting(session, key="feature.demo", current_user=fake_user("admin", superuser=True))
                    pruned = await prune_records(session, jobs_days=1, audit_days=1, graph_notifications_days=1, current_user=fake_user("admin", superuser=True))
                    return saved, deleted, pruned

            saved, deleted, pruned = asyncio.run(run())

        self.assertEqual(saved["key"], "feature.demo")
        self.assertEqual(deleted, {"deleted": "feature.demo"})
        self.assertIn("jobs", pruned["removed"])


if __name__ == "__main__":
    unittest.main()

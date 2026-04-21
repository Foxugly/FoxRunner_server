from __future__ import annotations

import asyncio
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from api.models import GraphSubscriptionRecord, JobRecord
from api.tasks import _prune_retention_task, _renew_graph_subscriptions_task, _run_scenario_job
from api.time_utils import utc_now_naive
from tests.helpers import build_service, setup_test_db


class TaskTests(unittest.TestCase):
    def test_renew_graph_subscriptions_disabled(self):
        previous = os.environ.get("GRAPH_SUBSCRIPTION_RENEW_ENABLED")
        os.environ["GRAPH_SUBSCRIPTION_RENEW_ENABLED"] = "false"
        try:
            result = asyncio.run(_renew_graph_subscriptions_task())
        finally:
            if previous is None:
                os.environ.pop("GRAPH_SUBSCRIPTION_RENEW_ENABLED", None)
            else:
                os.environ["GRAPH_SUBSCRIPTION_RENEW_ENABLED"] = previous

        self.assertEqual(result, {"enabled": False, "renewed": 0})

    def test_renew_graph_subscriptions_updates_expiring_records(self):
        with (
            patch("api.tasks.is_graph_configured", return_value=True),
            patch("api.tasks.renew_graph_subscription", return_value={"expirationDateTime": "2026-04-23T10:00:00Z"}),
            self._patched_task_db() as (_, session_maker),
        ):

            async def seed():
                async with session_maker() as session:
                    session.add(
                        GraphSubscriptionRecord(
                            subscription_id="sub1",
                            resource="users/a/messages",
                            change_type="created",
                            notification_url="https://example.com/webhook",
                            expiration_datetime=utc_now_naive() + timedelta(hours=1),
                        )
                    )
                    await session.commit()

            asyncio.run(seed())
            result = asyncio.run(_renew_graph_subscriptions_task())

        self.assertEqual(result["renewed"], 1)
        self.assertEqual(result["errors"], [])

    def test_prune_retention_task_disabled(self):
        previous = os.environ.get("RETENTION_PRUNE_ENABLED")
        os.environ["RETENTION_PRUNE_ENABLED"] = "false"
        try:
            result = asyncio.run(_prune_retention_task())
        finally:
            if previous is None:
                os.environ.pop("RETENTION_PRUNE_ENABLED", None)
            else:
                os.environ["RETENTION_PRUNE_ENABLED"] = previous

        self.assertEqual(result, {"enabled": False})

    def test_run_scenario_job_marks_failed_on_exception(self):
        with self._patched_task_db() as (_, session_maker):

            async def seed():
                async with session_maker() as session:
                    session.add(JobRecord(job_id="job1", kind="scenario", user_id="alice", target_id="alice_scenario", status="queued", dry_run=True))
                    await session.commit()

            asyncio.run(seed())
            with patch("api.tasks.load_config", side_effect=RuntimeError("boom")), self.assertRaises(RuntimeError):
                asyncio.run(_run_scenario_job("job1", "alice_scenario", True))

            async def read_status():
                async with session_maker() as session:
                    return await session.get(JobRecord, 1)

            record = asyncio.run(read_status())

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error, "boom")

    class _patched_task_db:
        def __enter__(self):
            from contextlib import ExitStack

            self.stack = ExitStack()
            self.tmp = self.stack.enter_context(__import__("tempfile").TemporaryDirectory())
            self.service = build_service(self.tmp)
            self.session_maker, self.engine = setup_test_db(self.tmp, self.service)
            import api.tasks

            self.stack.enter_context(patch.object(api.tasks, "async_session_maker", self.session_maker))
            return self.service, self.session_maker

        def __exit__(self, exc_type, exc, tb):
            asyncio.run(self.engine.dispose())
            return self.stack.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()

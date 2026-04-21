from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from api import db as api_db
from api.graph import (
    create_graph_subscription,
    delete_graph_subscription,
    get_graph_access_token,
    get_graph_access_token_async,
    save_graph_notifications,
    save_graph_subscription,
    send_graph_mail,
)
from api.models import GraphNotificationRecord, GraphSubscriptionRecord, JobRecord
from api.routers import graph as graph_router
from api.schemas import GraphRenewPayload, GraphSubscriptionPayload
from api.tasks import (
    _get_job,
    _optional_int,
    _prune_retention_task,
    _renew_graph_subscriptions_task,
    _run_scenario_job,
    prune_retention_task,
    renew_graph_subscriptions_task,
    run_scenario_job,
)
from api.timezones import validate_timezone_name
from state.store import (
    ExecutionStateStore,
    HistoryStore,
    LastRunStore,
    NextExecutionStore,
    ProcessLock,
    _isoformat,
    _parse_datetime,
)
from tests.helpers import fake_user, temp_service_db


class GraphTaskStoreEdgeTests(unittest.TestCase):
    def test_graph_tokens_mail_and_subscription_http_calls(self):
        response = MagicMock()
        response.json.return_value = {"access_token": "token", "id": "sub1"}
        response.raise_for_status.return_value = None

        with (
            patch.multiple("api.graph", GRAPH_TENANT_ID="tenant", GRAPH_CLIENT_ID="client", GRAPH_CLIENT_SECRET="secret", GRAPH_MAIL_SENDER="sender@example.com"),
            patch("api.graph.httpx.post", return_value=response) as post,
        ):
            self.assertEqual(get_graph_access_token(), "token")
            send_graph_mail(to="to@example.com", subject="Subject", body="Body")
        self.assertGreaterEqual(post.call_count, 2)

        class AsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, *args, **kwargs):
                return response

            async def patch(self, *args, **kwargs):
                return response

            async def delete(self, *args, **kwargs):
                return response

        with (
            patch.multiple("api.graph", GRAPH_TENANT_ID="tenant", GRAPH_CLIENT_ID="client", GRAPH_CLIENT_SECRET="secret"),
            patch("api.graph.httpx.AsyncClient", return_value=AsyncClient()),
        ):
            self.assertEqual(asyncio.run(get_graph_access_token_async()), "token")
            created = asyncio.run(
                create_graph_subscription(
                    resource="users/a/messages",
                    change_type="created",
                    notification_url="https://example.com/webhook",
                    expiration_datetime="2026-04-23T10:00:00Z",
                    lifecycle_notification_url="https://example.com/lifecycle",
                )
            )
            self.assertEqual(created["id"], "sub1")
            self.assertEqual(asyncio.run(delete_graph_subscription("sub1")), None)

        with patch.multiple("api.graph", GRAPH_MAIL_SENDER=""), self.assertRaises(RuntimeError):
            send_graph_mail(to="to@example.com", subject="Subject", body="Body")

    def test_save_graph_subscription_and_notifications(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run():
                async with session_maker() as session:
                    record = await save_graph_subscription(
                        session,
                        subscription={"id": "sub1", "clientState": "secret", "expirationDateTime": "2026-04-23T10:00:00Z"},
                        resource="users/a/messages",
                        change_type="created",
                        notification_url="https://example.com/webhook",
                        lifecycle_notification_url=None,
                    )
                    first = await save_graph_notifications(
                        session,
                        {
                            "value": [
                                {"subscriptionId": "sub1", "changeType": "created", "resource": "users/a/messages/1", "clientState": "state"},
                                "ignored",
                            ]
                        },
                    )
                    duplicate = await save_graph_notifications(
                        session,
                        {"value": [{"subscriptionId": "sub1", "changeType": "created", "resource": "users/a/messages/1", "clientState": "state"}]},
                    )
                    lifecycle = await save_graph_notifications(
                        session,
                        {"value": [{"subscriptionId": "sub1", "lifecycleEvent": "reauthorizationRequired", "resource": "users/a/messages", "clientState": "state"}]},
                        lifecycle=True,
                    )
                    return record, first, duplicate, lifecycle, await session.get(GraphNotificationRecord, 1)

            with patch.multiple("api.graph", GRAPH_WEBHOOK_CLIENT_STATE="state"), patch.dict(os.environ, {"GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION": "false"}, clear=False):
                record, first, duplicate, lifecycle, notification = asyncio.run(run())

        self.assertEqual(record.subscription_id, "sub1")
        self.assertEqual(first, 1)
        self.assertEqual(duplicate, 0)
        self.assertEqual(lifecycle, 1)
        self.assertEqual(notification.raw_payload["clientState"], "***redacted***")

    def test_graph_notification_error_paths(self):
        with temp_service_db() as (_, _, session_maker, _):

            async def run_invalid_payload():
                async with session_maker() as session:
                    await save_graph_notifications(session, {"value": "bad"})

            async def run_bad_state():
                async with session_maker() as session:
                    await save_graph_notifications(session, {"value": [{"subscriptionId": "sub1", "clientState": "bad"}]})

            with self.assertRaises(HTTPException):
                asyncio.run(run_invalid_payload())
            with patch.multiple("api.graph", GRAPH_WEBHOOK_CLIENT_STATE="expected"), self.assertRaises(HTTPException):
                asyncio.run(run_bad_state())

            async def unknown():
                async with session_maker() as session:
                    await save_graph_notifications(session, {"value": [{"subscriptionId": "missing", "clientState": ""}]})

            with (
                patch.multiple("api.graph", GRAPH_WEBHOOK_CLIENT_STATE=""),
                patch.dict(os.environ, {"GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION": "true"}, clear=False),
                self.assertRaises(HTTPException),
            ):
                asyncio.run(unknown())

    def test_graph_router_endpoints_delegate_and_validate_tokens(self):
        async def run():
            user = fake_user("admin", superuser=True)
            with (
                patch("api.routers.graph.create_graph_subscription_service", new=AsyncMock(return_value={"subscription_id": "sub1"})) as create_service,
                patch("api.routers.graph.list_subscriptions", new=AsyncMock(return_value=([{"subscription_id": "sub1"}], 1))) as list_subs,
                patch("api.routers.graph.renew_subscription", new=AsyncMock(return_value={"subscription_id": "sub1"})) as renew,
                patch("api.routers.graph.delete_subscription", new=AsyncMock(return_value={"deleted": "sub1"})) as delete,
                patch("api.routers.graph.list_notifications", new=AsyncMock(return_value=([{"resource": "r"}], 1))) as list_notifications,
                patch("api.routers.graph.save_graph_notifications", new=AsyncMock(return_value=2)) as save_notifications,
            ):
                created = await graph_router.create_subscription(
                    GraphSubscriptionPayload(
                        resource="users/a/messages",
                        change_type="created",
                        notification_url="https://example.com/webhook",
                        expiration_datetime="2026-04-23T10:00:00Z",
                    ),
                    session=object(),
                    current_user=user,
                )
                listed = await graph_router.graph_subscriptions_endpoint(limit=10, offset=0, session=object(), current_user=user)
                renewed = await graph_router.renew_subscription_endpoint("sub1", GraphRenewPayload(expiration_datetime="2026-04-24T10:00:00Z"), session=object(), current_user=user)
                deleted = await graph_router.delete_subscription_endpoint("sub1", session=object(), current_user=user)
                notifications = await graph_router.graph_notifications_endpoint(limit=10, offset=0, subscription_id="sub1", session=object(), current_user=user)
                webhook_token = await graph_router.graph_webhook(validation_token="token", session=object())
                lifecycle_token = await graph_router.graph_lifecycle_webhook(validation_token="life", session=object())
                webhook = await graph_router.graph_webhook({"value": []}, validation_token=None, session=object())
                lifecycle = await graph_router.graph_lifecycle_webhook({"value": []}, validation_token=None, session=object())
                return (
                    created,
                    listed,
                    renewed,
                    deleted,
                    notifications,
                    webhook_token,
                    lifecycle_token,
                    webhook,
                    lifecycle,
                    create_service,
                    list_subs,
                    renew,
                    delete,
                    list_notifications,
                    save_notifications,
                )

        (
            created,
            listed,
            renewed,
            deleted,
            notifications,
            webhook_token,
            lifecycle_token,
            webhook,
            lifecycle,
            create_service,
            list_subs,
            renew,
            delete,
            list_notifications,
            save_notifications,
        ) = asyncio.run(run())

        self.assertEqual(created["subscription_id"], "sub1")
        self.assertEqual(listed["total"], 1)
        self.assertEqual(renewed["subscription_id"], "sub1")
        self.assertEqual(deleted["deleted"], "sub1")
        self.assertEqual(notifications["total"], 1)
        self.assertIsInstance(webhook_token, PlainTextResponse)
        self.assertIsInstance(lifecycle_token, PlainTextResponse)
        self.assertEqual(webhook, {"accepted": 2})
        self.assertEqual(lifecycle, {"accepted": 2})
        create_service.assert_awaited_once()
        list_subs.assert_awaited_once()
        renew.assert_awaited_once()
        delete.assert_awaited_once()
        list_notifications.assert_awaited_once()
        self.assertEqual(save_notifications.await_count, 2)

    def test_db_session_generator_and_create_tables(self):
        class Session:
            async def __aenter__(self):
                return "session"

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def run_session():
            with patch("api.db.async_session_maker", return_value=Session()):
                generator = api_db.get_async_session()
                value = await anext(generator)
                await generator.aclose()
                return value

        self.assertEqual(asyncio.run(run_session()), "session")

        conn = MagicMock()
        conn.run_sync = AsyncMock()

        class Begin:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with patch("api.db.engine", SimpleNamespace(begin=MagicMock(return_value=Begin()))):
            asyncio.run(api_db.create_db_and_tables())
        conn.run_sync.assert_awaited_once()

    def test_task_wrappers_success_error_and_retention_paths(self):
        task_result = object()
        with (
            patch("api.tasks._run_scenario_job", new=MagicMock(return_value=task_result)),
            patch("api.tasks._renew_graph_subscriptions_task", new=MagicMock(return_value=task_result)),
            patch("api.tasks._prune_retention_task", new=MagicMock(return_value=task_result)),
            patch("api.tasks.asyncio.run", return_value={"ok": True}) as run,
        ):
            self.assertEqual(run_scenario_job("job", "scenario", True), {"ok": True})
            self.assertEqual(renew_graph_subscriptions_task(), {"ok": True})
            self.assertEqual(prune_retention_task(), {"ok": True})
        self.assertEqual(run.call_count, 3)

        self.assertIsNone(_optional_int("MISSING_INT"))
        with patch.dict(os.environ, {"EMPTY_INT": ""}, clear=False):
            self.assertIsNone(_optional_int("EMPTY_INT"))
        with patch.dict(os.environ, {"VALUE_INT": "12"}, clear=False):
            self.assertEqual(_optional_int("VALUE_INT"), 12)

        with temp_service_db() as (_, _, session_maker, _):

            async def missing_job():
                async with session_maker() as session:
                    await _get_job(session, "missing")

            with self.assertRaises(RuntimeError):
                asyncio.run(missing_job())

        with patch("api.tasks.is_graph_configured", return_value=False), patch.dict(os.environ, {"GRAPH_SUBSCRIPTION_RENEW_ENABLED": "true"}, clear=False):
            self.assertEqual(asyncio.run(_renew_graph_subscriptions_task()), {"enabled": True, "configured": False, "renewed": 0})

        with patch("api.tasks.is_graph_configured", return_value=True), patch("api.tasks.renew_graph_subscription", side_effect=RuntimeError("graph down")):
            with temp_service_db() as (_, _, session_maker, _):

                async def seed():
                    async with session_maker() as session:
                        session.add(
                            GraphSubscriptionRecord(
                                subscription_id="sub-error",
                                resource="users/a/messages",
                                change_type="created",
                                notification_url="https://example.com/webhook",
                                expiration_datetime=datetime.now(UTC).replace(tzinfo=None),
                            )
                        )
                        await session.commit()

                asyncio.run(seed())
                with patch.object(__import__("api.tasks").tasks, "async_session_maker", session_maker):
                    result = asyncio.run(_renew_graph_subscriptions_task())
            self.assertEqual(result["renewed"], 0)
            self.assertEqual(result["errors"][0]["subscription_id"], "sub-error")

    def test_run_scenario_job_success_and_prune_enabled(self):
        with temp_service_db() as (_, service, session_maker, _):

            async def seed_job():
                async with session_maker() as session:
                    session.add(JobRecord(job_id="job-success", kind="scenario", user_id="alice", target_id="alice_scenario", status="queued", dry_run=True))
                    await session.commit()

            asyncio.run(seed_job())
            runtime_service = SimpleNamespace(run_scenario=MagicMock(return_value=0))
            with (
                patch.object(__import__("api.tasks").tasks, "async_session_maker", session_maker),
                patch("api.tasks.load_config", return_value=service.config),
                patch("api.tasks.load_scheduler_catalog", new=AsyncMock(return_value=(service.slots, service.scenarios))),
                patch("api.tasks.build_runtime_services_from_catalog", return_value=runtime_service),
            ):
                self.assertEqual(asyncio.run(_run_scenario_job("job-success", "alice_scenario", True)), {"job_id": "job-success", "exit_code": 0})

        with (
            patch.dict(
                os.environ,
                {
                    "RETENTION_PRUNE_ENABLED": "true",
                    "RETENTION_JOBS_DAYS": "1",
                    "RETENTION_AUDIT_DAYS": "",
                    "RETENTION_GRAPH_NOTIFICATIONS_DAYS": "",
                    "RETENTION_ARTIFACTS_DAYS": "1",
                },
                clear=False,
            ),
            patch("api.tasks.prune_database_records", new=AsyncMock(return_value={"jobs": 1, "job_events": 0, "audit": 0, "graph_notifications": 0})),
            patch("api.tasks.load_config", return_value=SimpleNamespace(runtime=SimpleNamespace(artifacts_dir=Path(".")))),
            patch("api.tasks.prune_artifacts", return_value=3),
        ):
            with temp_service_db() as (_, _, session_maker, _), patch.object(__import__("api.tasks").tasks, "async_session_maker", session_maker):
                result = asyncio.run(_prune_retention_task())
            self.assertEqual(result["removed"]["jobs"], 1)
            self.assertEqual(result["removed"]["artifacts"], 3)

    def test_timezone_validation_and_state_store_edges(self):
        self.assertEqual(validate_timezone_name(None), "Europe/Brussels")
        self.assertEqual(validate_timezone_name("   "), "Europe/Brussels")
        with self.assertRaises(ValueError):
            validate_timezone_name("Invalid/Timezone")

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            state = ExecutionStateStore(base / "executions.json")
            self.assertFalse(state.has_executed("2099-01-01|slot|00:00-01:00"))
            (base / "executions.json").write_text("{bad", encoding="utf-8")
            self.assertFalse(state.has_executed("x"))
            state.mark_executed("1999-01-01|old|00:00-01:00", datetime(1999, 1, 1, tzinfo=UTC))
            state.mark_executed("2099-01-01|new|00:00-01:00", datetime(2099, 1, 1, tzinfo=UTC))
            self.assertTrue(state.has_executed("2099-01-01|new|00:00-01:00"))

            next_store = NextExecutionStore(base / "next.json")
            next_store.save("slot", datetime(2026, 1, 1), "planned", details="details")
            self.assertEqual(json.loads((base / "next.json").read_text(encoding="utf-8"))["details"], "details")
            next_store.clear()
            next_store.clear()

            last = LastRunStore(base / "last.json")
            last.save(slot_key="slot", executed_at=datetime(2026, 1, 1), status="ok", step="step", message="message")
            self.assertEqual(json.loads((base / "last.json").read_text(encoding="utf-8"))["slot_key"], "slot")

            history = HistoryStore(base / "history.jsonl")
            self.assertEqual(history.read(), [])
            with self.assertRaises(ValueError):
                history.prune(older_than_days=-1)
            self.assertEqual(history.prune(older_than_days=1), 0)
            self.assertEqual(_isoformat(datetime(2026, 1, 1)), "2026-01-01T00:00:00Z")
            self.assertEqual(_parse_datetime("2026-01-01T00:00:00").tzinfo, UTC)

    def test_process_lock_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "process.lock"
            lock = ProcessLock(lock_path, stale_seconds=0)
            self.assertTrue(lock.acquire())
            self.assertFalse(ProcessLock(lock_path, stale_seconds=999).acquire())
            lock.release()
            lock.release()

            lock_path.write_text("{bad", encoding="utf-8")
            invalid_lock = ProcessLock(lock_path, stale_seconds=999)
            self.assertTrue(invalid_lock.acquire())
            invalid_lock.release()

            lock_path.write_text(json.dumps({"pid": -1, "created_at": time.time()}), encoding="utf-8")
            recovered = ProcessLock(lock_path, stale_seconds=999)
            self.assertTrue(recovered.acquire())
            recovered.release()

            self.assertFalse(ProcessLock._pid_exists(-1))
            with patch("state.store.os.name", "posix"):
                with patch("state.store.os.kill", side_effect=OSError):
                    self.assertFalse(ProcessLock._pid_exists(12345))
                with patch("state.store.os.kill", return_value=None):
                    self.assertTrue(ProcessLock._pid_exists(12345))


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_LOG_HTTP_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth import current_active_user
from api.catalog import seed_catalog_from_json
from api.db import Base, get_async_session
from api.main import app, create_app, get_config, get_service
from app.config import AppConfig, NetworkConfig, RuntimeConfig, TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from scenarios.loader import ScenarioData, ScenarioDefinition
from scheduler.model import TimeSlot
from scheduler.service import SchedulerService


class ApiTests(unittest.TestCase):
    def test_openapi_contract_exposes_only_versioned_routes_and_typed_pages(self):
        client = TestClient(app)
        payload = client.get("/openapi.json").json()
        paths = payload["paths"]
        self.assertIn("/api/v1/health", paths)
        self.assertIn("/api/v1/users/{user_id}/history", paths)
        self.assertNotIn("/health", paths)
        schemas = payload["components"]["schemas"]
        for schema in ["ScenarioPagePayload", "SlotPagePayload", "JobPagePayload", "HistoryPagePayload"]:
            self.assertIn(schema, schemas)
        self.assertIn("timezone_name", schemas["UserRead"]["properties"])
        self.assertIn("timezone_name", schemas["UserCreate"]["properties"])
        self.assertIn("timezone_name", schemas["UserUpdate"]["properties"])
        self.assertIn('"format": "date-time"', json.dumps(schemas["JobPayload"]["properties"]["created_at"]))

    def test_legacy_routes_can_be_disabled(self):
        previous = os.environ.get("API_ENABLE_LEGACY_ROUTES")
        os.environ["API_ENABLE_LEGACY_ROUTES"] = "false"
        try:
            isolated_app = create_app()
            client = TestClient(isolated_app)
            legacy = client.get("/health")
            versioned = client.get("/api/v1/health")
        finally:
            if previous is None:
                os.environ.pop("API_ENABLE_LEGACY_ROUTES", None)
            else:
                os.environ["API_ENABLE_LEGACY_ROUTES"] = previous

        self.assertEqual(legacy.status_code, 404)
        self.assertEqual(versioned.status_code, 200)
        self.assertIn("X-Request-ID", versioned.headers)

    def test_request_id_header_is_preserved(self):
        client = TestClient(app)
        response = client.get("/api/v1/health", headers={"X-Request-ID": "request-123"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "request-123")

    def test_common_timezones_endpoint_exposes_frontend_choices(self):
        client = TestClient(app)
        response = client.get("/api/v1/timezones/common")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["default_timezone"], "Europe/Brussels")
        self.assertIn("America/New_York", response.json()["timezones"])

    def test_user_model_rejects_invalid_timezone_assignment(self):
        from api.models import User

        with self.assertRaises(ValueError):
            User(
                email="alice@example.com",
                hashed_password="unused",
                is_active=True,
                is_superuser=False,
                is_verified=True,
                timezone_name="Invalid/Timezone",
            )

    def test_graph_expiration_requires_utc_datetime(self):
        from pydantic import ValidationError

        from api.schemas import GraphRenewPayload, GraphSubscriptionPayload

        with self.assertRaises(ValidationError):
            GraphSubscriptionPayload(
                resource="users/alice/messages",
                change_type="created",
                notification_url="https://example.com/webhook",
                expiration_datetime="2026-04-22T10:00:00+02:00",
            )
        with self.assertRaises(ValidationError):
            GraphRenewPayload(expiration_datetime="2026-04-22T10:00:00")

    def test_security_headers_are_present(self):
        client = TestClient(app)
        response = client.get("/api/v1/health")

        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_cors_allows_configured_origin_only(self):
        previous = os.environ.get("API_CORS_ORIGINS")
        os.environ["API_CORS_ORIGINS"] = "https://app.example.com"
        try:
            isolated_app = create_app()
            client = TestClient(isolated_app)
            allowed = client.get("/api/v1/health", headers={"Origin": "https://app.example.com"})
            denied = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
        finally:
            if previous is None:
                os.environ.pop("API_CORS_ORIGINS", None)
            else:
                os.environ["API_CORS_ORIGINS"] = previous

        self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "https://app.example.com")
        self.assertIsNone(denied.headers.get("Access-Control-Allow-Origin"))

    def test_payload_too_large_is_rejected(self):
        previous = os.environ.get("API_MAX_BODY_BYTES")
        os.environ["API_MAX_BODY_BYTES"] = "10"
        try:
            client = TestClient(app)
            response = client.post("/api/v1/graph/webhook", content="x" * 20, headers={"content-length": "20"})
        finally:
            if previous is None:
                os.environ.pop("API_MAX_BODY_BYTES", None)
            else:
                os.environ["API_MAX_BODY_BYTES"] = previous

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["code"], "payload_too_large")

    def test_secret_redaction_masks_sensitive_error_text(self):
        from api.redaction import redact, redact_text

        self.assertEqual(redact_text("AUTH_SECRET leaked"), "***redacted***")
        self.assertEqual(redact({"GRAPH_CLIENT_SECRET": "x", "safe": "ok"}), {"GRAPH_CLIENT_SECRET": "***redacted***", "safe": "ok"})

    def test_user_scenarios_returns_owned_scenarios(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_service():
                return service

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_service] = override_service
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.get("/users/alice/scenarios")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            payload = response.json()["items"]
            self.assertEqual([item["scenario_id"] for item in payload], ["alice_scenario"])

    def test_user_plan_uses_target_user_timezone(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def seed_user():
                from api.models import User

                async with session_maker() as session:
                    session.add(
                        User(
                            email="alice",
                            hashed_password="unused",
                            is_active=True,
                            is_superuser=False,
                            is_verified=True,
                            timezone_name="America/New_York",
                        )
                    )
                    await session.commit()

            asyncio.run(seed_user())

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                response = client.get("/api/v1/users/alice/plan")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["timezone"], "America/New_York")

    def test_versioned_api_routes_are_available(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                health = client.get("/api/v1/health")
                scenarios = client.get("/api/v1/users/alice/scenarios")
                openapi = client.get("/openapi.json")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            self.assertEqual(scenarios.status_code, 200)
            self.assertEqual([item["scenario_id"] for item in scenarios.json()["items"]], ["alice_scenario"])
            self.assertIn("/api/v1/health", openapi.json()["paths"])
            self.assertNotIn("/health", openapi.json()["paths"])

    def test_user_scenarios_pagination_uses_total(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                response = client.get("/api/v1/users/admin/scenarios?limit=1&offset=1")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["limit"], 1)
            self.assertEqual(response.json()["offset"], 1)
            self.assertEqual(response.json()["total"], 2)
            self.assertEqual(len(response.json()["items"]), 1)

    def test_paginated_scenarios_handles_large_catalog(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def seed_many():
                from api.models import ScenarioRecord

                async with session_maker() as session:
                    for index in range(1000):
                        scenario_id = f"bulk_{index:04d}"
                        session.add(ScenarioRecord(scenario_id=scenario_id, owner_user_id="alice", description=scenario_id, definition={"description": scenario_id, "steps": []}))
                    await session.commit()

            asyncio.run(seed_many())

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.get("/api/v1/users/alice/scenarios?limit=25&offset=500")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 1001)
            self.assertEqual(len(response.json()["items"]), 25)

    def test_user_history_is_served_from_database_sync(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            service.config.runtime.history_file.write_text(
                json.dumps(
                    {
                        "slot_key": "alice_slot:alice_scenario",
                        "slot_id": "alice_slot",
                        "scenario_id": "alice_scenario",
                        "execution_id": "exec-1",
                        "executed_at": "2026-04-21T10:00:00",
                        "status": "success",
                        "step": "done",
                        "message": "ok",
                        "updated_at": "2026-04-21T10:00:01",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.get("/api/v1/users/alice/history")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 1)
            self.assertEqual(response.json()["items"][0]["execution_id"], "exec-1")
            self.assertTrue(response.json()["items"][0]["executed_at"].endswith("Z"))

    def test_user_cannot_run_other_user_scenario(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_service():
                return service

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_service] = override_service
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="bob")
            try:
                client = TestClient(app)
                response = client.post("/users/bob/scenarios/alice_scenario/run")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 404)

    def test_user_can_crud_scenario_steps(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_service():
                return service

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_service] = override_service
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                created = client.post(
                    "/users/alice/scenarios/alice_scenario/step-collections/steps",
                    json={"step": {"type": "sleep", "seconds": 1}},
                )
                listed = client.get("/users/alice/scenarios/alice_scenario/step-collections/steps")
                updated = client.put(
                    "/users/alice/scenarios/alice_scenario/step-collections/steps/0",
                    json={"step": {"type": "notify", "message": "ok"}},
                )
                deleted = client.delete("/users/alice/scenarios/alice_scenario/step-collections/steps/0")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["index"], 0)
            self.assertEqual(listed.json(), [{"type": "sleep", "seconds": 1}])
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["step"]["type"], "notify")
            self.assertEqual(deleted.status_code, 200)
            raw = json.loads((Path(tmp) / "scenarios.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["scenarios"]["alice_scenario"]["steps"], [])

    def test_invalid_step_is_rejected(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_service():
                return service

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_service] = override_service
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.post(
                    "/users/alice/scenarios/alice_scenario/step-collections/steps",
                    json={"step": {"type": "open_url"}},
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 422)

    def test_auth_register_login_and_me(self):
        with TemporaryDirectory() as tmp:
            session_maker, engine = _setup_empty_test_db(tmp)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                registered = client.post(
                    "/auth/register",
                    json={"email": "alice@example.com", "password": "LongEnough123"},
                )
                logged_in = client.post(
                    "/auth/jwt/login",
                    data={"username": "alice@example.com", "password": "LongEnough123"},
                )
                token = logged_in.json()["access_token"]
                me = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
                updated = client.patch(
                    "/users/me",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"timezone_name": "America/New_York"},
                )
                invalid_timezone = client.patch(
                    "/users/me",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"timezone_name": "Invalid/Timezone"},
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(registered.status_code, 201)
            self.assertEqual(logged_in.status_code, 200)
            self.assertEqual(me.status_code, 200)
            self.assertEqual(me.json()["email"], "alice@example.com")
            self.assertEqual(me.json()["timezone_name"], "Europe/Brussels")
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["timezone_name"], "America/New_York")
            self.assertEqual(invalid_timezone.status_code, 422)

    def test_enqueue_scenario_creates_persistent_job(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                with patch("api.services.jobs.run_scenario_job.delay", return_value=SimpleNamespace(id="celery-1")):
                    created = client.post("/users/alice/scenarios/alice_scenario/jobs")
                job_id = created.json()["job_id"]
                fetched = client.get(f"/jobs/{job_id}?user_id=alice")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(created.status_code, 202)
            self.assertEqual(created.json()["status"], "queued")
            self.assertEqual(created.json()["celery_task_id"], "celery-1")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["job_id"], job_id)
            self.assertTrue(fetched.json()["created_at"].endswith("Z"))

    def test_client_config_exposes_frontend_safe_settings(self):
        with TemporaryDirectory() as tmp:
            session_maker, engine = _setup_empty_test_db(tmp)

            async def seed_settings():
                from api.settings import upsert_setting

                async with session_maker() as session:
                    await upsert_setting(session, key="feature.dashboard", value={"enabled": True})
                    await upsert_setting(session, key="feature.admin.panel", value={"enabled": True})

            asyncio.run(seed_settings())

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.get("/api/v1/config/client")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["default_timezone"], "Europe/Brussels")
            self.assertEqual(response.json()["features"], {"dashboard": True})

    def test_admin_db_stats_exposes_operational_counts(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                response = client.get("/api/v1/admin/db-stats")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["tables"]["scenarios"], 2)
            self.assertEqual(response.json()["failed_jobs"], 0)

    def test_job_events_are_persistent(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                with patch("api.services.jobs.run_scenario_job.delay", return_value=SimpleNamespace(id="celery-1")):
                    created = client.post("/users/alice/scenarios/alice_scenario/jobs")
                events = client.get(f"/jobs/{created.json()['job_id']}/events?user_id=alice")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(events.status_code, 200)
            self.assertEqual([event["event_type"] for event in events.json()], ["queued", "submitted"])

    def test_run_scenario_uses_database_catalog(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.post("/users/alice/scenarios/alice_scenario/run")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["success"])

    def test_graph_webhook_validation_token_returns_plain_text(self):
        client = TestClient(app)
        response = client.post("/graph/webhook?validationToken=opaque-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "opaque-token")
        self.assertIn("text/plain", response.headers["content-type"])

    def test_graph_webhook_persists_notification_with_client_state(self):
        with TemporaryDirectory() as tmp:
            old_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE")
            os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "secret-state"
            import api.graph

            api.graph.GRAPH_WEBHOOK_CLIENT_STATE = "secret-state"
            session_maker, engine = _setup_empty_test_db(tmp)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                response = client.post(
                    "/graph/webhook",
                    json={
                        "value": [
                            {
                                "subscriptionId": "sub1",
                                "clientState": "secret-state",
                                "changeType": "created",
                                "resource": "users/a/messages/b",
                                "tenantId": "tenant",
                            }
                        ]
                    },
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())
                if old_state is None:
                    os.environ.pop("GRAPH_WEBHOOK_CLIENT_STATE", None)
                else:
                    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = old_state
                api.graph.GRAPH_WEBHOOK_CLIENT_STATE = old_state or ""

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"accepted": 1})

    def test_graph_webhook_deduplicates_retried_notification(self):
        with TemporaryDirectory() as tmp:
            old_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE")
            os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "secret-state"
            import api.graph

            api.graph.GRAPH_WEBHOOK_CLIENT_STATE = "secret-state"
            session_maker, engine = _setup_empty_test_db(tmp)

            async def override_session():
                async with session_maker() as session:
                    yield session

            payload = {
                "value": [
                    {
                        "subscriptionId": "sub1",
                        "clientState": "secret-state",
                        "changeType": "created",
                        "resource": "users/a/messages/b",
                        "tenantId": "tenant",
                    }
                ]
            }
            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                first = client.post("/api/v1/graph/webhook", json=payload)
                second = client.post("/api/v1/graph/webhook", json=payload)
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())
                if old_state is None:
                    os.environ.pop("GRAPH_WEBHOOK_CLIENT_STATE", None)
                else:
                    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = old_state
                api.graph.GRAPH_WEBHOOK_CLIENT_STATE = old_state or ""

            self.assertEqual(first.json(), {"accepted": 1})
            self.assertEqual(second.json(), {"accepted": 0})

    def test_graph_webhook_stores_redacted_raw_payload(self):
        with TemporaryDirectory() as tmp:
            old_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE")
            os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "secret-state"
            import api.graph
            from api.models import GraphNotificationRecord

            api.graph.GRAPH_WEBHOOK_CLIENT_STATE = "secret-state"
            session_maker, engine = _setup_empty_test_db(tmp)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/graph/webhook",
                    json={"value": [{"subscriptionId": "sub1", "clientState": "secret-state", "changeType": "created", "resource": "users/a/messages/b"}]},
                )

                async def read_raw():
                    async with session_maker() as session:
                        record = await session.scalar(select(GraphNotificationRecord))
                        return record.raw_payload

                raw_payload = asyncio.run(read_raw())
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())
                if old_state is None:
                    os.environ.pop("GRAPH_WEBHOOK_CLIENT_STATE", None)
                else:
                    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = old_state
                api.graph.GRAPH_WEBHOOK_CLIENT_STATE = old_state or ""

            self.assertEqual(response.status_code, 200)
            self.assertEqual(raw_payload["clientState"], "***redacted***")

    def test_graph_webhook_can_require_known_subscription(self):
        with TemporaryDirectory() as tmp:
            old_require = os.environ.get("GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION")
            old_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE")
            os.environ["GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION"] = "true"
            os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "secret-state"
            import api.graph

            api.graph.GRAPH_WEBHOOK_CLIENT_STATE = "secret-state"
            session_maker, engine = _setup_empty_test_db(tmp)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/v1/graph/webhook",
                    json={"value": [{"subscriptionId": "missing", "clientState": "secret-state", "changeType": "created", "resource": "users/a/messages/b"}]},
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())
                if old_require is None:
                    os.environ.pop("GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION", None)
                else:
                    os.environ["GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION"] = old_require
                if old_state is None:
                    os.environ.pop("GRAPH_WEBHOOK_CLIENT_STATE", None)
                else:
                    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = old_state
                api.graph.GRAPH_WEBHOOK_CLIENT_STATE = old_state or ""

            self.assertEqual(response.status_code, 404)

    def test_owner_can_create_scenario_and_share_it(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                created = client.post(
                    "/scenarios",
                    json={"scenario_id": "new_scenario", "owner_user_id": "alice", "description": "New"},
                )
                shared = client.post("/scenarios/new_scenario/shares", json={"user_id": "bob"})
                shares = client.get("/scenarios/new_scenario/shares")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["scenario_id"], "new_scenario")
            self.assertEqual(shared.status_code, 201)
            self.assertEqual(shares.json()["user_ids"], ["bob"])

    def test_shared_scenario_is_read_only_for_non_owner(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            try:
                client = TestClient(app)
                app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
                self.assertEqual(client.post("/scenarios/alice_scenario/shares", json={"user_id": "bob"}).status_code, 201)
                app.dependency_overrides[current_active_user] = lambda: _fake_user(email="bob")
                read = client.get("/users/bob/scenarios/alice_scenario")
                write = client.post(
                    "/users/bob/scenarios/alice_scenario/step-collections/steps",
                    json={"step": {"type": "sleep", "seconds": 1}},
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(read.status_code, 200)
            self.assertEqual(write.status_code, 403)
            self.assertEqual(write.json()["code"], "forbidden")

    def test_owner_can_crud_slots(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                created = client.post(
                    "/slots",
                    json={
                        "slot_id": "late_slot",
                        "scenario_id": "alice_scenario",
                        "days": [0],
                        "start": "20:00",
                        "end": "21:00",
                    },
                )
                listed = client.get("/slots?scenario_id=alice_scenario")
                updated = client.patch("/slots/late_slot", json={"enabled": False})
                deleted = client.delete("/slots/late_slot")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["enabled"], True)
            self.assertIn("late_slot", [item["slot_id"] for item in listed.json()["items"]])
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["enabled"], False)
            self.assertEqual(deleted.status_code, 200)

    def test_invalid_slot_payload_is_rejected(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.post(
                    "/slots",
                    json={
                        "slot_id": "bad slot",
                        "scenario_id": "alice_scenario",
                        "days": [7],
                        "start": "21:00",
                        "end": "20:00",
                    },
                )
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "validation_error")

    def test_jobs_can_be_listed_cancelled_and_retried(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                with patch("api.services.jobs.run_scenario_job.delay", return_value=SimpleNamespace(id="celery-1")):
                    created = client.post("/users/alice/scenarios/alice_scenario/jobs")
                job_id = created.json()["job_id"]
                listed = client.get("/jobs")
                with patch("api.services.jobs.celery_app.control.revoke") as revoke:
                    cancelled = client.post(f"/jobs/{job_id}/cancel?user_id=alice")
                with patch("api.services.jobs.run_scenario_job.delay", return_value=SimpleNamespace(id="celery-2")):
                    retried = client.post(f"/jobs/{job_id}/retry?user_id=alice")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(listed.status_code, 200)
            self.assertEqual([item["job_id"] for item in listed.json()["items"]], [job_id])
            self.assertEqual(listed.json()["total"], 1)
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            revoke.assert_called_once_with("celery-1", terminate=False)
            self.assertEqual(retried.status_code, 202)
            self.assertEqual(retried.json()["celery_task_id"], "celery-2")

    def test_admin_can_read_artifacts_and_export_catalog(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)
            screenshots = service.config.runtime.artifacts_dir / "screenshots"
            screenshots.mkdir(parents=True)
            (screenshots / "screen.txt").write_text("ok", encoding="utf-8")

            def override_config():
                return service.config

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_config] = override_config
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                artifacts = client.get("/artifacts")
                downloaded = client.get("/artifacts/screenshots/screen.txt")
                exported = client.get("/admin/export")
                config_checks = client.get("/admin/config-checks")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(artifacts.status_code, 200)
            self.assertEqual(artifacts.json()["items"][0]["name"], "screen.txt")
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.text, "ok")
            self.assertEqual(exported.status_code, 200)
            self.assertIn("alice_scenario", exported.json()["scenarios"]["scenarios"])
            self.assertEqual(config_checks.status_code, 200)

    def test_sensitive_config_values_are_not_exposed(self):
        previous = {
            "AUTH_SECRET": os.environ.get("AUTH_SECRET"),
            "GRAPH_CLIENT_SECRET": os.environ.get("GRAPH_CLIENT_SECRET"),
            "GRAPH_WEBHOOK_CLIENT_STATE": os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE"),
        }
        os.environ["AUTH_SECRET"] = "super-secret-auth-value"
        os.environ["GRAPH_CLIENT_SECRET"] = "super-secret-graph-value"
        os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "super-secret-client-state"
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            def override_service():
                return service

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_service] = override_service
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                payloads = [
                    client.get("/api/v1/ready").text,
                    client.get("/api/v1/runtime").text,
                    client.get("/api/v1/admin/config-checks").text,
                    client.get("/api/v1/config/client").text,
                ]
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        combined = "\n".join(payloads)
        self.assertNotIn("super-secret-auth-value", combined)
        self.assertNotIn("super-secret-graph-value", combined)
        self.assertNotIn("super-secret-client-state", combined)

    def test_version_and_monitoring_endpoints(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                version = client.get("/version")
                status_response = client.get("/api/v1/status")
                monitoring = client.get("/monitoring/summary")
                metrics = client.get("/metrics")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(version.status_code, 200)
            self.assertEqual(version.json()["name"], "smiley")
            self.assertEqual(status_response.status_code, 200)
            self.assertIn("ready", status_response.json())
            self.assertEqual(monitoring.status_code, 200)
            self.assertIn("jobs", monitoring.json())
            self.assertEqual(metrics.status_code, 200)
            self.assertIn("smiley_jobs_total", metrics.text)
            self.assertIn("smiley_jobs_by_status", metrics.text)

    def test_admin_settings_crud(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="admin", superuser=True)
            try:
                client = TestClient(app)
                saved = client.put("/admin/settings/ui.theme", json={"value": {"mode": "dark"}, "description": "UI"})
                listed = client.get("/admin/settings")
                deleted = client.delete("/admin/settings/ui.theme")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["value"]["mode"], "dark")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["items"][0]["key"], "ui.theme")
            self.assertEqual(deleted.status_code, 200)

    def test_user_features_returns_visible_feature_flags(self):
        with TemporaryDirectory() as tmp:
            session_maker, engine = _setup_empty_test_db(tmp)

            async def seed():
                from api.models import AppSettingRecord

                async with session_maker() as session:
                    session.add(AppSettingRecord(key="feature.dashboard", value={"enabled": True}, description=""))
                    session.add(AppSettingRecord(key="feature.admin.panel", value={"enabled": True}, description=""))
                    await session.commit()

            asyncio.run(seed())

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                response = client.get("/api/v1/users/me/features")
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["features"], {"dashboard": True})

    def test_idempotency_key_reuses_created_job_response(self):
        with TemporaryDirectory() as tmp:
            service = _build_service(tmp)
            session_maker, engine = _setup_test_db(tmp, service)

            async def override_session():
                async with session_maker() as session:
                    yield session

            app.dependency_overrides = {}
            app.dependency_overrides[get_async_session] = override_session
            app.dependency_overrides[current_active_user] = lambda: _fake_user(email="alice")
            try:
                client = TestClient(app)
                headers = {"Idempotency-Key": "job-once"}
                with patch("api.services.jobs.run_scenario_job.delay", return_value=SimpleNamespace(id="celery-1")) as delay:
                    first = client.post("/users/alice/scenarios/alice_scenario/jobs", headers=headers)
                    second = client.post("/users/alice/scenarios/alice_scenario/jobs", headers=headers)
            finally:
                app.dependency_overrides = {}
                asyncio.run(engine.dispose())

            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(first.json()["job_id"], second.json()["job_id"])
            self.assertEqual(delay.call_count, 1)

    def test_migrations_can_downgrade_and_upgrade(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "migration-cycle.db"
            env = {**os.environ, "AUTH_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
            commands = [
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                [sys.executable, "-m", "alembic", "downgrade", "base"],
                [sys.executable, "-m", "alembic", "upgrade", "head"],
            ]
            for command in commands:
                result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_retention_task_can_be_disabled(self):
        from api.tasks import prune_retention_task

        previous = os.environ.get("RETENTION_PRUNE_ENABLED")
        os.environ["RETENTION_PRUNE_ENABLED"] = "false"
        try:
            result = prune_retention_task()
        finally:
            if previous is None:
                os.environ.pop("RETENTION_PRUNE_ENABLED", None)
            else:
                os.environ["RETENTION_PRUNE_ENABLED"] = previous

        self.assertEqual(result, {"enabled": False})


def _fake_user(email: str, superuser: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        is_superuser=superuser,
        is_active=True,
        is_verified=True,
        timezone_name="Europe/Brussels",
    )


def _setup_test_db(tmp: str, service: SchedulerService):
    db_path = Path(tmp) / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            await seed_catalog_from_json(session, service.config.runtime.scenarios_file, service.config.runtime.slots_file)

    asyncio.run(setup())
    return session_maker, engine


def _setup_empty_test_db(tmp: str):
    db_path = Path(tmp) / "auth.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    return session_maker, engine


def _build_service(tmp: str) -> SchedulerService:
    base = Path(tmp)
    scenarios_file = base / "scenarios.json"
    scenarios_file.write_text(
        """
{
  "schema_version": 1,
  "data": {},
  "scenarios": {
    "alice_scenario": {
      "user_id": "alice",
      "description": "Alice",
      "steps": []
    },
    "bob_scenario": {
      "user_ids": ["bob"],
      "description": "Bob",
      "steps": []
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    slots_file = base / "slots.json"
    slots_file.write_text(
        """
{
  "slots": [
    {
      "id": "alice_slot",
      "days": [0, 1, 2, 3, 4, 5, 6],
      "start": "00:00",
      "end": "23:59",
      "scenario": "alice_scenario"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    runtime = RuntimeConfig(
        timezone_name="Europe/Brussels",
        check_interval_seconds=10,
        countdown_threshold_seconds=300,
        network_retry_seconds=10,
        planning_notification_cooldown_seconds=900,
        lock_stale_seconds=100,
        state_dir=base,
        lock_file=base / "scheduler.lock",
        execution_history_file=base / "executions.json",
        next_execution_file=base / "next.json",
        last_run_file=base / "last_run.json",
        slots_file=slots_file,
        scenarios_file=scenarios_file,
        history_file=base / "history.jsonl",
        artifacts_dir=base / "artifacts",
        log_file=None,
        log_max_bytes=1024,
        log_backup_count=2,
        log_json=False,
    )
    return SchedulerService(
        config=AppConfig(
            task=TaskConfig(),
            network=NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
            runtime=runtime,
            debug_enabled=False,
        ),
        logger=Logger(debug_enabled=False),
        notifier=Notifier(None, Logger(debug_enabled=False)),
        network_guard=type(
            "Guard",
            (),
            {
                "is_default_network_available": lambda self, context="": True,
                "is_network_available_by_key": lambda self, key: True,
            },
        )(),
        slots=(TimeSlot("alice_slot", (0, 1, 2, 3, 4, 5, 6), 0, 0, 23, 59, "alice_scenario"),),
        scenarios={
            "alice_scenario": ScenarioDefinition("alice_scenario", "Alice", steps=()),
            "bob_scenario": ScenarioDefinition("bob_scenario", "Bob", steps=()),
        },
        scenario_data=ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None),
    )

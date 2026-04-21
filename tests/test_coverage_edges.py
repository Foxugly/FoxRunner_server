from __future__ import annotations

import asyncio
import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from api.artifacts import artifact_response, list_artifacts, prune_artifacts
from api.feature_flags import is_feature_enabled
from api.health import _check_celery, _check_redis, readiness
from api.logging_config import JsonFormatter, configure_api_logging
from api.mail import send_password_reset_email
from api.models import AppSettingRecord
from api.retention import prune_database_records
from app.config import PushoverConfig, TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from operations.http_ops import handle_http_request
from operations.registry import OperationContext
from tests.helpers import setup_empty_test_db


class CoverageEdgeTests(unittest.TestCase):
    def test_feature_flags_default_and_enabled(self):
        with TemporaryDirectory() as tmp:
            session_maker, engine = setup_empty_test_db(tmp)

            async def run():
                async with session_maker() as session:
                    missing = await is_feature_enabled(session, "demo", default=True)
                    session.add(AppSettingRecord(key="feature.demo", value={"enabled": False}, description=""))
                    await session.commit()
                    disabled = await is_feature_enabled(session, "demo", default=True)
                    return missing, disabled

            missing, disabled = asyncio.run(run())
            asyncio.run(engine.dispose())

        self.assertTrue(missing)
        self.assertFalse(disabled)

    def test_mail_uses_graph_or_smtp_or_noops_without_host(self):
        with patch.dict(os.environ, {"GRAPH_MAIL_ENABLED": "true", "APP_PASSWORD_RESET_URL": "https://app/reset"}, clear=False):
            with patch("api.mail.send_graph_mail") as send_graph:
                send_password_reset_email("alice@example.com", "token")
                send_graph.assert_called_once()

        with patch.dict(os.environ, {"GRAPH_MAIL_ENABLED": "false", "SMTP_HOST": ""}, clear=False):
            send_password_reset_email("alice@example.com", "token")

        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch.dict(
            os.environ,
            {
                "GRAPH_MAIL_ENABLED": "false",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "2525",
                "SMTP_USERNAME": "user",
                "SMTP_PASSWORD": "pass",
                "SMTP_FROM": "from@example.com",
                "SMTP_STARTTLS": "true",
            },
            clear=False,
        ):
            with patch("api.mail.smtplib.SMTP", return_value=smtp):
                send_password_reset_email("alice@example.com", "token")
                smtp.starttls.assert_called_once()
                smtp.login.assert_called_once_with("user", "pass")
                smtp.send_message.assert_called_once()

    def test_logging_formatter_and_configuration(self):
        record = logging.LogRecord("smiley.api", logging.INFO, __file__, 1, "hello", (), None)
        record.request_id = "req"
        payload = JsonFormatter().format(record)
        self.assertIn('"request_id": "req"', payload)

        previous_env = os.environ.get("APP_ENV")
        previous_http = os.environ.get("API_LOG_HTTP_ENABLED")
        os.environ["APP_ENV"] = "development"
        os.environ["API_LOG_HTTP_ENABLED"] = "true"
        logger = logging.getLogger("smiley.api")
        old_handlers = list(logger.handlers)
        logger.handlers = []
        try:
            configure_api_logging(json_enabled=True)
            self.assertFalse(logger.disabled)
            self.assertTrue(logger.handlers)
            configure_api_logging(json_enabled=False)
        finally:
            logger.handlers = old_handlers
            if previous_env is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = previous_env
            if previous_http is None:
                os.environ.pop("API_LOG_HTTP_ENABLED", None)
            else:
                os.environ["API_LOG_HTTP_ENABLED"] = previous_http

    def test_health_helpers_cover_ok_and_error_paths(self):
        redis_client = MagicMock()
        with patch("redis.Redis.from_url", return_value=redis_client):
            self.assertEqual(_check_redis(), "ok")
        with patch("redis.Redis.from_url", side_effect=RuntimeError("down")):
            self.assertIn("error:", _check_redis())
        with patch("api.health.celery_app.control.ping", return_value=[{"worker": "pong"}]):
            self.assertEqual(_check_celery(), "ok")
        with patch("api.health.celery_app.control.ping", return_value=[]):
            self.assertEqual(_check_celery(), "no_workers")
        with patch("api.health.celery_app.control.ping", side_effect=RuntimeError("boom")):
            self.assertIn("error:", _check_celery())

    def test_readiness_degraded_when_celery_required(self):
        class Session:
            async def execute(self, query):
                return None

        with (
            patch("api.health._check_redis", return_value="ok"),
            patch("api.health._check_celery", return_value="no_workers"),
            patch("api.health.is_graph_configured", return_value=False),
        ):
            with patch.dict(os.environ, {"API_REQUIRE_CELERY_WORKER": "true"}, clear=False):
                result = asyncio.run(readiness(Session()))
        self.assertEqual(result["status"], "degraded")

    def test_artifact_helpers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshots = root / "screenshots"
            screenshots.mkdir()
            artifact = screenshots / "screen.txt"
            artifact.write_text("ok", encoding="utf-8")
            self.assertEqual(list_artifacts(root)[0]["name"], "screen.txt")
            self.assertEqual(Path(artifact_response(root, "screenshots", "screen.txt").path), artifact)
            with self.assertRaises(HTTPException):
                artifact_response(root, "missing", "screen.txt")
            with self.assertRaises(HTTPException):
                artifact_response(root, "screenshots", "../bad")
            self.assertEqual(prune_artifacts(root, older_than_days=999), 0)

    def test_prune_database_records_no_filters(self):
        with TemporaryDirectory() as tmp:
            session_maker, engine = setup_empty_test_db(tmp)

            async def run():
                async with session_maker() as session:
                    return await prune_database_records(session)

            result = asyncio.run(run())
            asyncio.run(engine.dispose())

        self.assertEqual(result, {"jobs": 0, "job_events": 0, "audit": 0, "graph_notifications": 0})

    def test_notifier_paths(self):
        logger = Logger(debug_enabled=False)
        disabled = Notifier(None, logger)
        self.assertFalse(disabled.send("hello"))

        response = MagicMock()
        response.raise_for_status.return_value = None
        with patch("app.notifier.requests.post", return_value=response):
            enabled = Notifier(PushoverConfig(token="t", user_key="u", timeout_seconds=1), logger)
            self.assertTrue(enabled.send("hello"))

        fallback_response = SimpleNamespace(status=500, read=lambda: b"no")
        fallback_conn = MagicMock()
        fallback_conn.getresponse.return_value = fallback_response
        with patch("app.notifier.requests.post", side_effect=RuntimeError("down")), patch("app.notifier.http.client.HTTPSConnection", return_value=fallback_conn):
            enabled = Notifier(PushoverConfig(token="t", user_key="u", timeout_seconds=1), logger)
            self.assertFalse(enabled.send("hello"))

    def test_http_operation(self):
        context = _operation_context(dry_run=True)
        handle_http_request(context, {"url": "https://example.com"})

        response_ok = SimpleNamespace(status_code=204)
        with patch("operations.http_ops.requests.request", return_value=response_ok) as request:
            context = _operation_context(dry_run=False)
            handle_http_request(context, {"url": "https://example.com", "method": "post", "expected_status": 204})
            request.assert_called_once()
        response_bad = SimpleNamespace(status_code=500)
        with patch("operations.http_ops.requests.request", return_value=response_bad):
            with self.assertRaises(RuntimeError):
                handle_http_request(context, {"url": "https://example.com", "expected_status": 200})


if __name__ == "__main__":
    unittest.main()


def _operation_context(*, dry_run: bool) -> OperationContext:
    return OperationContext(
        driver=None,
        config=TaskConfig(),
        logger=Logger(debug_enabled=False),
        notifier=Notifier(None, Logger(debug_enabled=False)),
        network_check=None,
        network_check_by_key=None,
        template_context={},
        pushovers={},
        default_pushover_key=None,
        networks={},
        default_network_key=None,
        parallel_safe_steps=frozenset(),
        dry_run=dry_run,
    )

"""Tests for the system status aggregation (scheduler / celery / redis / db)
exposed at GET /api/v1/system/status and consumed by the frontend alarm banner.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from accounts.models import User
from ops import services as ops_services
from ops.models import AppSetting


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_heartbeat(state_dir: str, *, age_seconds: float, state: str = "planning") -> None:
    at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    payload = {"updated_at": _iso_z(at), "pid": 1234, "state": state}
    (Path(state_dir) / "scheduler_heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")


class SystemStatusServiceTest(TestCase):
    def _run(self, state_dir: str, *, ping_replies):
        """Run system_status with celery mocked and APP_STATE_DIR redirected."""
        celery_mock = mock.MagicMock()
        celery_mock.control.ping.return_value = ping_replies
        with (
            mock.patch("foxrunner.celery.celery_app", celery_mock),
            mock.patch.dict(os.environ, {"APP_STATE_DIR": state_dir}),
        ):
            return ops_services.system_status()

    def test_all_ok(self):
        AppSetting.objects.create(key="celery_beat_heartbeat", value={"at": _iso_z(datetime.now(UTC))})
        with tempfile.TemporaryDirectory() as state_dir:
            _write_heartbeat(state_dir, age_seconds=5)
            result = self._run(state_dir, ping_replies=[{"worker@host": {"ok": "pong"}}])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["checks"]["scheduler"]["status"], "ok")
        self.assertEqual(result["checks"]["scheduler"]["state"], "planning")
        self.assertEqual(result["checks"]["celery_worker"]["status"], "ok")
        self.assertEqual(result["down"], [])

    def test_scheduler_down_when_heartbeat_stale(self):
        AppSetting.objects.create(key="celery_beat_heartbeat", value={"at": _iso_z(datetime.now(UTC))})
        with tempfile.TemporaryDirectory() as state_dir:
            _write_heartbeat(state_dir, age_seconds=600)  # >> 90s threshold
            result = self._run(state_dir, ping_replies=[{"worker@host": {}}])
        self.assertEqual(result["checks"]["scheduler"]["status"], "down")
        self.assertIn("offline", result["checks"]["scheduler"]["detail"])
        self.assertEqual(result["status"], "down")  # scheduler is critical
        self.assertIn("scheduler", result["down"])

    def test_scheduler_down_when_heartbeat_missing(self):
        with tempfile.TemporaryDirectory() as state_dir:  # no heartbeat file
            result = self._run(state_dir, ping_replies=[{"worker@host": {}}])
        self.assertEqual(result["checks"]["scheduler"]["status"], "down")
        self.assertEqual(result["status"], "down")

    def test_degraded_when_only_workers_down(self):
        AppSetting.objects.create(key="celery_beat_heartbeat", value={"at": _iso_z(datetime.now(UTC))})
        with tempfile.TemporaryDirectory() as state_dir:
            _write_heartbeat(state_dir, age_seconds=5)
            result = self._run(state_dir, ping_replies=[])  # no workers responded
        self.assertEqual(result["checks"]["celery_worker"]["status"], "down")
        self.assertEqual(result["checks"]["scheduler"]["status"], "ok")
        self.assertEqual(result["status"], "degraded")  # worker is non-critical

    def test_beat_down_when_stamp_stale(self):
        AppSetting.objects.create(
            key="celery_beat_heartbeat",
            value={"at": _iso_z(datetime.now(UTC) - timedelta(seconds=600))},
        )
        with tempfile.TemporaryDirectory() as state_dir:
            _write_heartbeat(state_dir, age_seconds=5)
            result = self._run(state_dir, ping_replies=[{"worker@host": {}}])
        self.assertEqual(result["checks"]["celery_beat"]["status"], "down")
        self.assertEqual(result["status"], "degraded")  # beat is non-critical


def _login(client: Client, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/jwt/login",
        data=f"username={email}&password={password}",
        content_type="application/x-www-form-urlencoded",
    )
    assert response.status_code == 200, response.content
    return response.json()["access_token"]


class SystemStatusEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(email="alice@example.com", password="password123!")
        self.token = _login(self.client, "alice@example.com", "password123!")

    def test_requires_auth(self):
        response = self.client.get("/api/v1/system/status")
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_returns_status_shape(self):
        response = self.client.get(
            "/api/v1/system/status",
            **{"HTTP_AUTHORIZATION": f"Bearer {self.token}"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn(body["status"], {"ok", "degraded", "down"})
        for key in ("database", "redis", "celery_worker", "celery_beat", "scheduler"):
            self.assertIn(key, body["checks"])

"""Tests for ``foxrunner.rate_limit.RateLimitMiddleware``.

Each test resets the in-process bucket between cases (the module-level
``_WINDOWS`` dict is process-global). Redis is mocked: we never want a
real Redis connection during ``manage.py test``.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from foxrunner import rate_limit


def _reset_buckets() -> None:
    rate_limit._WINDOWS.clear()
    rate_limit._REDIS_DISABLED_UNTIL = 0.0


class _RateLimitMixin:
    def setUp(self):  # type: ignore[override]
        super().setUp()
        _reset_buckets()
        self.client = Client()

    def tearDown(self):  # type: ignore[override]
        _reset_buckets()
        super().tearDown()


class UnlimitedPathTest(_RateLimitMixin, TestCase):
    @patch.dict(os.environ, {"API_RATE_LIMIT_ENABLED": "true", "API_RATE_LIMIT_MAX_REQUESTS": "1"})
    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_unlimited_path_passes_through(self, _client):
        # /api/v1/health is NOT in the limited set; even with MAX=1 we
        # should be able to hit it many times.
        for _ in range(5):
            response = self.client.get("/api/v1/health")
            self.assertEqual(response.status_code, 200)


class LimitedPathTest(_RateLimitMixin, TestCase):
    @patch.dict(
        os.environ,
        {
            "API_RATE_LIMIT_ENABLED": "true",
            "API_RATE_LIMIT_MAX_REQUESTS": "3",
            "API_RATE_LIMIT_WINDOW_SECONDS": "60",
        },
    )
    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_limited_path_blocks_after_max_requests(self, _client):
        url = "/api/v1/auth/jwt/login"
        for _ in range(3):
            response = self.client.post(url, data="username=x&password=y", content_type="application/x-www-form-urlencoded")
            # 401/400 is fine -- we're testing the rate limiter, not the
            # success path.
            self.assertNotEqual(response.status_code, 429, response.content)
        response = self.client.post(url, data="username=x&password=y", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 429, response.content)
        body = response.json()
        self.assertEqual(body, {"code": "rate_limited", "message": "Trop de requetes.", "details": None})

    @patch.dict(
        os.environ,
        {
            "API_RATE_LIMIT_ENABLED": "true",
            "API_RATE_LIMIT_MAX_REQUESTS": "2",
            "API_RATE_LIMIT_WINDOW_SECONDS": "1",
        },
    )
    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_limited_path_resets_after_window(self, _client):
        url = "/api/v1/auth/jwt/login"
        for _ in range(2):
            self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
        blocked = self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
        self.assertEqual(blocked.status_code, 429)
        # Window is 1 second -- sleep past it and confirm the next request
        # is no longer rate limited.
        time.sleep(1.5)
        recovered = self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
        self.assertNotEqual(recovered.status_code, 429, recovered.content)


class RedisFallbackTest(_RateLimitMixin, TestCase):
    @patch.dict(
        os.environ,
        {
            "API_RATE_LIMIT_ENABLED": "true",
            "API_RATE_LIMIT_MAX_REQUESTS": "2",
            "API_RATE_LIMIT_WINDOW_SECONDS": "60",
        },
    )
    def test_falls_back_to_in_process_when_redis_unreachable(self):
        # Patch ``django_redis.get_redis_connection`` to raise -- mirrors
        # the production behaviour when Redis is down. The middleware
        # should silently switch to the in-process deque without 5xx-ing.
        with patch("django_redis.get_redis_connection", side_effect=ConnectionError("redis down")):
            url = "/api/v1/auth/jwt/login"
            for _ in range(2):
                response = self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
                self.assertNotEqual(response.status_code, 429)
            blocked = self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.json()["code"], "rate_limited")


@override_settings()
class DisabledViaEnvTest(_RateLimitMixin, TestCase):
    @patch.dict(os.environ, {"API_RATE_LIMIT_ENABLED": "false", "API_RATE_LIMIT_MAX_REQUESTS": "1"})
    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_disabled_via_env_var(self, _client):
        # MAX=1 would normally block on the second request; with the limit
        # disabled both should pass.
        url = "/api/v1/auth/jwt/login"
        for _ in range(3):
            response = self.client.post(url, data="x=1", content_type="application/x-www-form-urlencoded")
            self.assertNotEqual(response.status_code, 429, response.content)

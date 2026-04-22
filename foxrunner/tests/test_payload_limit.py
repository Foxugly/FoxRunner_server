"""Tests for ``foxrunner.payload_limit.PayloadLimitMiddleware``.

The middleware translates Django's ``RequestDataTooBig`` exception
(raised when the request body exceeds ``DATA_UPLOAD_MAX_MEMORY_SIZE``)
into a 413 with the project's standard JSON envelope.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase, override_settings


class PayloadWithinLimitTest(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_request_within_limit_succeeds(self, _client):
        body = "username=alice@example.com&password=password123!"
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data=body,
            content_type="application/x-www-form-urlencoded",
        )
        # 401 is the expected status (no such user). What matters: the
        # body was NOT rejected for size.
        self.assertNotEqual(response.status_code, 413)


@override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=128)
class PayloadExceedsLimitTest(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_request_exceeding_limit_returns_413(self, _client):
        # JSON body > 128 bytes triggers RequestDataTooBig as Django reads
        # the request stream.
        oversized = '{"x":"' + ("a" * 1024) + '"}'
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data=oversized,
            content_type="application/json",
            HTTP_CONTENT_LENGTH=str(len(oversized)),
        )
        self.assertEqual(response.status_code, 413, response.content)
        body = response.json()
        self.assertEqual(body["code"], "payload_too_large")
        self.assertEqual(body["message"], "Payload trop volumineux.")
        self.assertEqual(body["details"], {"max_bytes": 128})

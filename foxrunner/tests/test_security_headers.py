"""Regression test for the security headers stack.

Asserts that ``django.middleware.security.SecurityMiddleware`` plus
``foxrunner.middleware.RequestContextMiddleware`` deliver:

* ``X-Content-Type-Options: nosniff``
* ``Referrer-Policy: no-referrer``
* ``X-Frame-Options: DENY``
* ``X-Request-ID: <hex uuid>``
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import Client, TestCase


class SecurityHeadersTest(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_security_headers_present_on_health(self, _client):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        request_id = response["X-Request-ID"]
        self.assertTrue(request_id, "X-Request-ID must be set")
        # Should be a 32-char hex when no client header is supplied.
        self.assertEqual(len(request_id), 32)
        int(request_id, 16)  # raises if not hex

    @patch("foxrunner.rate_limit._get_redis_client", return_value=None)
    def test_request_id_echoed_when_provided(self, _client):
        provided = "abcdef0123456789"
        response = self.client.get("/api/v1/health", HTTP_X_REQUEST_ID=provided)
        self.assertEqual(response["X-Request-ID"], provided)

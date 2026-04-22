"""Tests for the auth wrappers in ``accounts.api``.

Mirrors the FastAPI auth contract verified by ``tests/test_api.py`` in
the legacy tree. Coverage focuses on the wrappers (login, logout,
forgot/reset password, /users/me) -- djoser's own register / refresh /
verify endpoints are exercised separately by djoser's test suite.
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.signing import TimestampSigner
from django.test import Client, TestCase

from accounts.api import PASSWORD_RESET_MAX_AGE_SECONDS, PASSWORD_RESET_SALT
from accounts.models import User


class _AuthMixin:
    """Helpers shared across every auth test."""

    def login(self, client: Client, email: str, password: str) -> str:
        response = client.post(
            "/api/v1/auth/jwt/login",
            data=f"username={email}&password={password}",
            content_type="application/x-www-form-urlencoded",
        )
        assert response.status_code == 200, response.content
        return response.json()["access_token"]


class JwtLoginTest(_AuthMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="alice@example.com", password="password123!")

    def test_form_login_returns_access_token(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=alice@example.com&password=password123!",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("access_token", body)
        self.assertTrue(body["access_token"])
        self.assertEqual(body["token_type"], "bearer")

    def test_form_login_accepts_email_alias(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="email=alice@example.com&password=password123!",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access_token", response.json())

    def test_form_login_bad_password_returns_401(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=alice@example.com&password=wrong",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["code"], "unauthorized")
        self.assertEqual(body["message"], "Identifiants invalides.")

    def test_form_login_unknown_user_returns_401(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=ghost@example.com&password=whatever123!",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["code"], "unauthorized")
        self.assertEqual(body["message"], "Identifiants invalides.")

    def test_form_login_missing_fields_returns_400(self):
        response = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=alice@example.com",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "bad_request")


class JwtLogoutTest(TestCase):
    def test_logout_returns_ok(self):
        response = self.client.post("/api/v1/auth/jwt/logout")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"status": "ok"})


class UsersMeTest(_AuthMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="bob@example.com",
            password="password123!",
            is_verified=True,
            timezone_name="Europe/Brussels",
        )

    def test_users_me_get(self):
        token = self.login(self.client, "bob@example.com", "password123!")
        response = self.client.get("/api/v1/users/me", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        for field in ("id", "email", "is_active", "is_superuser", "is_verified", "timezone_name", "date_joined"):
            self.assertIn(field, body)
        self.assertEqual(body["email"], "bob@example.com")
        self.assertEqual(body["timezone_name"], "Europe/Brussels")
        self.assertTrue(body["is_active"])
        self.assertTrue(body["is_verified"])
        self.assertFalse(body["is_superuser"])

    def test_users_me_requires_auth(self):
        response = self.client.get("/api/v1/users/me")
        self.assertEqual(response.status_code, 401)

    def test_users_me_patch_timezone(self):
        token = self.login(self.client, "bob@example.com", "password123!")
        response = self.client.patch(
            "/api/v1/users/me",
            data={"timezone_name": "Europe/Paris"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["timezone_name"], "Europe/Paris")
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone_name, "Europe/Paris")

    def test_users_me_patch_invalid_timezone(self):
        token = self.login(self.client, "bob@example.com", "password123!")
        response = self.client.patch(
            "/api/v1/users/me",
            data={"timezone_name": "Not/A_Real_Zone"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 422, response.content)
        body = response.json()
        # The HttpError(422, ...) is mapped to {code: "validation_error", ...}
        # by ``_code_for_status`` in foxrunner.exception_handlers.
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("IANA", body["message"])

    def test_users_me_patch_password(self):
        token = self.login(self.client, "bob@example.com", "password123!")
        response = self.client.patch(
            "/api/v1/users/me",
            data={"password": "newpassword!"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpassword!"))

    def test_users_me_patch_email(self):
        token = self.login(self.client, "bob@example.com", "password123!")
        response = self.client.patch(
            "/api/v1/users/me",
            data={"email": "bob2@example.com"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["email"], "bob2@example.com")


class ForgotPasswordTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="carol@example.com", password="password123!")

    def test_forgot_password_silent_for_unknown_email(self):
        with patch("app.mail.send_password_reset_email") as mock_send:
            response = self.client.post(
                "/api/v1/auth/forgot-password",
                data={"email": "ghost@example.com"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json(), {"status": "queued"})
        mock_send.assert_not_called()

    def test_forgot_password_known_email_sends_token(self):
        with patch("app.mail.send_password_reset_email") as mock_send:
            response = self.client.post(
                "/api/v1/auth/forgot-password",
                data={"email": "carol@example.com"},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json(), {"status": "queued"})
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(args[0], "carol@example.com")
        self.assertTrue(args[1])
        # The token must round-trip through TimestampSigner.unsign.
        signer = TimestampSigner(salt=PASSWORD_RESET_SALT)
        decoded = signer.unsign(args[1], max_age=PASSWORD_RESET_MAX_AGE_SECONDS)
        self.assertEqual(decoded, str(self.user.id))


class ResetPasswordTest(_AuthMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(email="dave@example.com", password="password123!")

    def _sign(self, payload: str) -> str:
        return TimestampSigner(salt=PASSWORD_RESET_SALT).sign(payload)

    def test_reset_password_with_valid_token(self):
        token = self._sign(str(self.user.id))
        response = self.client.post(
            "/api/v1/auth/reset-password",
            data={"token": token, "password": "newpass!"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"status": "ok"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass!"))
        # The new password lets the user log in.
        access_token = self.login(self.client, "dave@example.com", "newpass!")
        self.assertTrue(access_token)

    def test_reset_password_with_invalid_token_returns_400(self):
        response = self.client.post(
            "/api/v1/auth/reset-password",
            data={"token": "garbage:nope", "password": "newpass!"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()
        self.assertEqual(body["code"], "bad_request")
        # The original "Token invalide." is redacted because the word
        # "token" is a secret marker in api.redaction.redact_text. This
        # mirrors the FastAPI contract -- the client only learns from the
        # status + code, never the raw message.
        self.assertEqual(body["message"], "***redacted***")

    def test_reset_password_with_expired_token_returns_400(self):
        token = self._sign(str(self.user.id))

        # Patch ``django.core.signing.time.time`` so unsign sees a future
        # timestamp (>3600s after signing) and raises SignatureExpired.
        from django.core import signing

        original_time = signing.time.time
        try:
            signing.time.time = lambda: original_time() + PASSWORD_RESET_MAX_AGE_SECONDS + 60
            response = self.client.post(
                "/api/v1/auth/reset-password",
                data={"token": token, "password": "newpass!"},
                content_type="application/json",
            )
        finally:
            signing.time.time = original_time

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "bad_request")

    def test_reset_password_with_unknown_user_returns_400(self):
        # Sign a syntactically valid UUID that does not exist in the DB.
        token = self._sign("00000000-0000-0000-0000-000000000000")
        response = self.client.post(
            "/api/v1/auth/reset-password",
            data={"token": token, "password": "newpass!"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "bad_request")

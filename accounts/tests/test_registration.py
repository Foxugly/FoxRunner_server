"""Tests for the self-service registration + email-activation flow.

Covers ``accounts.api.register`` / ``accounts.api.activate`` and the
``accounts.activation`` TimestampSigner token. The contract mirrors the
other account flows (forgot-password, magic-link): anti-enumeration ``202``
on register, single-use-by-TTL token, activation flips the account live.
"""

from __future__ import annotations

from unittest.mock import patch

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.test import Client, TestCase

from accounts.activation import (
    ACTIVATION_MAX_AGE_SECONDS,
    make_activation_token,
    parse_activation_token,
)
from accounts.models import User

STRONG_PASSWORD = "Str0ngPass!42x"


class ActivationTokenTest(TestCase):
    def test_round_trip(self):
        token = make_activation_token("abc-123")
        self.assertEqual(parse_activation_token(token), "abc-123")

    def test_expired_raises(self):
        token = make_activation_token("abc-123")
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + ACTIVATION_MAX_AGE_SECONDS + 60
            with self.assertRaises(SignatureExpired):
                parse_activation_token(token)
        finally:
            signing.time.time = original

    def test_tampered_raises(self):
        with self.assertRaises(BadSignature):
            parse_activation_token("garbage:nope")

    def test_salt_isolated_from_magic_link(self):
        # A magic-link token must NOT validate as an activation token.
        from accounts.magic_link import make_magic_link_token

        with self.assertRaises(BadSignature):
            parse_activation_token(make_magic_link_token("abc-123"))


class RegisterEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()

    def _register(self, email: str, password: str = STRONG_PASSWORD):
        return self.client.post(
            "/api/v1/auth/register",
            data={"email": email, "password": password},
            content_type="application/json",
        )

    def test_unknown_email_creates_inactive_user_and_sends_link(self):
        with patch("app.mail.send_activation_email") as mock_send:
            r = self._register("newbie@example.com")
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json(), {"status": "queued"})

        user = User.objects.get(email="newbie@example.com")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_verified)

        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(args[0], "newbie@example.com")
        # The emailed token must round-trip to this user's id.
        self.assertEqual(parse_activation_token(args[1]), str(user.id))

    def test_pending_user_cannot_log_in(self):
        with patch("app.mail.send_activation_email"):
            self._register("pending@example.com")
        r = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=pending@example.com&password=" + STRONG_PASSWORD,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(r.status_code, 401, r.content)

    def test_weak_password_returns_422_and_creates_nothing(self):
        with patch("app.mail.send_activation_email") as mock_send:
            r = self._register("weak@example.com", password="abc")
        self.assertEqual(r.status_code, 422, r.content)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        mock_send.assert_not_called()

    def test_existing_active_email_is_silent(self):
        User.objects.create_user(email="taken@example.com", password="OriginalPass!9", is_active=True, is_verified=True)
        with patch("app.mail.send_activation_email") as mock_send:
            r = self._register("taken@example.com")
        # Anti-enumeration: same 202, but no email and the password is untouched.
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json(), {"status": "queued"})
        mock_send.assert_not_called()
        self.assertEqual(User.objects.filter(email="taken@example.com").count(), 1)
        user = User.objects.get(email="taken@example.com")
        self.assertTrue(user.check_password("OriginalPass!9"))

    def test_existing_pending_email_resends_without_touching_password(self):
        pending = User.objects.create_user(
            email="again@example.com",
            password="OriginalPass!9",
            is_active=False,
            is_verified=False,
        )
        with patch("app.mail.send_activation_email") as mock_send:
            r = self._register("again@example.com")  # different password submitted
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(parse_activation_token(args[1]), str(pending.id))
        # No duplicate row, and the stored password is NOT overwritten.
        self.assertEqual(User.objects.filter(email="again@example.com").count(), 1)
        pending.refresh_from_db()
        self.assertTrue(pending.check_password("OriginalPass!9"))

    def test_email_is_normalized(self):
        with patch("app.mail.send_activation_email"):
            r = self._register("Mixed@Example.com")
        self.assertEqual(r.status_code, 202, r.content)
        # normalize_email lower-cases the domain part.
        self.assertTrue(User.objects.filter(email="Mixed@example.com").exists())


class ActivateEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="activate@example.com",
            password=STRONG_PASSWORD,
            is_active=False,
            is_verified=False,
        )

    def _activate(self, token: str):
        return self.client.post(
            "/api/v1/auth/activate",
            data={"token": token},
            content_type="application/json",
        )

    def test_valid_token_activates_and_verifies(self):
        r = self._activate(make_activation_token(self.user.id))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {"status": "ok"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertTrue(self.user.is_verified)

    def test_activation_enables_login(self):
        self._activate(make_activation_token(self.user.id))
        r = self.client.post(
            "/api/v1/auth/jwt/login",
            data="username=activate@example.com&password=" + STRONG_PASSWORD,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["access_token"])

    def test_expired_token_returns_410(self):
        token = make_activation_token(self.user.id)
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + ACTIVATION_MAX_AGE_SECONDS + 60
            r = self._activate(token)
        finally:
            signing.time.time = original
        self.assertEqual(r.status_code, 410, r.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_invalid_token_returns_400(self):
        self.assertEqual(self._activate("garbage:nope").status_code, 400)

    def test_unknown_user_returns_400(self):
        token = make_activation_token("00000000-0000-0000-0000-000000000000")
        self.assertEqual(self._activate(token).status_code, 400)

    def test_activation_is_idempotent(self):
        token = make_activation_token(self.user.id)
        self.assertEqual(self._activate(token).status_code, 200)
        # Same token again (still within TTL) stays a 200 no-op.
        self.assertEqual(self._activate(token).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active and self.user.is_verified)


class RegistrationEndToEndTest(TestCase):
    """register -> activate (via the emailed token) -> login."""

    def test_full_flow(self):
        client = Client()
        with patch("app.mail.send_activation_email") as mock_send:
            reg = client.post(
                "/api/v1/auth/register",
                data={"email": "journey@example.com", "password": STRONG_PASSWORD},
                content_type="application/json",
            )
        self.assertEqual(reg.status_code, 202, reg.content)
        token = mock_send.call_args[0][1]

        act = client.post(
            "/api/v1/auth/activate",
            data={"token": token},
            content_type="application/json",
        )
        self.assertEqual(act.status_code, 200, act.content)

        login = client.post(
            "/api/v1/auth/jwt/login",
            data="username=journey@example.com&password=" + STRONG_PASSWORD,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(login.status_code, 200, login.content)
        self.assertTrue(login.json()["access_token"])

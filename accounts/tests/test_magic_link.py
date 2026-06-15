from __future__ import annotations

from unittest.mock import patch

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.test import Client, TestCase

from accounts.magic_link import (
    MAGIC_LINK_MAX_AGE_SECONDS,
    make_magic_link_token,
    parse_magic_link_token,
)
from accounts.models import User


class MagicLinkTokenTest(TestCase):
    def test_round_trip(self):
        token = make_magic_link_token("abc-123")
        self.assertEqual(parse_magic_link_token(token), "abc-123")

    def test_expired_raises(self):
        token = make_magic_link_token("abc-123")
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + MAGIC_LINK_MAX_AGE_SECONDS + 60
            with self.assertRaises(SignatureExpired):
                parse_magic_link_token(token)
        finally:
            signing.time.time = original

    def test_tampered_raises(self):
        with self.assertRaises(BadSignature):
            parse_magic_link_token("garbage:nope")


class MagicLinkRequestEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="eve@example.com", password="password123!", is_verified=True
        )

    def test_silent_for_unknown_email(self):
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "ghost@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(r.json(), {"status": "queued"})
        mock_send.assert_not_called()

    def test_silent_for_unverified(self):
        User.objects.create_user(
            email="unv@example.com", password="password123!", is_verified=False
        )
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "unv@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_not_called()

    def test_silent_for_inactive(self):
        User.objects.create_user(
            email="inactive@example.com",
            password="password123!",
            is_verified=True,
            is_active=False,
        )
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "inactive@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_not_called()

    def test_eligible_sends_token(self):
        with patch("app.mail.send_magic_link_email") as mock_send:
            r = self.client.post(
                "/api/v1/auth/magic-link/request",
                data={"email": "eve@example.com"},
                content_type="application/json",
            )
        self.assertEqual(r.status_code, 202, r.content)
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(args[0], "eve@example.com")
        self.assertEqual(parse_magic_link_token(args[1]), str(self.user.id))


class MagicLinkExchangeEndpointTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            email="frank@example.com", password="password123!", is_verified=True
        )

    def _exchange(self, token):
        return self.client.post(
            "/api/v1/auth/magic-link/exchange",
            data={"token": token},
            content_type="application/json",
        )

    def test_valid_token_returns_access_token(self):
        r = self._exchange(make_magic_link_token(self.user.id))
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["access_token"])
        self.assertEqual(body["token_type"], "bearer")

    def test_expired_token_returns_410(self):
        token = make_magic_link_token(self.user.id)
        original = signing.time.time
        try:
            signing.time.time = lambda: original() + MAGIC_LINK_MAX_AGE_SECONDS + 60
            r = self._exchange(token)
        finally:
            signing.time.time = original
        self.assertEqual(r.status_code, 410, r.content)

    def test_invalid_token_returns_400(self):
        self.assertEqual(self._exchange("garbage:nope").status_code, 400)

    def test_unknown_user_returns_400(self):
        token = make_magic_link_token("00000000-0000-0000-0000-000000000000")
        self.assertEqual(self._exchange(token).status_code, 400)

    def test_unverified_user_returns_400(self):
        self.user.is_verified = False
        self.user.save(update_fields=["is_verified"])
        self.assertEqual(self._exchange(make_magic_link_token(self.user.id)).status_code, 400)

    def test_inactive_user_returns_400(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self._exchange(make_magic_link_token(self.user.id)).status_code, 400)

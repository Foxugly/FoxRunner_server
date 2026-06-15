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

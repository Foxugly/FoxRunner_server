import uuid
from unittest.mock import MagicMock

from django.test import TestCase

from foxrunner.idempotency import _fingerprint, get_idempotent_response, store_idempotent_response
from ops.models import IdempotencyKey


def _request(headers: dict[str, str]):
    request = MagicMock()
    request.headers = headers
    return request


class IdempotencyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_id = uuid.uuid4()

    def test_fingerprint_is_stable(self):
        self.assertEqual(_fingerprint({"b": 1, "a": 2}), _fingerprint({"a": 2, "b": 1}))

    def test_get_returns_none_when_no_header(self):
        request = _request({})
        self.assertIsNone(get_idempotent_response(request, user_id=self.user_id, payload={"x": 1}))

    def test_store_and_replay(self):
        request = _request({"Idempotency-Key": "key-1"})
        payload = {"a": 1}
        store_idempotent_response(request, user_id=self.user_id, payload=payload, response={"ok": True})
        replay = get_idempotent_response(request, user_id=self.user_id, payload=payload)
        self.assertEqual(replay, {"ok": True})

    def test_replay_with_different_payload_returns_409(self):
        from ninja.errors import HttpError

        request = _request({"Idempotency-Key": "key-2"})
        store_idempotent_response(request, user_id=self.user_id, payload={"a": 1}, response={"ok": True})
        with self.assertRaises(HttpError) as ctx:
            get_idempotent_response(request, user_id=self.user_id, payload={"a": 2})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_concurrent_insert_silent_success_for_same_payload(self):
        request = _request({"Idempotency-Key": "key-3"})
        IdempotencyKey.objects.create(user_id=self.user_id, key="key-3", request_fingerprint=_fingerprint({"a": 1}), response={"ok": True})
        # Second call should NOT raise (same fingerprint)
        store_idempotent_response(request, user_id=self.user_id, payload={"a": 1}, response={"ok": True})

    def test_concurrent_insert_with_different_payload_returns_409(self):
        from ninja.errors import HttpError

        request = _request({"Idempotency-Key": "key-4"})
        IdempotencyKey.objects.create(user_id=self.user_id, key="key-4", request_fingerprint=_fingerprint({"a": 1}), response={"ok": True})
        with self.assertRaises(HttpError) as ctx:
            store_idempotent_response(request, user_id=self.user_id, payload={"a": 2}, response={"ok": False})
        self.assertEqual(ctx.exception.status_code, 409)

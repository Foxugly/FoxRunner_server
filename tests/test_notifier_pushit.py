import os
import unittest
from unittest import mock

from app.config import PushItConfig, PushoverConfig, load_pushit_config
from app.notifier import Notifier


class _FakeResp:
    def __init__(self, status: int = 200):
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _notifier(*, pushit=None, pushover=None) -> Notifier:
    return Notifier(pushover, mock.MagicMock(), pushit=pushit)


class PushItNotifierTests(unittest.TestCase):
    def test_pushit_is_tried_first_and_short_circuits_pushover(self):
        n = _notifier(
            pushit=PushItConfig(app_token="apt_test", base_url="https://x/api/v1"),
            pushover=PushoverConfig(token="t", user_key="u"),
        )
        with mock.patch("app.notifier.requests.post", return_value=_FakeResp(200)) as post:
            self.assertTrue(n.send("hello"))
        post.assert_called_once()
        url = post.call_args_list[0][0][0]
        kwargs = post.call_args_list[0][1]
        self.assertEqual(url, "https://x/api/v1/notifications/app/send/")
        self.assertEqual(kwargs["headers"]["X-App-Token"], "apt_test")
        self.assertEqual(kwargs["json"], {"title": "FoxRunner", "message": "hello"})

    def test_falls_back_to_pushover_when_pushit_fails(self):
        n = _notifier(
            pushit=PushItConfig(app_token="apt_test"),
            pushover=PushoverConfig(token="t", user_key="u"),
        )
        outcomes = [RuntimeError("pushit down"), _FakeResp(200)]

        def _side(*_args, **_kwargs):
            result = outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch("app.notifier.requests.post", side_effect=_side) as post:
            self.assertTrue(n.send("hi"))
        self.assertEqual(post.call_count, 2)  # PushIT (failed) then Pushover
        self.assertEqual(post.call_args_list[1][0][0], "https://api.pushover.net/1/messages.json")

    def test_no_channel_configured_returns_false(self):
        n = _notifier(pushit=None, pushover=None)
        self.assertFalse(n.is_enabled())
        self.assertFalse(n.send("x"))

    def test_pushit_alone_enables_notifier(self):
        n = _notifier(pushit=PushItConfig(app_token="apt_test"))
        self.assertTrue(n.is_enabled())

    def test_load_pushit_config_from_env(self):
        with mock.patch.dict(
            os.environ,
            {"PUSHIT_APP_TOKEN": "apt_xyz", "PUSHIT_API_BASE_URL": "https://h/api/v1"},
            clear=False,
        ):
            cfg = load_pushit_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.app_token, "apt_xyz")
        self.assertEqual(cfg.base_url, "https://h/api/v1")

    def test_load_pushit_config_absent_when_no_token(self):
        with mock.patch.dict(os.environ, {"PUSHIT_APP_TOKEN": ""}, clear=False):
            self.assertIsNone(load_pushit_config())


if __name__ == "__main__":
    unittest.main()

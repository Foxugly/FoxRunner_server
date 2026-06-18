import unittest
from unittest import mock

from app.config import PushItConfig
from app.logger import Logger
from app.main import _build_notifier_resolver
from scenarios.loader import ScenarioData, ScenarioDefinition, build_scenarios_from_map


def _data() -> ScenarioData:
    return ScenarioData(pushovers={}, networks={})


class OwnerNotifierResolverTests(unittest.TestCase):
    def test_loader_parses_owner_user_id(self):
        scenarios = build_scenarios_from_map({"s": {"owner_user_id": "uid-9", "steps": []}})
        self.assertEqual(scenarios["s"].owner_user_id, "uid-9")

    def test_loader_owner_user_id_absent_is_none(self):
        scenarios = build_scenarios_from_map({"s": {"steps": []}})
        self.assertIsNone(scenarios["s"].owner_user_id)

    def test_resolver_uses_owner_config_from_db(self):
        resolver = _build_notifier_resolver(Logger(debug_enabled=False), _data())
        scenario = ScenarioDefinition(scenario_id="s", description="", steps=(), owner_user_id="uid-1")
        with mock.patch("app.main._load_owner_pushit", return_value=PushItConfig(app_token="apt_owner")):
            notifier = resolver(scenario)
        self.assertIsNotNone(notifier.pushit)
        self.assertEqual(notifier.pushit.app_token, "apt_owner")

    def test_resolver_falls_back_to_env_when_owner_has_none(self):
        with mock.patch("app.main.load_pushit_config", return_value=PushItConfig(app_token="apt_env")):
            resolver = _build_notifier_resolver(Logger(debug_enabled=False), _data())
        scenario = ScenarioDefinition(scenario_id="s", description="", steps=(), owner_user_id="uid-1")
        with mock.patch("app.main._load_owner_pushit", return_value=None):
            notifier = resolver(scenario)
        self.assertEqual(notifier.pushit.app_token, "apt_env")

    def test_resolver_none_when_no_owner_and_no_env(self):
        with mock.patch("app.main.load_pushit_config", return_value=None):
            resolver = _build_notifier_resolver(Logger(debug_enabled=False), _data())
        notifier = resolver(None)
        self.assertIsNone(notifier.pushit)


if __name__ == "__main__":
    unittest.main()

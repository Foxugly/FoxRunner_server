import unittest
from unittest.mock import patch

from app.config import NetworkConfig, PushoverConfig, TaskConfig
from app.logger import Logger
from scenarios.loader import ScenarioData, ScenarioDefinition, ScenarioStep
from scenarios.runner import run_task


class TaskRunnerTests(unittest.TestCase):
    def test_dry_run_supports_block_steps(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(
                    type="group",
                    payload={
                        "steps": (
                            ScenarioStep(type="sleep", payload={"seconds": 1}),
                            ScenarioStep(
                                type="repeat",
                                payload={
                                    "times": 2,
                                    "steps": (
                                        ScenarioStep(
                                            type="parallel",
                                            payload={
                                                "steps": (
                                                    ScenarioStep(type="notify", payload={"message": "A"}),
                                                    ScenarioStep(type="notify", payload={"message": "B"}),
                                                )
                                            },
                                        ),
                                    ),
                                },
                            ),
                        )
                    },
                ),
                ScenarioStep(
                    type="try",
                    payload={
                        "try_steps": (ScenarioStep(type="notify", payload={"message": "ok"}),),
                        "catch_steps": (),
                        "finally_steps": (ScenarioStep(type="notify", payload={"message": "done"}),),
                    },
                ),
            ),
        )
        scenario_data = ScenarioData(
            pushovers={"default": PushoverConfig(token="x", user_key="y")},
            networks={"office": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True)},
            default_pushover_key="default",
            default_network_key="office",
        )
        result = run_task(
            TaskConfig(),
            Logger(debug_enabled=False),
            scenario=scenario,
            scenario_data=scenario_data,
            dry_run=True,
            notifier=None,
            network_check=lambda: True,
            network_check_by_key=lambda key: True,
            initial_context={"slot_id": "slot", "scenario_id": "test"},
        )
        self.assertTrue(result.success)

    def test_nested_open_url_creates_driver(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(
                    type="group",
                    payload={"steps": (ScenarioStep(type="open_url", payload={"url": "https://example.test"}),)},
                ),
            ),
        )
        scenario_data = ScenarioData(
            pushovers={"default": PushoverConfig(token="x", user_key="y")},
            networks={"office": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True)},
            default_pushover_key="default",
            default_network_key="office",
        )

        class DummyDriver:
            def __init__(self):
                self.opened = []

            def set_page_load_timeout(self, seconds):
                self.timeout = seconds

            def get(self, url):
                self.opened.append(url)

            def quit(self):
                return None

        created = DummyDriver()
        with patch("scenarios.runner.create_driver", return_value=created):
            result = run_task(
                TaskConfig(),
                Logger(debug_enabled=False),
                scenario=scenario,
                scenario_data=scenario_data,
                dry_run=False,
                notifier=None,
                network_check=lambda: True,
                network_check_by_key=lambda key: True,
                initial_context={"slot_id": "slot", "scenario_id": "test"},
            )
        self.assertTrue(result.success)
        self.assertEqual(created.opened, ["https://example.test"])

    def test_when_supports_extended_conditions(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(type="set_context", payload={"key": "kind", "value": "alpha"}),
                ScenarioStep(type="set_context", payload={"key": "flag", "value": "yes"}, when="context_not_exists:missing"),
                ScenarioStep(type="set_context", payload={"key": "in_ok", "value": "1"}, when="context_in:kind=alpha,beta"),
                ScenarioStep(type="set_context", payload={"key": "re_ok", "value": "1"}, when=r"context_matches:kind=^alp"),
            ),
        )
        scenario_data = ScenarioData(
            pushovers={},
            networks={},
            default_pushover_key=None,
            default_network_key=None,
        )
        result = run_task(
            TaskConfig(),
            Logger(debug_enabled=False),
            scenario=scenario,
            scenario_data=scenario_data,
            dry_run=True,
            notifier=None,
            network_check=lambda: True,
            network_check_by_key=lambda key: True,
            initial_context={},
        )
        self.assertTrue(result.success)

    def test_retry_delay_seconds_waits_between_attempts(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(
                    type="open_url",
                    payload={"url": "https://example.test"},
                    retry=1,
                    retry_delay_seconds=0.5,
                ),
            ),
        )
        scenario_data = ScenarioData(
            pushovers={},
            networks={},
            default_pushover_key=None,
            default_network_key=None,
        )

        class FailingDriver:
            def set_page_load_timeout(self, seconds):
                return None

            def get(self, url):
                raise RuntimeError("boom")

            def quit(self):
                return None

            def save_screenshot(self, path):
                return True

            @property
            def page_source(self):
                return "<html></html>"

        with patch("scenarios.runner.create_driver", return_value=FailingDriver()):
            with patch("scenarios.runner.time.sleep") as sleep_mock:
                result = run_task(
                    TaskConfig(),
                    Logger(debug_enabled=False),
                    scenario=scenario,
                    scenario_data=scenario_data,
                    dry_run=False,
                    notifier=None,
                    network_check=lambda: True,
                    network_check_by_key=lambda key: True,
                    initial_context={"execution_id": "exec-test"},
                )
        self.assertFalse(result.success)
        sleep_mock.assert_any_call(0.5)

    def test_retry_backoff_seconds_increases_delay(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(
                    type="open_url",
                    payload={"url": "https://example.test"},
                    retry=2,
                    retry_delay_seconds=0.5,
                    retry_backoff_seconds=2.0,
                ),
            ),
        )
        scenario_data = ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None)

        class FailingDriver:
            def set_page_load_timeout(self, seconds):
                return None

            def get(self, url):
                raise RuntimeError("boom")

            def quit(self):
                return None

            def save_screenshot(self, path):
                return True

            @property
            def page_source(self):
                return "<html></html>"

        with patch("scenarios.runner.create_driver", return_value=FailingDriver()):
            with patch("scenarios.runner.time.sleep") as sleep_mock:
                result = run_task(
                    TaskConfig(),
                    Logger(debug_enabled=False),
                    scenario=scenario,
                    scenario_data=scenario_data,
                    dry_run=False,
                    notifier=None,
                    network_check=lambda: True,
                    network_check_by_key=lambda key: True,
                    initial_context={"execution_id": "exec-test"},
                )
        self.assertFalse(result.success)
        sleep_mock.assert_any_call(0.5)
        sleep_mock.assert_any_call(1.0)

    def test_continue_on_error_allows_next_step(self):
        scenario = ScenarioDefinition(
            scenario_id="test",
            description="",
            steps=(
                ScenarioStep(type="open_url", payload={"url": "https://example.test"}, continue_on_error=True),
                ScenarioStep(type="set_context", payload={"key": "after", "value": "ok"}),
            ),
        )
        scenario_data = ScenarioData(pushovers={}, networks={}, default_pushover_key=None, default_network_key=None)

        class FailingDriver:
            def set_page_load_timeout(self, seconds):
                return None

            def get(self, url):
                raise RuntimeError("boom")

            def quit(self):
                return None

            def save_screenshot(self, path):
                return True

            @property
            def page_source(self):
                return "<html></html>"

        with patch("scenarios.runner.create_driver", return_value=FailingDriver()):
            result = run_task(
                TaskConfig(),
                Logger(debug_enabled=False),
                scenario=scenario,
                scenario_data=scenario_data,
                dry_run=False,
                notifier=None,
                network_check=lambda: True,
                network_check_by_key=lambda key: True,
                initial_context={"execution_id": "exec-test"},
            )
        self.assertTrue(result.success)

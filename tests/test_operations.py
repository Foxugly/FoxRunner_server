import unittest
from unittest.mock import patch

from app.config import NetworkConfig, PushoverConfig, TaskConfig
from app.logger import Logger
from operations import build_operation_registry
from operations.network_ops import handle_require_enterprise_network
from operations.notify_ops import _render_message
from operations.registry import OperationContext
from scenarios.schema import BLOCK_STEP_TYPES


class OperationTests(unittest.TestCase):
    def test_registry_contains_expected_handlers(self):
        registry = build_operation_registry()
        for key in (
            "open_url",
            "click",
            "wait_for_element",
            "input_text",
            "assert_text",
            "assert_attribute",
            "extract_text_to_context",
            "extract_attribute_to_context",
            "screenshot",
            "select_option",
            "wait_until_url_contains",
            "wait_until_title_contains",
            "close_browser",
            "notify",
            "http_request",
            "require_enterprise_network",
        ):
            self.assertIn(key, registry)
        self.assertIn("group", BLOCK_STEP_TYPES)
        self.assertIn("parallel", BLOCK_STEP_TYPES)
        self.assertIn("repeat", BLOCK_STEP_TYPES)
        self.assertIn("try", BLOCK_STEP_TYPES)

    def test_render_message(self):
        message = _render_message(
            "Slot {slot_id} scenario {scenario_id}",
            {"slot_id": "weekday_evening", "scenario_id": "solidaris_pointer"},
        )
        self.assertEqual(message, "Slot weekday_evening scenario solidaris_pointer")

    def test_notify_can_use_named_pushover(self):
        pushovers = {
            "ops": PushoverConfig(token="token", user_key="user"),
        }
        context = OperationContext(
            driver=None,
            config=TaskConfig(),
            logger=Logger(debug_enabled=False),
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            template_context={"slot_id": "weekday_evening"},
            pushovers=pushovers,
            default_pushover_key=None,
            networks={},
            default_network_key=None,
            parallel_safe_steps=frozenset(),
            dry_run=False,
        )
        handler = build_operation_registry()["notify"]
        with patch("app.notifier.Notifier.send", return_value=True) as send_mock:
            handler(context, {"pushover_key": "ops", "message": "Slot {slot_id}"})
        send_mock.assert_called_once_with("Slot weekday_evening")

    def test_require_enterprise_network_can_use_named_network(self):
        context = OperationContext(
            driver=None,
            config=TaskConfig(),
            logger=Logger(debug_enabled=False),
            notifier=None,
            network_check=None,
            network_check_by_key=lambda key: key == "office",
            template_context={},
            pushovers={},
            default_pushover_key=None,
            networks={
                "office": NetworkConfig((), (), (), (), (), (), (), 1.0, (), True),
            },
            default_network_key="office",
            parallel_safe_steps=frozenset(),
            dry_run=False,
        )
        handle_require_enterprise_network(context, {"network_key": "office"})

    def test_context_operations_update_template_context(self):
        context = OperationContext(
            driver=None,
            config=TaskConfig(),
            logger=Logger(debug_enabled=False),
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            template_context={},
            pushovers={},
            default_pushover_key=None,
            networks={},
            default_network_key=None,
            parallel_safe_steps=frozenset(),
            dry_run=False,
        )
        registry = build_operation_registry()
        registry["set_context"](context, {"key": "name", "value": "Alice"})
        registry["format_context"](context, {"key": "message", "template": "Hi {name}"})
        self.assertEqual(context.template_context["message"], "Hi Alice")

    def test_extract_ops_fill_context_in_dry_run(self):
        context = OperationContext(
            driver=None,
            config=TaskConfig(),
            logger=Logger(debug_enabled=False),
            notifier=None,
            network_check=None,
            network_check_by_key=None,
            template_context={},
            pushovers={},
            default_pushover_key=None,
            networks={},
            default_network_key=None,
            parallel_safe_steps=frozenset(),
            dry_run=True,
        )
        registry = build_operation_registry()
        registry["extract_text_to_context"](
            context,
            {"target": type("T", (), {"key": "txt", "by": "id", "locator": "x", "timeout": 1})()},
        )
        registry["extract_attribute_to_context"](
            context,
            {"target": type("T", (), {"key": "href", "by": "id", "locator": "x", "timeout": 1, "attribute": "href"})()},
        )
        self.assertEqual(context.template_context["txt"], "<dry-run>")
        self.assertEqual(context.template_context["href"], "<dry-run>")

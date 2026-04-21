from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from operations.registry import OperationContext
from operations.selenium_ops import (
    _resolve_by,
    handle_assert_attribute,
    handle_assert_text,
    handle_click,
    handle_close_browser,
    handle_input_text,
    handle_open_url,
    handle_screenshot,
    handle_select_option,
    handle_wait_for_element,
    handle_wait_until_title_contains,
    handle_wait_until_url_contains,
)


def _context(*, driver=None, dry_run: bool = False) -> OperationContext:
    return OperationContext(
        driver=driver,
        config=TaskConfig(),
        logger=Logger(debug_enabled=False),
        notifier=Notifier(None, Logger(debug_enabled=False)),
        network_check=None,
        network_check_by_key=None,
        template_context={},
        pushovers={},
        default_pushover_key=None,
        networks={},
        default_network_key=None,
        parallel_safe_steps=frozenset(),
        dry_run=dry_run,
    )


class SeleniumOpsEdgeTests(unittest.TestCase):
    def test_dry_run_and_missing_driver_paths(self):
        handle_open_url(_context(dry_run=True), {"url": "https://example.com"})
        with self.assertRaises(RuntimeError):
            handle_open_url(_context(), {"url": "https://example.com"})
        with self.assertRaises(RuntimeError):
            handle_click(_context(), {"by": "id", "locator": "submit"})
        with self.assertRaises(ValueError):
            _resolve_by("bad")

    def test_open_click_wait_input_and_screenshot(self):
        driver = MagicMock()
        element = MagicMock()
        wait = MagicMock()
        wait.until.return_value = element
        with TemporaryDirectory() as tmp:
            screenshot_path = Path(tmp) / "screens" / "one.png"
            with patch("operations.selenium_ops.WebDriverWait", return_value=wait):
                context = _context(driver=driver)
                handle_open_url(context, {"url": "https://example.com"})
                handle_click(context, {"by": "id", "locator": "submit", "timeout": 1})
                handle_wait_for_element(context, {"by": "css", "locator": ".ready", "timeout": 1})
                handle_input_text(context, {"by": "name", "locator": "email", "text": "a@example.com", "clear_first": True})
                handle_screenshot(context, {"path": str(screenshot_path)})

        driver.get.assert_called_once_with("https://example.com")
        driver.execute_script.assert_called_once()
        element.click.assert_called_once()
        element.clear.assert_called_once()
        element.send_keys.assert_called_once_with("a@example.com")
        driver.save_screenshot.assert_called_once()

    def test_assert_text_and_attribute_modes(self):
        element = SimpleNamespace(text="Hello world", get_attribute=lambda name: "btn primary")
        wait = MagicMock()
        wait.until.return_value = element
        with patch("operations.selenium_ops.WebDriverWait", return_value=wait):
            context = _context(driver=MagicMock())
            handle_assert_text(context, {"by": "id", "locator": "msg", "text": "Hello", "match": "contains"})
            handle_assert_text(context, {"by": "id", "locator": "msg", "text": "Hello world", "match": "equals"})
            handle_assert_attribute(context, {"by": "id", "locator": "btn", "attribute": "class", "value": "primary", "match": "contains"})
            handle_assert_attribute(context, {"by": "id", "locator": "btn", "attribute": "class", "value": "btn primary", "match": "equals"})
            with self.assertRaises(AssertionError):
                handle_assert_text(context, {"by": "id", "locator": "msg", "text": "Nope", "match": "contains"})
            with self.assertRaises(AssertionError):
                handle_assert_attribute(context, {"by": "id", "locator": "btn", "attribute": "class", "value": "Nope", "match": "equals"})

    def test_select_wait_until_and_close_browser(self):
        element = MagicMock()
        wait = MagicMock()
        wait.until.return_value = element
        select = MagicMock()
        with patch("operations.selenium_ops.WebDriverWait", return_value=wait), patch("operations.selenium_ops.Select", return_value=select):
            context = _context(driver=MagicMock())
            handle_select_option(context, {"by": "id", "locator": "country", "value": "be"})
            handle_select_option(context, {"by": "id", "locator": "country", "visible_text": "Belgium"})
            handle_select_option(context, {"by": "id", "locator": "country", "index": 1})
            with self.assertRaises(ValueError):
                handle_select_option(context, {"by": "id", "locator": "country"})
            handle_wait_until_url_contains(context, {"value": "/done"})
            handle_wait_until_title_contains(context, {"value": "Done"})
            handle_close_browser(context, {})

        select.select_by_value.assert_called_once_with("be")
        select.select_by_visible_text.assert_called_once_with("Belgium")
        select.select_by_index.assert_called_once_with(1)
        self.assertIsNone(context.driver)


if __name__ == "__main__":
    unittest.main()

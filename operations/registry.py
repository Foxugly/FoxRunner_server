from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.config import NetworkConfig, PushoverConfig, TaskConfig
from app.logger import Logger
from app.notifier import Notifier
from scenarios.loader import StepReference


@dataclass
class OperationContext:
    driver: object | None
    config: TaskConfig
    logger: Logger
    notifier: Notifier | None
    network_check: Callable[[], bool] | None
    network_check_by_key: Callable[[str | None], bool] | None
    template_context: dict[str, str]
    pushovers: dict[str, PushoverConfig]
    default_pushover_key: str | None
    networks: dict[str, NetworkConfig]
    default_network_key: str | None
    parallel_safe_steps: frozenset[str]
    dry_run: bool = False

    def resolve_ref(self, payload: dict, kind: str, legacy_key: str | None = None) -> str | None:
        ref = payload.get("ref")
        if isinstance(ref, StepReference):
            value = getattr(ref, kind, None)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"La reference '{kind}' doit etre une chaine.")
            if isinstance(value, str):
                return value
        if isinstance(ref, dict):
            value = ref.get(kind)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"La reference '{kind}' doit etre une chaine.")
            if isinstance(value, str):
                return value
        if legacy_key is not None:
            legacy_value = payload.get(legacy_key)
            if legacy_value is not None and not isinstance(legacy_value, str):
                raise ValueError(f"'{legacy_key}' doit etre une chaine.")
            if isinstance(legacy_value, str):
                return legacy_value
        if kind == "pushover":
            return self.default_pushover_key
        if kind == "network":
            return self.default_network_key
        return None


def build_operation_registry() -> dict[str, Callable[[OperationContext, dict], None]]:
    from .context_ops import handle_format_context, handle_set_context
    from .http_ops import handle_http_request
    from .network_ops import handle_require_enterprise_network
    from .notify_ops import handle_notify
    from .selenium_ops import (
        handle_assert_attribute,
        handle_assert_text,
        handle_click,
        handle_close_browser,
        handle_extract_attribute_to_context,
        handle_extract_text_to_context,
        handle_input_text,
        handle_open_url,
        handle_screenshot,
        handle_select_option,
        handle_wait_for_element,
        handle_wait_until_title_contains,
        handle_wait_until_url_contains,
    )
    from .time_ops import handle_sleep, handle_sleep_random

    return {
        "open_url": handle_open_url,
        "click": handle_click,
        "wait_for_element": handle_wait_for_element,
        "input_text": handle_input_text,
        "assert_text": handle_assert_text,
        "assert_attribute": handle_assert_attribute,
        "extract_text_to_context": handle_extract_text_to_context,
        "extract_attribute_to_context": handle_extract_attribute_to_context,
        "screenshot": handle_screenshot,
        "select_option": handle_select_option,
        "wait_until_url_contains": handle_wait_until_url_contains,
        "wait_until_title_contains": handle_wait_until_title_contains,
        "close_browser": handle_close_browser,
        "sleep": handle_sleep,
        "sleep_random": handle_sleep_random,
        "notify": handle_notify,
        "http_request": handle_http_request,
        "require_enterprise_network": handle_require_enterprise_network,
        "set_context": handle_set_context,
        "format_context": handle_format_context,
    }


def build_parallel_safe_steps() -> frozenset[str]:
    return frozenset(
        {
            "sleep",
            "sleep_random",
            "notify",
            "http_request",
            "require_enterprise_network",
            "set_context",
            "format_context",
        }
    )

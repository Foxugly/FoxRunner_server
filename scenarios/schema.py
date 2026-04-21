from __future__ import annotations

DSL_SCHEMA_VERSION = 1

ATOMIC_STEP_TYPES = {
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
    "sleep",
    "sleep_random",
    "notify",
    "http_request",
    "require_enterprise_network",
    "set_context",
    "format_context",
}

BLOCK_STEP_TYPES = {
    "group",
    "parallel",
    "repeat",
    "try",
}

SUPPORTED_STEP_TYPES = ATOMIC_STEP_TYPES | BLOCK_STEP_TYPES

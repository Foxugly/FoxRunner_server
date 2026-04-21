from __future__ import annotations

from typing import Any

SECRET_MARKERS = ("secret", "password", "token", "key", "authorization", "client_state", "clientstate")
REDACTED = "***redacted***"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: REDACTED if _is_secret_key(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def redact_text(value: str) -> str:
    redacted = value
    for marker in SECRET_MARKERS:
        if marker in redacted.lower():
            return REDACTED
    return redacted


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)

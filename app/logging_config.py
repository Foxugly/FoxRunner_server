"""Shared logging utilities.

The :class:`JsonFormatter` is referenced from the Django ``LOGGING``
config (``foxrunner/settings.py``) and stays framework-agnostic so the
CLI engine can reuse it too.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.redaction import redact


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "client"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

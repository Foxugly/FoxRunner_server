from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from api.redaction import redact


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


def configure_api_logging(*, json_enabled: bool) -> None:
    logger = logging.getLogger("smiley.api")
    import os

    if os.getenv("APP_ENV", "").lower() == "test" or os.getenv("API_LOG_HTTP_ENABLED", "true").lower() != "true":
        logger.disabled = True
        return
    logger.disabled = False
    logger.setLevel(logging.INFO)
    if logger.handlers:
        for handler in logger.handlers:
            handler.setFormatter(JsonFormatter() if json_enabled else logging.Formatter("%(levelname)s %(name)s %(message)s"))
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if json_enabled else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False

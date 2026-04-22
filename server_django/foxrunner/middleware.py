"""HTTP middleware shared across the Django project.

``RequestContextMiddleware`` ensures every response carries an
``X-Request-ID`` header (mirrored from the incoming request when present)
and emits a structured access log entry through the ``smiley.api``
logger, matching the FastAPI implementation.
"""

from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger("smiley.api")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.request_id = request_id
        start = time.perf_counter()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            response["X-Request-ID"] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client": request.META.get("REMOTE_ADDR"),
                },
            )

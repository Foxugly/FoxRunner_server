"""Payload-size cap middleware.

Two complementary mechanisms apply the ``DATA_UPLOAD_MAX_MEMORY_SIZE``
setting:

1. **Pre-flight Content-Length check (this middleware).** When the
   client advertises a ``Content-Length`` header that already exceeds the
   limit we short-circuit immediately with a 413 + JSON envelope, before
   the body is read. This matches the FastAPI implementation and avoids
   draining a multi-megabyte payload only to reject it.
2. **Lazy ``RequestDataTooBig`` interception.** Django raises this
   exception the first time a view materialises an oversized body.
   ``__call__`` wraps ``get_response`` so the exception is replaced with
   the same JSON 413 envelope rather than the default HTML page or the
   Ninja "internal_error" handler.

Chunked-body enforcement at the ASGI layer (the FastAPI
``PayloadLimitMiddleware`` covered the ``Transfer-Encoding: chunked``
case where Content-Length is absent) is intentionally NOT ported here:
the current Django deployment runs under Gunicorn/WSGI which buffers
bodies and provides Content-Length. See the ``TODO(asgi-deploy)`` comment
in ``settings.py`` for follow-up if Daphne/Uvicorn becomes the production
target.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse


class PayloadLimitMiddleware:
    """Pre-flight Content-Length check + lazy RequestDataTooBig handler."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        max_bytes = _max_bytes()
        content_length = request.META.get("CONTENT_LENGTH")
        if content_length:
            try:
                declared = int(content_length)
            except (TypeError, ValueError):
                return _too_large(max_bytes)
            if declared > max_bytes:
                return _too_large(max_bytes)
        try:
            return self.get_response(request)
        except RequestDataTooBig:
            return _too_large(max_bytes)

    def process_exception(self, request, exception):
        # Defensive: some Django call paths surface RequestDataTooBig
        # through ``process_exception`` rather than re-raising into the
        # middleware ``__call__`` frame.
        if isinstance(exception, RequestDataTooBig):
            return _too_large(_max_bytes())
        return None


def _max_bytes() -> int:
    return int(getattr(settings, "DATA_UPLOAD_MAX_MEMORY_SIZE", 1048576))


def _too_large(max_bytes: int) -> HttpResponse:
    body = json.dumps(
        {
            "code": "payload_too_large",
            "message": "Payload trop volumineux.",
            "details": {"max_bytes": max_bytes},
        }
    )
    return HttpResponse(body, status=413, content_type="application/json")

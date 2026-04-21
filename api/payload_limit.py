from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _too_large(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"code": "payload_too_large", "message": "Payload trop volumineux.", "details": {"max_bytes": max_bytes}},
    )


def install_payload_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def payload_limit(request: Request, call_next):
        max_bytes = int(os.getenv("API_MAX_BODY_BYTES", "1048576"))
        raw_length = request.headers.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                return _too_large(max_bytes)
            if declared > max_bytes:
                return _too_large(max_bytes)
            return await call_next(request)

        # No content-length (e.g. Transfer-Encoding: chunked). Wrap the ASGI receive
        # channel to count bytes as they arrive and abort past the limit.
        original_receive = request.receive
        total = 0

        async def limited_receive() -> dict:
            nonlocal total
            message = await original_receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                total += len(body)
                if total > max_bytes:
                    raise _PayloadTooLargeError()
            return message

        request._receive = limited_receive  # noqa: SLF001 - Starlette exposes no public hook
        try:
            return await call_next(request)
        except _PayloadTooLargeError:
            return _too_large(max_bytes)


class _PayloadTooLargeError(Exception):
    pass

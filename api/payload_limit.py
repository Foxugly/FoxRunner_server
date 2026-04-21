from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse


def _too_large(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"code": "payload_too_large", "message": "Payload trop volumineux.", "details": {"max_bytes": max_bytes}},
    )


class PayloadLimitMiddleware:
    """ASGI middleware that enforces API_MAX_BODY_BYTES on both Content-Length
    and chunked request bodies.

    Implemented at the ASGI layer (not via BaseHTTPMiddleware) so that
    wrapping ``receive`` actually reaches the downstream application.
    BaseHTTPMiddleware rebuilds its own receive channel from the materialized
    Request body, which makes `request._receive` swapping a no-op for the
    chunked case.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = int(os.getenv("API_MAX_BODY_BYTES", "1048576"))
        content_length = _header_value(scope, b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                await _too_large(max_bytes)(scope, receive, send)
                return
            if declared > max_bytes:
                await _too_large(max_bytes)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        total = 0
        rejected = False

        async def limited_receive() -> dict:
            nonlocal total, rejected
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                total += len(body)
                if total > max_bytes:
                    rejected = True
                    # Drain the remaining body so the client doesn't hang.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: dict) -> None:
            if rejected:
                # Replace whatever the downstream app tries to send with a 413.
                if message["type"] == "http.response.start":
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 413,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                    return
                if message["type"] == "http.response.body":
                    await send({"type": "http.response.body", "body": _too_large_body(max_bytes), "more_body": False})
                    return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)


def _header_value(scope, key: bytes) -> bytes | None:
    for header_key, header_value in scope.get("headers", []):
        if header_key == key:
            return header_value
    return None


def _too_large_body(max_bytes: int) -> bytes:
    import json

    return json.dumps(
        {"code": "payload_too_large", "message": "Payload trop volumineux.", "details": {"max_bytes": max_bytes}},
    ).encode("utf-8")


def install_payload_limit(app: FastAPI) -> None:
    app.add_middleware(PayloadLimitMiddleware)

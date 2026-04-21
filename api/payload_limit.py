from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def install_payload_limit(app: FastAPI) -> None:
    @app.middleware("http")
    async def payload_limit(request: Request, call_next):
        max_bytes = int(os.getenv("API_MAX_BODY_BYTES", "1048576"))
        raw_length = request.headers.get("content-length")
        if raw_length is not None and int(raw_length) > max_bytes:
            return JSONResponse(status_code=413, content={"code": "payload_too_large", "message": "Payload trop volumineux.", "details": {"max_bytes": max_bytes}})
        return await call_next(request)

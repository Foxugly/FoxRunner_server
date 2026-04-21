from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

_WINDOWS: dict[str, deque[float]] = defaultdict(deque)


def install_rate_limit(app) -> None:
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next: Callable):
        if os.getenv("API_RATE_LIMIT_ENABLED", "true").lower() != "true":
            return await call_next(request)
        if not _is_limited_path(request.url.path):
            return await call_next(request)

        window_seconds = int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
        max_requests = int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "60"))
        key = f"{request.client.host if request.client else 'unknown'}:{request.url.path}"
        now = time.time()
        bucket = _WINDOWS[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            return JSONResponse(
                status_code=429,
                content={"code": "rate_limited", "message": "Trop de requetes.", "details": None},
            )
        bucket.append(now)
        return await call_next(request)


def _is_limited_path(path: str) -> bool:
    normalized = path.removeprefix("/api/v1")
    return normalized.startswith("/auth/") or normalized in {"/graph/webhook", "/graph/lifecycle"}

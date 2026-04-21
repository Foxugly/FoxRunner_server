from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("smiley.api.rate_limit")

# Fallback in-process window. Only used when Redis is unavailable; single-worker
# dev environments. Multi-worker deployments must rely on the Redis backend
# below so limits aren't multiplied by the worker count.
_WINDOWS: dict[str, deque[float]] = defaultdict(deque)

_REDIS_CLIENT = None
_REDIS_DISABLED = False


def _get_redis_client():
    global _REDIS_CLIENT, _REDIS_DISABLED
    if _REDIS_DISABLED:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    url = os.getenv("API_RATE_LIMIT_REDIS_URL") or os.getenv("CELERY_BROKER_URL")
    if not url or not url.startswith("redis://"):
        _REDIS_DISABLED = True
        return None
    try:
        from redis.asyncio import Redis

        _REDIS_CLIENT = Redis.from_url(url, decode_responses=False)
        return _REDIS_CLIENT
    except Exception as exc:
        logger.warning("Rate limit Redis backend unavailable, falling back to in-process: %s", exc)
        _REDIS_DISABLED = True
        return None


async def _allow_redis(client, key: str, window_seconds: int, max_requests: int) -> bool:
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    min_score = now_ms - window_ms
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, min_score)
    pipe.zadd(key, {str(now_ms): now_ms})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    _, _, count, _ = await pipe.execute()
    return int(count) <= max_requests


def install_rate_limit(app) -> None:
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next: Callable):
        if os.getenv("API_RATE_LIMIT_ENABLED", "true").lower() != "true":
            return await call_next(request)
        if not _is_limited_path(request.url.path):
            return await call_next(request)

        window_seconds = int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
        max_requests = int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "60"))
        key = f"ratelimit:{request.client.host if request.client else 'unknown'}:{request.url.path}"

        client = _get_redis_client()
        if client is not None:
            try:
                if not await _allow_redis(client, key, window_seconds, max_requests):
                    return _too_many()
                return await call_next(request)
            except Exception as exc:
                logger.warning("Rate limit Redis call failed, falling back to in-process: %s", exc)

        now = time.time()
        bucket = _WINDOWS[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            return _too_many()
        bucket.append(now)
        return await call_next(request)


def _too_many() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"code": "rate_limited", "message": "Trop de requetes.", "details": None},
    )


def _is_limited_path(path: str) -> bool:
    normalized = path.removeprefix("/api/v1")
    return normalized.startswith("/auth/") or normalized in {"/graph/webhook", "/graph/lifecycle"}

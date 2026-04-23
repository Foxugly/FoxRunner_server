"""Sliding-window rate limit middleware (Django port of ``api/rate_limit.py``).

Backend strategy:
    * Primary: Redis sorted-set sliding window via ``django_redis``. The same
      Redis instance that powers the Celery broker is reused unless
      ``API_RATE_LIMIT_REDIS_URL`` is set.
    * Fallback: in-process ``deque`` per (client, path) bucket. Single-worker
      dev only -- multi-worker deployments must rely on Redis to avoid
      multiplying the limit by the worker count.

Limited paths (after stripping ``/api/v1``):
    * ``/auth/*``
    * ``/graph/webhook``
    * ``/graph/lifecycle``

Toggled via ``API_RATE_LIMIT_ENABLED`` (default ``true``). Window and quota
come from ``API_RATE_LIMIT_WINDOW_SECONDS`` and ``API_RATE_LIMIT_MAX_REQUESTS``.
On limit, the middleware returns a 429 with the standard
``{code, message, details}`` envelope.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque

from django.http import HttpResponse

logger = logging.getLogger("foxrunner.api.rate_limit")

# Fallback in-process windows. Module-level so all middleware instances share
# the same buckets (Django typically instantiates middleware once per worker).
_WINDOWS: dict[str, deque[float]] = defaultdict(deque)


class RateLimitMiddleware:
    """Sliding-window per-IP+path rate limiter.

    Honoured environment variables:
        * ``API_RATE_LIMIT_ENABLED`` (default ``true``)
        * ``API_RATE_LIMIT_WINDOW_SECONDS`` (default ``60``)
        * ``API_RATE_LIMIT_MAX_REQUESTS`` (default ``60``)
        * ``API_RATE_LIMIT_REDIS_URL`` (optional; falls back to
          ``CELERY_BROKER_URL`` via the default ``django_redis`` cache)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.getenv("API_RATE_LIMIT_ENABLED", "true").lower() != "true":
            return self.get_response(request)
        if not _is_limited_path(request.path):
            return self.get_response(request)

        window_seconds = int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
        max_requests = int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "60"))
        client_ip = _client_ip(request)
        key = f"ratelimit:{client_ip}:{request.path}"

        client = _get_redis_client()
        if client is not None:
            try:
                allowed = _allow_redis(client, key, window_seconds, max_requests)
                if not allowed:
                    return _too_many()
                return self.get_response(request)
            except Exception as exc:
                logger.warning("Rate limit Redis call failed, falling back to in-process: %s", exc)
                _trip_redis_breaker()

        # In-process fallback
        now = time.time()
        bucket = _WINDOWS[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            return _too_many()
        bucket.append(now)
        return self.get_response(request)


def _is_limited_path(path: str) -> bool:
    normalized = path.removeprefix("/api/v1")
    return normalized.startswith("/auth/") or normalized in {"/graph/webhook", "/graph/lifecycle"}


def _client_ip(request) -> str:
    return request.META.get("REMOTE_ADDR") or "unknown"


# Circuit-breaker timestamp. After a failed Redis call we skip Redis for
# REDIS_RECOVERY_SECONDS so we don't pay the connect-timeout cost on every
# request when Redis is down. ``0`` means "Redis is healthy / not yet probed".
_REDIS_DISABLED_UNTIL: float = 0.0
REDIS_RECOVERY_SECONDS = 30.0


def _get_redis_client():
    """Return a raw Redis client through ``django_redis`` or ``None`` on failure.

    Imported lazily so test suites that patch ``django_redis.get_redis_connection``
    pick up the patched symbol on every request. Honours a short circuit
    breaker (``_REDIS_DISABLED_UNTIL``) to avoid hammering an unreachable
    Redis with connect attempts on every request.
    """
    global _REDIS_DISABLED_UNTIL
    if _REDIS_DISABLED_UNTIL and time.time() < _REDIS_DISABLED_UNTIL:
        return None
    try:
        from django_redis import get_redis_connection

        return get_redis_connection("default")
    except Exception as exc:
        logger.warning("Rate limit Redis backend unavailable, falling back to in-process: %s", exc)
        _REDIS_DISABLED_UNTIL = time.time() + REDIS_RECOVERY_SECONDS
        return None


def _trip_redis_breaker() -> None:
    """Mark Redis as unavailable for ``REDIS_RECOVERY_SECONDS``."""
    global _REDIS_DISABLED_UNTIL
    _REDIS_DISABLED_UNTIL = time.time() + REDIS_RECOVERY_SECONDS


def _allow_redis(client, key: str, window_seconds: int, max_requests: int) -> bool:
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    min_score = now_ms - window_ms
    pipe = client.pipeline()
    pipe.zremrangebyscore(key, 0, min_score)
    pipe.zadd(key, {str(now_ms): now_ms})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    _, _, count, _ = pipe.execute()
    return int(count) <= max_requests


def _too_many() -> HttpResponse:
    body = json.dumps({"code": "rate_limited", "message": "Trop de requetes.", "details": None})
    return HttpResponse(body, status=429, content_type="application/json")

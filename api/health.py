from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.celery_app import celery_app
from api.graph import is_graph_configured


async def readiness(session: AsyncSession) -> dict[str, object]:
    checks: dict[str, object] = {}
    try:
        await session.execute(text("select 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    checks["graph_configured"] = is_graph_configured()
    checks["redis"] = _check_redis()
    checks["celery"] = _check_celery()
    celery_required = os.getenv("API_REQUIRE_CELERY_WORKER", "false").lower() == "true"
    ok = checks.get("database") == "ok" and checks.get("redis") == "ok" and (not celery_required or checks.get("celery") == "ok")
    return {"status": "ok" if ok else "degraded", "checks": checks}


def _check_redis() -> str:
    try:
        import redis

        client = redis.Redis.from_url(os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"), socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return "ok"
    except Exception as exc:
        return f"error: {exc}"


def _check_celery() -> str:
    try:
        replies = celery_app.control.ping(timeout=1)
        return "ok" if replies else "no_workers"
    except Exception as exc:
        return f"error: {exc}"

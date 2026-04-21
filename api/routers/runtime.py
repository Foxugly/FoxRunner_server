from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User, current_active_user
from api.db import get_async_session
from api.dependencies import get_config, get_service, require_superuser
from api.health import readiness
from api.monitoring import monitoring_summary
from api.schemas import ClientConfigPayload, ConfigValidationPayload, HealthPayload, MonitoringSummaryPayload, ReadyPayload, StatusPayload, TimezoneListPayload, VersionPayload
from api.settings import list_settings
from api.timezones import COMMON_TIMEZONES, DEFAULT_TIMEZONE
from api.version import API_VERSION, APP_NAME
from app.config import AppConfig
from app.logger import Logger
from app.main import validate_config
from scheduler.service import SchedulerService

router = APIRouter(tags=["runtime"])


@router.get("/health", response_model=HealthPayload)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=ReadyPayload)
async def ready(session: AsyncSession = Depends(get_async_session)) -> dict[str, object]:
    return await readiness(session)


@router.get("/status", response_model=StatusPayload)
async def status_endpoint(session: AsyncSession = Depends(get_async_session)) -> dict[str, object]:
    ready_state = await readiness(session)
    status_value = str(ready_state.get("status", "degraded"))
    return {
        "status": status_value,
        "api_version": API_VERSION,
        "environment": os.getenv("APP_ENV", "local"),
        "ready": status_value == "ok",
        "checks": ready_state.get("checks", {}),
    }


@router.get("/version", response_model=VersionPayload)
def version() -> dict[str, object]:
    return {
        "name": APP_NAME,
        "api_version": API_VERSION,
        "environment": os.getenv("APP_ENV", "local"),
    }


@router.get("/timezones/common", response_model=TimezoneListPayload)
def common_timezones() -> dict[str, object]:
    return {"default_timezone": DEFAULT_TIMEZONE, "timezones": list(COMMON_TIMEZONES)}


@router.get("/config/client", response_model=ClientConfigPayload)
async def client_config(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    records = await list_settings(session)
    features = {
        record.key.removeprefix("feature."): bool((record.value or {}).get("enabled", False))
        for record in records
        if record.key.startswith("feature.") and (current_user.is_superuser or not record.key.startswith("feature.admin."))
    }
    return {
        "api_version": API_VERSION,
        "environment": os.getenv("APP_ENV", "local"),
        "default_timezone": DEFAULT_TIMEZONE,
        "features": features,
    }


@router.get("/runtime")
def runtime(service: SchedulerService = Depends(get_service)) -> dict[str, object]:
    return service.dump_runtime()


@router.get("/config/validate", response_model=ConfigValidationPayload)
def validate_current_config(config: AppConfig = Depends(get_config)) -> dict[str, object]:
    logger = Logger(debug_enabled=False)
    exit_code = validate_config(config, logger)
    return {"valid": exit_code == 0, "exit_code": exit_code}


@router.get("/monitoring/summary", tags=["monitoring"], response_model=MonitoringSummaryPayload)
async def monitoring_summary_endpoint(
    stuck_after_minutes: int = Query(default=30, ge=1),
    graph_expiring_hours: int = Query(default=24, ge=1),
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> dict[str, object]:
    require_superuser(current_user)
    return await monitoring_summary(session, stuck_after_minutes=stuck_after_minutes, graph_expiring_hours=graph_expiring_hours)


@router.get("/metrics", tags=["monitoring"])
async def metrics_endpoint(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(current_active_user),
) -> PlainTextResponse:
    require_superuser(current_user)
    summary = await monitoring_summary(session)
    jobs = summary["jobs"]
    graph = summary["graph"]
    lines = [
        "# HELP smiley_jobs_total Total persisted jobs.",
        "# TYPE smiley_jobs_total gauge",
        f"smiley_jobs_total {jobs['total']}",
        "# HELP smiley_jobs_failed Failed jobs.",
        "# TYPE smiley_jobs_failed gauge",
        f"smiley_jobs_failed {jobs['failed']}",
        "# HELP smiley_jobs_stuck Queued or running jobs older than threshold.",
        "# TYPE smiley_jobs_stuck gauge",
        f"smiley_jobs_stuck {jobs['stuck']}",
        "# HELP smiley_jobs_by_status Jobs grouped by status.",
        "# TYPE smiley_jobs_by_status gauge",
        *[f'smiley_jobs_by_status{{status="{status}"}} {count}' for status, count in sorted(jobs.get("by_status", {}).items())],
        "# HELP smiley_graph_subscriptions_expiring Graph subscriptions close to expiration.",
        "# TYPE smiley_graph_subscriptions_expiring gauge",
        f"smiley_graph_subscriptions_expiring {graph['subscriptions_expiring']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

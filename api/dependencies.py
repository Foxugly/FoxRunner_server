from __future__ import annotations

import logging
from dataclasses import replace

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User
from api.catalog import load_scheduler_catalog
from api.permissions import require_superuser as require_superuser
from api.redaction import redact_text
from api.serializers import serialize_user as serialize_user
from api.timezones import validate_timezone_name
from app.config import AppConfig, load_config
from app.main import build_runtime_services, build_runtime_services_from_catalog
from scheduler.service import SchedulerService

logger = logging.getLogger("smiley.api.dependencies")


def get_config() -> AppConfig:
    return load_config()


def actor_id(user: User) -> str:
    return str(user.id)


def get_service(config: AppConfig = Depends(get_config)) -> SchedulerService:
    try:
        return build_runtime_services(config)
    except Exception as exc:
        # Log the raw exception for operators but only surface a redacted
        # message to API clients — the underlying error may carry file paths
        # or secrets (cf. api.errors redaction policy).
        logger.exception("get_service failed to build runtime services")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Impossible de charger les services FoxRunner: {redact_text(str(exc))}",
        ) from exc


async def build_service_from_db(config: AppConfig, session: AsyncSession, *, timezone_name: str | None = None) -> SchedulerService:
    if timezone_name is not None:
        config = replace(config, runtime=replace(config.runtime, timezone_name=validate_timezone_name(timezone_name)))
    slots, scenarios = await load_scheduler_catalog(session)
    return build_runtime_services_from_catalog(config, slots, scenarios)

from __future__ import annotations

from dataclasses import replace

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import User
from api.catalog import load_scheduler_catalog
from api.permissions import require_superuser as require_superuser
from api.serializers import serialize_user as serialize_user
from api.timezones import validate_timezone_name
from app.config import AppConfig, load_config
from app.main import build_runtime_services, build_runtime_services_from_catalog
from scheduler.service import SchedulerService


def get_config() -> AppConfig:
    return load_config()


def actor_id(user: User) -> str:
    return str(user.id)


def get_service(config: AppConfig = Depends(get_config)) -> SchedulerService:
    try:
        return build_runtime_services(config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Impossible de charger les services FoxRunner: {exc}",
        ) from exc


async def build_service_from_db(config: AppConfig, session: AsyncSession, *, timezone_name: str | None = None) -> SchedulerService:
    if timezone_name is not None:
        config = replace(config, runtime=replace(config.runtime, timezone_name=validate_timezone_name(timezone_name)))
    slots, scenarios = await load_scheduler_catalog(session)
    return build_runtime_services_from_catalog(config, slots, scenarios)

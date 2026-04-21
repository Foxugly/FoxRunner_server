from __future__ import annotations

import os
from contextlib import asynccontextmanager

import truststore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import (
    UserCreate,
    UserRead,
    UserUpdate,
    auth_backend,
    ensure_production_auth_secret,
    fastapi_users,
)
from api.catalog import seed_catalog_from_json
from api.db import async_session_maker, create_db_and_tables
from api.dependencies import get_config as get_config
from api.dependencies import get_service as get_service
from api.errors import install_error_handlers
from api.http_logging import install_http_logging
from api.logging_config import configure_api_logging
from api.payload_limit import install_payload_limit
from api.rate_limit import install_rate_limit
from api.routers.admin import router as admin_router
from api.routers.artifacts import router as artifacts_router
from api.routers.catalog import router as catalog_router
from api.routers.graph import router as graph_router
from api.routers.jobs import router as jobs_router
from api.routers.runtime import router as runtime_router
from api.routers.users import router as users_router
from app.config import load_config

truststore.inject_into_ssl()

__all__ = ["app", "create_app", "get_config", "get_service"]


OPENAPI_TAGS = [
    {"name": "auth", "description": "Authentification et reset password."},
    {"name": "users", "description": "Utilisateur courant et routes FastAPI Users."},
    {"name": "runtime", "description": "Sante, version, configuration et runtime."},
    {"name": "admin", "description": "Operations administrateur."},
    {"name": "audit", "description": "Journal d'audit."},
    {"name": "scenarios", "description": "Catalogue scenarios et partages."},
    {"name": "steps", "description": "CRUD des etapes DSL."},
    {"name": "slots", "description": "Creneaux planifies."},
    {"name": "jobs", "description": "Jobs Celery persistants."},
    {"name": "artifacts", "description": "Screenshots et pages capturees."},
    {"name": "graph", "description": "Microsoft Graph et webhooks."},
    {"name": "monitoring", "description": "Indicateurs d'exploitation."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_production_auth_secret(os.getenv("APP_ENV", "local"), os.getenv("AUTH_SECRET", ""))
    if os.getenv("API_CREATE_TABLES_ON_STARTUP", "true").lower() == "true":
        await create_db_and_tables()
    config = load_config()
    async with async_session_maker() as session:
        await seed_catalog_from_json(session, config.runtime.scenarios_file, config.runtime.slots_file)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="FoxRunner API",
        version="1.0.0",
        description="API de pilotage du scheduler et des scenarios FoxRunner.",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    configure_api_logging(json_enabled=os.getenv("API_LOG_JSON", os.getenv("APP_LOG_JSON", "false")).lower() == "true")
    install_error_handlers(app)
    install_http_logging(app)
    install_payload_limit(app)
    install_rate_limit(app)

    cors_origins = [origin.strip() for origin in os.getenv("API_CORS_ORIGINS", "http://localhost:4200").split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    if os.getenv("API_ENABLE_LEGACY_ROUTES", "true").lower() == "true":
        include_api_routes(app, include_in_schema=False)
    include_api_routes(app, prefix="/api/v1", include_in_schema=True)
    return app


def include_api_routes(app: FastAPI, *, prefix: str = "", include_in_schema: bool = True) -> None:
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix=f"{prefix}/auth/jwt",
        tags=["auth"],
        include_in_schema=include_in_schema,
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix=f"{prefix}/auth",
        tags=["auth"],
        include_in_schema=include_in_schema,
    )
    app.include_router(
        fastapi_users.get_reset_password_router(),
        prefix=f"{prefix}/auth",
        tags=["auth"],
        include_in_schema=include_in_schema,
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix=f"{prefix}/users",
        tags=["users"],
        include_in_schema=include_in_schema,
    )
    app.include_router(runtime_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(users_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(artifacts_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(admin_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(graph_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(jobs_router, prefix=prefix, include_in_schema=include_in_schema)
    app.include_router(catalog_router, prefix=prefix, include_in_schema=include_in_schema)


app = create_app()

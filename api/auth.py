from __future__ import annotations

import os
import uuid

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import SQLAlchemyUserDatabase
from pydantic import field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_async_session
from api.mail import send_password_reset_email
from api.models import User
from api.timezones import DEFAULT_TIMEZONE, validate_timezone_name

SECRET = os.getenv("AUTH_SECRET", "change-me-before-production-32-bytes-minimum")
JWT_LIFETIME_SECONDS = int(os.getenv("AUTH_JWT_LIFETIME_SECONDS", "3600"))
APP_ENV = os.getenv("APP_ENV", "development").lower()

if APP_ENV in {"production", "prod"} and SECRET == "change-me-before-production-32-bytes-minimum":
    raise RuntimeError("AUTH_SECRET doit etre configure en production.")


class UserRead(schemas.BaseUser[uuid.UUID]):
    timezone_name: str = DEFAULT_TIMEZONE


class UserCreate(schemas.BaseUserCreate):
    timezone_name: str = DEFAULT_TIMEZONE

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return validate_timezone_name(value)


class UserUpdate(schemas.BaseUserUpdate):
    timezone_name: str | None = None

    @field_validator("timezone_name")
    @classmethod
    def validate_optional_timezone(cls, value: str | None) -> str | None:
        return None if value is None else validate_timezone_name(value)


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request=None) -> None:
        return None

    async def on_after_forgot_password(self, user: User, token: str, request=None) -> None:
        send_password_reset_email(user.email, token)


async def get_user_manager(user_db=Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=SECRET, lifetime_seconds=JWT_LIFETIME_SECONDS)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

current_active_user = fastapi_users.current_user(active=True)


def user_api_id(user: User) -> str:
    return str(user.id)


def ensure_user_scope(user_id: str, user: User) -> None:
    from api.permissions import require_user_scope

    require_user_scope(user_id, user)

"""Django settings for the FoxRunner backend migration.

Conventions:
    - All runtime configuration comes from environment variables. Defaults
      are dev-friendly and safe to run locally.
    - ``DATABASE_URL`` is read first, falling back to ``AUTH_DATABASE_URL``
      (the FastAPI env var) translated to a sync driver so the two
      backends can point at the same database during the transition.
    - Timezone policy mirrors ADR 006: store everything in UTC, expose
      ISO 8601 timestamps, let the frontend render in the user's profile
      timezone.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# The .env file lives at the repo root while both backends coexist.
load_dotenv(REPO_ROOT / ".env")

logger = logging.getLogger(__name__)


# --- Core ---------------------------------------------------------------

APP_ENV = os.getenv("APP_ENV", "development").lower()
DEBUG = APP_ENV not in {"production", "prod"}

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or os.getenv("AUTH_SECRET", "change-me-before-production-32-bytes-minimum")
if APP_ENV in {"production", "prod"}:
    if SECRET_KEY == "change-me-before-production-32-bytes-minimum":
        raise RuntimeError("DJANGO_SECRET_KEY / AUTH_SECRET must be set in production.")
    if len(SECRET_KEY) < 32:
        raise RuntimeError("DJANGO_SECRET_KEY / AUTH_SECRET must be at least 32 characters in production.")

ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if host.strip()]

# Suppress models.E034 (constraint/index name length > 30): a legacy Oracle limit.
# We intentionally preserve the Alembic-era index names (e.g. `ix_execution_history_scenario_executed_at`,
# 41 chars) so the schema stays diff-free against the existing PostgreSQL/SQLite tables.
# PostgreSQL allows up to 63 chars; SQLite is unlimited. Oracle is not a target.
SILENCED_SYSTEM_CHECKS = ["models.E034"]


# --- Applications -------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "djoser",
    # Local
    "accounts",
    "catalog",
    "ops",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "foxrunner.middleware.RequestContextMiddleware",
    # Order: rate limiting and payload guards run BEFORE Django's CommonMiddleware
    # so over-limit requests are short-circuited without touching auth or routing.
    "foxrunner.rate_limit.RateLimitMiddleware",
    "foxrunner.payload_limit.PayloadLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "foxrunner.urls"
WSGI_APPLICATION = "foxrunner.wsgi.application"
ASGI_APPLICATION = "foxrunner.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# --- Database -----------------------------------------------------------


def _database_config() -> dict:
    url = os.getenv("DATABASE_URL") or _translate_async_url(os.getenv("AUTH_DATABASE_URL", ""))
    if not url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(REPO_ROOT / ".runtime" / "users.db"),
        }
    if url.startswith("sqlite"):
        # sqlite:///relative/path.db or sqlite:////absolute/path
        path = url.split("sqlite:///", 1)[1]
        if not path.startswith("/") and len(path) > 1 and path[1] != ":":
            path = str(REPO_ROOT / path)
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": path}
    if url.startswith("postgres"):
        return _parse_postgres(url)
    raise RuntimeError(f"Unsupported DATABASE_URL scheme: {url}")


def _translate_async_url(url: str) -> str:
    # sqlite+aiosqlite:///foo.db -> sqlite:///foo.db
    # postgresql+asyncpg://u:p@h/d -> postgresql://u:p@h/d
    if not url:
        return ""
    return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql").replace("postgres+asyncpg", "postgres")


def _parse_postgres(url: str) -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port) if parsed.port else "",
    }


DATABASES = {"default": _database_config()}


# --- Auth ---------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]


# --- REST / djoser / JWT ------------------------------------------------


def _parse_token_lifetime():
    from datetime import timedelta

    return timedelta(seconds=int(os.getenv("AUTH_JWT_LIFETIME_SECONDS", "3600")))


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
}

SIMPLE_JWT = {
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ACCESS_TOKEN_LIFETIME": _parse_token_lifetime(),
}

DJOSER = {
    "PASSWORD_RESET_CONFIRM_URL": os.getenv("APP_PASSWORD_RESET_URL", "http://localhost:4200/reset-password") + "?token={token}&uid={uid}",
    "SEND_ACTIVATION_EMAIL": False,
    "SERIALIZERS": {},
}


# --- CORS / security ----------------------------------------------------

CORS_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", os.getenv("API_CORS_ORIGINS", "http://localhost:4200")).split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"

# Payload size cap — matches the FastAPI API_MAX_BODY_BYTES semantics.
# Django enforces this only when Content-Length is provided (or for
# materialised multipart uploads); RequestDataTooBig bubbles up to
# ``foxrunner.payload_limit.PayloadLimitMiddleware`` which renders the
# JSON 413 envelope.
# TODO(asgi-deploy): the FastAPI implementation also covered chunked
# request bodies (``Transfer-Encoding: chunked`` without Content-Length)
# via an ASGI ``receive`` wrapper. Revisit ``payload_limit.py`` when
# Daphne/Uvicorn becomes the production target.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("API_MAX_BODY_BYTES", "1048576"))
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200


# --- Cache / throttling -------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("API_RATE_LIMIT_REDIS_URL") or os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "TIMEOUT": None,
    },
}

RATELIMIT_USE_CACHE = "default"


# --- Celery -------------------------------------------------------------

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Brussels")
CELERY_TASK_TRACK_STARTED = True

CELERY_BEAT_SCHEDULE = {
    "renew-graph-subscriptions": {
        "task": "ops.tasks.renew_graph_subscriptions_task",
        "schedule": int(os.getenv("GRAPH_SUBSCRIPTION_RENEW_INTERVAL_SECONDS", "3600")),
    },
    "prune-retention": {
        "task": "ops.tasks.prune_retention_task",
        "schedule": int(os.getenv("RETENTION_PRUNE_INTERVAL_SECONDS", "86400")),
    },
}


# --- Internationalization / time ----------------------------------------

LANGUAGE_CODE = "fr-be"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static -------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# --- Default primary key ------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Logging ------------------------------------------------------------

LOG_JSON = os.getenv("API_LOG_JSON", os.getenv("APP_LOG_JSON", "false")).lower() == "true"
HTTP_LOG_ENABLED = os.getenv("API_LOG_HTTP_ENABLED", "true").lower() == "true"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
        "json": {"()": "app.logging_config.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_JSON else "plain",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "smiley.api": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

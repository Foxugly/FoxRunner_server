# Environment Variables

Use `.env.example` as the canonical local template. Use `.env.test` for local test-oriented defaults.

## Runtime

- `APP_ENV`: environment name. `production` / `prod` turns on stricter runtime checks (`AUTH_SECRET` length, `GRAPH_WEBHOOK_CLIENT_STATE` required).
- `APP_TIMEZONE`: fallback business timezone.
- `APP_STATE_DIR`: runtime data directory.
- `APP_LOG_JSON`: JSON runtime logs.
- `APP_LOG_CONSOLE_ENABLED`: disables console logs during tests when false.
- `APP_LOCK_STALE_SECONDS`: scheduler lock recovery window. Default `3600` (1 h). Stale PID detection is the primary recovery path; the timeout is only the backstop when a PID cannot be verified.

## API

- `AUTH_DATABASE_URL`: SQLAlchemy async database URL. Required; `alembic.ini` no longer ships a usable default.
- `AUTH_SECRET`: JWT/password-reset secret. Must be different from the placeholder and at least 32 characters when `APP_ENV=production`.
- `API_CORS_ORIGINS`: trusted frontend origins.
- `API_ENABLE_LEGACY_ROUTES`: hidden unprefixed compatibility routes.
- `API_MAX_BODY_BYTES`: request body limit, enforced even for `Transfer-Encoding: chunked` uploads.
- `API_RATE_LIMIT_ENABLED`: API rate limiting toggle.
- `API_RATE_LIMIT_REDIS_URL`: optional Redis URL for the rate limiter's sliding window. Falls back to `CELERY_BROKER_URL` when unset, and to an in-process dict (single-worker only) when Redis is unreachable.

## Celery

- `CELERY_BROKER_URL`: Redis broker URL (DB 0 by convention).
- `CELERY_RESULT_BACKEND`: Redis result backend URL (DB 1 by convention, matching `docker-compose.yml`).

## Microsoft Graph

- `GRAPH_MAIL_ENABLED`: use Graph mail instead of SMTP fallback.
- `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`: Graph app credentials.
- `GRAPH_MAIL_SENDER`: sender mailbox.
- `GRAPH_WEBHOOK_CLIENT_STATE`: global webhook validation secret. Required in production. Webhooks accept deliveries whose `clientState` matches either the value stored on the target subscription at creation time or this global value, which supports rotation windows.
- `GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION`: require known subscription ids.
- `GRAPH_SUBSCRIPTION_RENEW_*`: renewal task controls.

## Retention

- `RETENTION_PRUNE_ENABLED`: enables periodic pruning.
- `RETENTION_*_DAYS`: retention windows.

## Smoke Tests

- `SMOKE_BASE_URL`: target API base URL.
- `SMOKE_EMAIL`, `SMOKE_PASSWORD`: optional auth smoke credentials.
- `SMOKE_TIMEOUT_SECONDS`: HTTP timeout.

## Scripts

- `BOOTSTRAP_PASSWORD`: read by `scripts/bootstrap_admin.py`. The script falls back to `getpass` when unset. The legacy `--password` argument has been removed to prevent leakage via shell history and `ps`.

## Docker Compose

`docker-compose.yml` sources credentials from the local `.env` (never committed):

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: database credentials.
- `AUTH_SECRET`: required so the stack starts in a production-like mode without baking secrets into the compose file.
- `API_CORS_ORIGINS`, `APP_ENV`: optional overrides for the API service.

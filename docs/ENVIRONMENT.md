# Environment Variables

Use `.env.example` as the canonical local template. Use `.env.test` for local test-oriented defaults.

## Runtime

- `APP_ENV`: environment name.
- `APP_TIMEZONE`: fallback business timezone.
- `APP_STATE_DIR`: runtime data directory.
- `APP_LOG_JSON`: JSON runtime logs.
- `APP_LOG_CONSOLE_ENABLED`: disables console logs during tests when false.

## API

- `AUTH_DATABASE_URL`: SQLAlchemy async database URL.
- `AUTH_SECRET`: JWT/password-reset secret.
- `API_CORS_ORIGINS`: trusted frontend origins.
- `API_ENABLE_LEGACY_ROUTES`: hidden unprefixed compatibility routes.
- `API_MAX_BODY_BYTES`: request body limit.
- `API_RATE_LIMIT_ENABLED`: in-process API rate limiting.

## Celery

- `CELERY_BROKER_URL`: Redis broker URL.
- `CELERY_RESULT_BACKEND`: Redis result backend URL.

## Microsoft Graph

- `GRAPH_MAIL_ENABLED`: use Graph mail instead of SMTP fallback.
- `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`: Graph app credentials.
- `GRAPH_MAIL_SENDER`: sender mailbox.
- `GRAPH_WEBHOOK_CLIENT_STATE`: webhook validation secret.
- `GRAPH_WEBHOOK_REQUIRE_SUBSCRIPTION`: require known subscription ids.
- `GRAPH_SUBSCRIPTION_RENEW_*`: renewal task controls.

## Retention

- `RETENTION_PRUNE_ENABLED`: enables periodic pruning.
- `RETENTION_*_DAYS`: retention windows.

## Smoke Tests

- `SMOKE_BASE_URL`: target API base URL.
- `SMOKE_EMAIL`, `SMOKE_PASSWORD`: optional auth smoke credentials.
- `SMOKE_TIMEOUT_SECONDS`: HTTP timeout.

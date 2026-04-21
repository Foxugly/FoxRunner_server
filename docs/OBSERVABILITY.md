# Observability

## Request IDs

Every API response includes `X-Request-ID`. Incoming values are preserved; otherwise the API generates one.

## Logs

API request logs use the `smiley.api` logger. Set `API_LOG_JSON=true` or `APP_LOG_JSON=true` for JSON logs.

Runtime logs use `app.logger.Logger`. Set `APP_LOG_CONSOLE_ENABLED=false` in tests to suppress console output.

## Metrics

`GET /api/v1/metrics` exposes Prometheus text metrics:

- `smiley_jobs_total`
- `smiley_jobs_failed`
- `smiley_jobs_stuck`
- `smiley_jobs_by_status{status="..."}`
- `smiley_graph_subscriptions_expiring`

## Readiness

`GET /api/v1/ready` checks DB, Redis broker, Celery workers, and Graph configuration.

`GET /api/v1/status` is a frontend-friendly summary containing readiness state, API version, environment, and checks.

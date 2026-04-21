# Architecture

```text
Angular / PrimeNG
      |
      | HTTPS + JWT
      v
Reverse proxy / API gateway
      |
      v
FastAPI /api/v1
      |
      +--> Database: users, catalog, jobs, audit, settings, Graph, history
      |
      +--> Redis: Celery broker/result backend
      |
      +--> Celery worker: scenario execution, Graph subscription renewal
      |
      +--> Celery beat: periodic maintenance tasks
      |
      +--> Microsoft Graph: mail, subscriptions, webhooks
      |
      +--> Runtime artifacts: screenshots and captured pages
```

## API

`api.main:create_app()` builds the FastAPI app. Public routes are under `/api/v1`; legacy unprefixed routes can be disabled with `API_ENABLE_LEGACY_ROUTES=false`.

Startup uses FastAPI lifespan to:

- validate production auth secret length;
- optionally create tables for local/dev;
- seed the DB catalog from JSON compatibility files.

## Services

Routers should stay thin:

- request validation and dependency injection;
- permission checks;
- service call;
- response model.

Business logic lives in `api/services/`. Lower-level persistence helpers remain in modules such as `api/catalog.py`, `api/jobs.py`, `api/history.py`, and `api/settings.py`.

## Timezones

Database timestamps are treated as UTC. API serializers emit UTC ISO 8601 values with `Z`, independent from the host timezone.

`APP_TIMEZONE` is the application fallback for local business calculations. Each user also has `timezone_name`; planning endpoints use the target user's timezone for slot windows when a profile exists. Frontends convert UTC timestamps to the user's timezone for display.

## Observability

Every HTTP response includes `X-Request-ID`. Incoming `X-Request-ID` is preserved; otherwise the API generates one.

Structured request context is logged through the `smiley.api` logger:

- request id;
- method;
- path;
- status code;
- duration in ms;
- client host.

## Readiness

`GET /api/v1/ready` checks:

- database connectivity;
- Redis broker connectivity;
- Celery worker ping;
- Graph configuration presence.

Database and Redis failures mark readiness as degraded. Celery without workers is reported as `no_workers` so deployment automation can decide whether that is blocking.

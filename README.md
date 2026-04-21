# FoxRunner

FoxRunner is a scheduled automation engine driven by scenarios, exposed through a FastAPI backend for a future Angular/PrimeNG UI.

## What It Contains

- CLI scheduler and Selenium automation engine
- FastAPI API
- FastAPI Users authentication
- SQLAlchemy async models
- Alembic migrations
- Celery + Redis jobs
- Persistent job events
- Admin operations, audit log, import/export, and artifact management
- Microsoft Graph mail and webhooks
- JSON compatibility for existing CLI workflows

## Quick Start

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run migrations:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Start the API:

```powershell
.\.venv\Scripts\uvicorn.exe api.main:app --reload
```

Start a Celery worker:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

Start Celery Beat for periodic maintenance:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app beat --loglevel=INFO
```

Docker Compose option:

```powershell
docker compose up --build
```

Flower is included in Docker Compose at `http://127.0.0.1:5555`.

Open:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

The public API is exposed under `/api/v1`. Unprefixed routes are still available for compatibility with existing local tooling and tests.

## CLI

Common commands:

- `python main.py --validate-config`
- `python main.py --validate-examples`
- `python main.py --plan`
- `python main.py --dump-runtime`
- `python main.py --list-slots`
- `python main.py --list-scenarios`
- `python main.py --history --history-limit 10`
- `python main.py --run-next --dry-run`
- `python main.py --run-slot slot_example --dry-run`
- `python main.py --run-scenario browser_scenario --dry-run`
- `python main.py --check --dry-run`
- `python main.py`

Module entrypoints:

- `python -m app`
- `python -m cli --limit 10`

## Project Structure

- `api/` : FastAPI, auth, DB catalog, jobs, Graph integration
- `app/` : CLI entrypoint, config, logger, notifier
- `scheduler/` : slot model and scheduling service
- `scenarios/` : DSL loader, validation, execution engine
- `operations/` : executable step operations
- `network/` : VPN/enterprise network detection
- `state/` : runtime JSON stores used by the CLI engine
- `migrations/` : Alembic migrations
- `tests/` : unit and API tests
- `.github/workflows/` : CI checks
- `scripts/` : local operational scripts
- `docs/` : detailed API, operations, and Graph documentation

## Configuration

Use `.env` for local configuration. Start from [.env.example](.env.example).

Important variables:

- `APP_ENV`
- `APP_TIMEZONE`
- `AUTH_DATABASE_URL`
- `AUTH_SECRET`
- `API_CORS_ORIGINS`
- `API_RATE_LIMIT_ENABLED`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `APP_LOG_JSON`
- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET`
- `GRAPH_MAIL_SENDER`
- `GRAPH_WEBHOOK_CLIENT_STATE`
- `GRAPH_SUBSCRIPTION_RENEW_ENABLED`

OpenAPI export:

```powershell
make openapi
```

## Timezones

FoxRunner stores technical timestamps in UTC and exposes API timestamps as ISO 8601 UTC values. `APP_TIMEZONE` is only the fallback for local business calculations. Each user profile has `timezone_name`; planning uses it for slot windows, and the frontend converts UTC dates to that timezone for display.

## Documentation

- [API](docs/API.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Architecture Decisions](docs/ADR.md)
- [Timezone Decision](docs/ADR_TIMEZONES.md)
- [Database Operations](docs/DB.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Environment](docs/ENVIRONMENT.md)
- [Angular Client Guide](docs/ANGULAR_CLIENT.md)
- [First Deployment](docs/FIRST_DEPLOYMENT.md)
- [Frontend Integration](docs/FRONTEND.md)
- [Operations](docs/OPERATIONS.md)
- [Production Checklist](docs/PRODUCTION.md)
- [Testing](docs/TESTING.md)
- [Release Checklist](docs/RELEASE.md)
- [Runbooks](docs/RUNBOOKS.md)
- [Security](docs/SECURITY.md)
- [Security Checklist](docs/SECURITY_CHECKLIST.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Observability](docs/OBSERVABILITY.md)
- [Roadmap](docs/ROADMAP.md)
- [Compatibility Policy](docs/COMPATIBILITY.md)
- [Microsoft Graph](docs/GRAPH.md)
- [Scenario DSL](SCHEMA.md)

## Runtime Files

By default runtime files are in `.runtime/`:

- `users.db`
- `next.json`
- `last_run.json`
- `executions.json`
- `history.jsonl`
- `scheduler.lock`
- `artifacts/screenshots/`
- `artifacts/pages/`

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest
```

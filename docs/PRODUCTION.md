# Production Checklist

## Runtime

- Set `APP_ENV=production`.
- Set a strong `AUTH_SECRET` with at least 32 characters.
- Disable automatic table creation and use migrations:

```env
API_CREATE_TABLES_ON_STARTUP=false
API_ENABLE_LEGACY_ROUTES=false
```

- Run migrations before starting the API:

```powershell
alembic upgrade head
```

## Services

Run these processes separately:

- API: `uvicorn api.main:app`
- Celery worker: `celery -A api.celery_app.celery_app worker`
- Celery beat: `celery -A api.celery_app.celery_app beat`
- Redis
- Database
- Reverse proxy or API gateway

## Security

- Terminate HTTPS at the reverse proxy.
- Restrict CORS with `API_CORS_ORIGINS`.
- Configure reverse-proxy rate limiting for `/api/v1/auth/*`, `/api/v1/graph/webhook`, and `/api/v1/graph/lifecycle`.
- Store Graph and auth secrets outside source control.
- Rotate `GRAPH_CLIENT_SECRET` and `AUTH_SECRET` through your deployment secret manager.

## Data

- Database is the API source of truth.
- Scenario and slot JSON files remain compatibility files for CLI workflows.
- API history is stored in DB and synchronized from the legacy JSONL history file when read.

## Backups

Back up:

- database;
- `.runtime/artifacts`;
- configuration files needed for CLI compatibility;
- deployment `.env` or secret definitions through the secret manager.

Validate restore regularly:

```powershell
alembic upgrade head
python scripts/export_openapi.py
```

## Monitoring

Use:

- `GET /api/v1/ready`
- `GET /api/v1/admin/config-checks`
- `GET /api/v1/monitoring/summary`
- `GET /api/v1/metrics`

Every response includes `X-Request-ID`. Preserve this value in frontend error reports and operational logs.

Alert on:

- failed jobs;
- stuck queued/running jobs;
- Graph subscriptions close to expiration;
- database readiness failures;
- worker/beat process downtime.

## Deployment Gate

Before promoting a release:

- `ruff check .`
- `python -m unittest`
- `alembic upgrade head` on a disposable database
- `python scripts/export_openapi.py`
- `docker compose config`
- Docker image build

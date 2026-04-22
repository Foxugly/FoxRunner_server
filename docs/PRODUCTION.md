# Production Checklist

## Runtime

- Set `APP_ENV=production`.
- Set a strong `DJANGO_SECRET_KEY` with at least 32 characters — enforced at Django startup. The legacy `AUTH_SECRET` name is still accepted during the dual-stack window.
- Run migrations before starting the API:

```powershell
# Django (post-swap)
python manage.py migrate

# FastAPI (legacy, removed in Phase 13)
alembic upgrade head
```

## Services

Run these processes separately:

- API (Django): `gunicorn foxrunner.wsgi:application --workers 2`
- API (legacy FastAPI, until Phase 13): `uvicorn api.main:app`
- Celery worker: `celery -A foxrunner.celery_app worker` (Django) / `celery -A api.celery_app.celery_app worker` (legacy)
- Celery beat: `celery -A foxrunner.celery_app beat` (Django) / `celery -A api.celery_app.celery_app beat` (legacy)
- Redis
- Database
- Reverse proxy or API gateway

## Security

- Terminate HTTPS at the reverse proxy.
- Restrict CORS with `CORS_ALLOWED_ORIGINS` (legacy `API_CORS_ORIGINS` still accepted).
- Configure reverse-proxy rate limiting for `/api/v1/auth/*`, `/api/v1/graph/webhook`, and `/api/v1/graph/lifecycle`. The built-in limiter is a safety net, not the primary control.
- Set `API_RATE_LIMIT_REDIS_URL` (or rely on the Celery broker URL) so the in-app limiter uses a shared Redis sliding window across workers.
- Set `GRAPH_WEBHOOK_CLIENT_STATE` — production refuses webhook deliveries when it is empty.
- Store Graph and auth secrets outside source control.
- Rotate `GRAPH_CLIENT_SECRET` and `DJANGO_SECRET_KEY` (legacy `AUTH_SECRET`) through your deployment secret manager.
- Do not deploy `docker-compose.yml` as-is in production; it is a local stack. It reads `POSTGRES_*` and `DJANGO_SECRET_KEY` (legacy `AUTH_SECRET`) from a local `.env` that must never be committed.

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
# Django
python manage.py migrate
python scripts/export_openapi.py

# Legacy FastAPI
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
- `python manage.py test` (Django) and/or `python -m unittest` (FastAPI, while dual-stack)
- `python manage.py migrate` (Django) / `alembic upgrade head` (FastAPI) on a disposable database
- `python scripts/export_openapi.py`
- `docker compose config` (requires `POSTGRES_*` and `DJANGO_SECRET_KEY` set in `.env`)
- Docker image build (multi-stage, runs as non-root `app`, ships `HEALTHCHECK` on `/api/v1/health`)

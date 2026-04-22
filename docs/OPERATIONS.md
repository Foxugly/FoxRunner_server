# Operations

> Dual-stack note (Phases 9–12): the Django backend runs on port **8001** while the FastAPI backend keeps port **8000**. Phase 13 deletes `api/` and the Django app moves to port 8000.

## Local API

Django (new, default starting in Phase 13):

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

FastAPI (legacy, removed in Phase 13):

```powershell
.\.venv\Scripts\uvicorn.exe api.main:app --reload
```

## Database

SQLite is the default local database:

```env
AUTH_DATABASE_URL=sqlite+aiosqlite:///.runtime/users.db
```

PostgreSQL example:

```env
AUTH_DATABASE_URL=postgresql+asyncpg://user:password@host:5432/smiley
```

Run migrations:

```powershell
# Django (new)
.\.venv\Scripts\python.exe manage.py migrate

# FastAPI (legacy, removed in Phase 13)
.\.venv\Scripts\alembic.exe upgrade head
```

Check migration state:

```powershell
# Django
.\.venv\Scripts\python.exe manage.py showmigrations

# FastAPI
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe history
```

Create a new migration:

```powershell
# Django
.\.venv\Scripts\python.exe manage.py makemigrations

# FastAPI
.\.venv\Scripts\alembic.exe revision --autogenerate -m "message"
```

## Local Reset

Full local reset with SQLite:

```powershell
Stop-Process -Name uvicorn,python,celery -ErrorAction SilentlyContinue
Remove-Item .runtime\users.db -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe manage.py migrate    # Django
# or, for the legacy FastAPI stack:
.\.venv\Scripts\alembic.exe upgrade head
```

On API startup, if the catalog tables are empty, scenarios and slots are seeded from:

```text
config/scenarios.json
config/slots.json
```

The API startup is:

```powershell
# Django (port 8001 during dual-stack)
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001

# FastAPI (port 8000, legacy)
.\.venv\Scripts\uvicorn.exe api.main:app --reload
```

## Catalog Import/Export

Export the runtime catalog from DB:

```http
GET /admin/export
```

Validate an import without replacing data:

```http
POST /admin/import?dry_run=true
```

Replace scenarios and slots:

```http
POST /admin/import?dry_run=false
```

Payload shape:

```json
{
  "scenarios": {
    "schema_version": 1,
    "data": {},
    "scenarios": {}
  },
  "slots": {
    "slots": []
  }
}
```

This import replaces scenarios, scenario shares, and slots. It does not delete users, jobs, job events, Graph records, or audit records.

## Celery and Redis

Redis must be reachable through `CELERY_BROKER_URL`.

Windows development:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

Production Linux:

```bash
celery -A api.celery_app.celery_app worker --loglevel=INFO
```

Celery Beat runs periodic tasks such as Microsoft Graph subscription renewal:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app beat --loglevel=INFO
```

Graph renewal is controlled by:

```env
GRAPH_SUBSCRIPTION_RENEW_ENABLED=true
GRAPH_SUBSCRIPTION_RENEW_INTERVAL_SECONDS=3600
GRAPH_SUBSCRIPTION_RENEW_BEFORE_HOURS=24
GRAPH_SUBSCRIPTION_RENEW_EXTENSION_HOURS=48
```

## Docker Compose

Start PostgreSQL, Redis, API, worker, beat, and Flower:

```powershell
docker compose up --build
```

Stop services:

```powershell
docker compose down
```

Flower is exposed on:

```text
http://127.0.0.1:5555
```

Validate the compose file when Docker is available:

```powershell
docker compose config
```

## Makefile

Common targets:

```powershell
make install   # installs requirements-dev.txt (ruff, coverage, pre-commit + runtime deps)
make test
make migrate
make run-api
make run-worker
make run-beat
make reset-local
make docker-up
make docker-down
```

Production deployments install only the runtime subset with `python -m pip install -r requirements.txt`.

On Windows, use a shell with `make` installed, or run the commands from the target directly.

## Logs

Set `APP_LOG_JSON=true` to emit JSON lines to stdout and the configured log file.

```env
APP_LOG_FILE=default
APP_LOG_JSON=true
APP_LOG_MAX_BYTES=1048576
APP_LOG_BACKUP_COUNT=3
```

## Rate Limiting

API rate limiting protects auth and Graph webhook routes. The limiter uses a Redis sliding window shared across workers when Redis is reachable, and falls back to an in-process counter otherwise (single-worker dev only):

```env
API_RATE_LIMIT_ENABLED=true
API_RATE_LIMIT_WINDOW_SECONDS=60
API_RATE_LIMIT_MAX_REQUESTS=60
API_RATE_LIMIT_REDIS_URL=redis://redis:6379/2   # optional; falls back to CELERY_BROKER_URL
```

For production, keep reverse-proxy or gateway rate limiting as the primary protection — the in-app limiter is a safety net. Example Nginx baseline:

```nginx
limit_req_zone $binary_remote_addr zone=smiley_auth:10m rate=5r/s;
limit_req_zone $binary_remote_addr zone=smiley_webhooks:10m rate=20r/s;

location /auth/ {
    limit_req zone=smiley_auth burst=20 nodelay;
    proxy_pass http://smiley_api;
}

location /graph/webhook {
    limit_req zone=smiley_webhooks burst=60 nodelay;
    proxy_pass http://smiley_api;
}
```

## Backup and Restore

SQLite local backup:

```powershell
New-Item -ItemType Directory -Force .runtime\backups
Copy-Item .runtime\users.db ".runtime\backups\users-$(Get-Date -Format yyyyMMdd-HHmmss).db"
```

SQLite restore:

```powershell
Stop-Process -Name uvicorn,python,celery -ErrorAction SilentlyContinue
Copy-Item .runtime\backups\users-YYYYMMDD-HHMMSS.db .runtime\users.db -Force
.\.venv\Scripts\python.exe manage.py migrate    # Django
# or, legacy FastAPI:
.\.venv\Scripts\alembic.exe upgrade head
```

PostgreSQL backup:

```bash
pg_dump "$DATABASE_URL" > backup.sql
```

PostgreSQL restore:

```bash
psql "$DATABASE_URL" < backup.sql
python manage.py migrate    # Django
# or, legacy:
alembic upgrade head
```

Helper scripts are available for local SQLite:

```powershell
.\scripts\backup_sqlite.ps1
.\scripts\restore_sqlite.ps1 -Backup .runtime\backups\users-YYYYMMDD-HHMMSS.db
```

## Migrations Policy

Migrations are forward-only for now. Downgrades are not part of the supported operational path; restore from backup if a migration must be rolled back.

## OpenAPI and TypeScript Client

Export the OpenAPI document:

```powershell
make openapi
```

Generate a TypeScript client from `openapi.json` with your preferred generator, for example `openapi-typescript` or `openapi-generator-cli`.

## Data Flow

- API startup creates tables if needed and imports JSON scenarios/slots only when the catalog DB is empty.
- API and Celery build the scheduler from DB records.
- Scenario, step, and slot mutations write to DB and sync back to `config/scenarios.json` / `config/slots.json` for CLI compatibility.
- Jobs and job events are persisted in DB.
- Audit entries are written for admin and catalog operations.

## Operational Endpoints

- `GET /health` returns a lightweight liveness response.
- `GET /ready` checks database readiness and integration configuration.
- `GET /admin/config-checks` exposes DB, auth, Celery, Graph, and catalog file checks.
- `GET /admin/export` exports scenarios and slots from DB.
- `POST /admin/import?dry_run=true` validates an import without replacing DB catalog data.
- `GET /jobs`, `POST /jobs/{job_id}/cancel`, and `POST /jobs/{job_id}/retry` operate on persisted Celery jobs.
- `GET /artifacts` lists screenshots and captured pages; `DELETE /artifacts` prunes old files.
- `GET /monitoring/summary` reports jobs failed/stuck, average job duration, and Graph subscriptions close to expiration.
- `GET /metrics` exposes the same core counters in Prometheus text format.
- `DELETE /admin/retention` prunes old jobs, audit rows, and Graph notifications according to query parameters.

## Catalog Recovery

The database is now the source of truth at runtime. JSON files remain compatibility outputs.

1. Restore the database backup first.
2. Run `GET /admin/export` to verify catalog content.
3. If JSON files are missing, perform a no-op catalog mutation or use export content to regenerate them.
4. Use `POST /admin/import?dry_run=true` before replacing catalog data from an external document.

## Recommended Local Flow

Django (default starting in Phase 13):

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

FastAPI (legacy, still primary during dual-stack):

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m unittest
.\.venv\Scripts\uvicorn.exe api.main:app --reload
```

In a second terminal:

```powershell
.\.venv\Scripts\celery.exe -A api.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

## Production Checklist

- Use PostgreSQL instead of SQLite.
- Run Redis and Celery as supervised services.
- Monitor queued/running jobs and retry/cancel stuck jobs from the API.
- Set `APP_ENV=production`.
- Set a strong `AUTH_SECRET`.
- Configure HTTPS and reverse proxy.
- Configure backup and retention for the database.
- Configure DB retention with `DELETE /admin/retention?jobs_days=...&audit_days=...&graph_notifications_days=...`.
- Configure artifact retention with `DELETE /artifacts?older_than_days=...`.
- Configure monitoring for API, worker, Redis, and Graph subscriptions.

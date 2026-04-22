# First Deployment

## 1. Prepare Environment

Create production secrets and environment variables:

```env
APP_ENV=production
DJANGO_SECRET_KEY=<strong-secret>           # legacy AUTH_SECRET still accepted
CORS_ALLOWED_ORIGINS=https://app.example.com  # legacy API_CORS_ORIGINS still accepted
APP_LOG_JSON=true
DATABASE_URL=<database-url>                 # legacy AUTH_DATABASE_URL still accepted
CELERY_BROKER_URL=<redis-url>
CELERY_RESULT_BACKEND=<redis-url>
```

Configure Graph only if mail/webhooks are enabled.

## 2. Install And Migrate

Production installs runtime dependencies only:

```powershell
python -m pip install -r requirements.txt
python manage.py migrate            # Django
# (or, legacy FastAPI: alembic upgrade head)
```

Development environments add lint, coverage, and pre-commit tooling:

```powershell
python -m pip install -r requirements-dev.txt
```

## 3. Create First Admin

```powershell
$env:BOOTSTRAP_PASSWORD = "<strong-password>"
python manage.py bootstrap_admin --email admin@localhost
```

`--password` is not accepted on the command line — set `BOOTSTRAP_PASSWORD` or let the command prompt via `getpass`. It creates a verified superuser or promotes an existing user. The legacy `python scripts/bootstrap_admin.py` is removed in Phase 13.

## 4. Start Services

Start separately:

- API (Django): `gunicorn foxrunner.wsgi:application --workers 2`
- API (legacy FastAPI, until Phase 13): `uvicorn api.main:app`
- worker: `celery -A foxrunner.celery_app worker` (Django) / `celery -A api.celery_app.celery_app worker` (legacy)
- beat: `celery -A foxrunner.celery_app beat` (Django) / `celery -A api.celery_app.celery_app beat` (legacy)
- Redis
- database
- reverse proxy

## 5. Verify

```text
GET /api/v1/health
GET /api/v1/ready
GET /api/v1/version
```

Check:

- `X-Request-ID` appears in responses;
- `/health` is disabled when `API_ENABLE_LEGACY_ROUTES=false`;
- `/api/v1/admin/config-checks` as admin;
- `/api/v1/monitoring/summary` as admin.

## 6. Backups

Before enabling users, verify backup and restore for:

- database;
- runtime artifacts;
- deployment secrets.

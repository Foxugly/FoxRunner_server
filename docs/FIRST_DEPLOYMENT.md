# First Deployment

## 1. Prepare Environment

Create production secrets and environment variables:

```env
APP_ENV=production
AUTH_SECRET=<strong-secret>
API_CREATE_TABLES_ON_STARTUP=false
API_ENABLE_LEGACY_ROUTES=false
API_CORS_ORIGINS=https://app.example.com
API_LOG_JSON=true
AUTH_DATABASE_URL=<database-url>
CELERY_BROKER_URL=<redis-url>
CELERY_RESULT_BACKEND=<redis-url>
```

Configure Graph only if mail/webhooks are enabled.

## 2. Install And Migrate

```powershell
python -m pip install -r requirements.txt
alembic upgrade head
```

## 3. Create First Admin

```powershell
python scripts/bootstrap_admin.py --email admin@example.com
```

The script creates a verified superuser or promotes an existing user.

## 4. Start Services

Start separately:

- API: `uvicorn api.main:app`
- worker: `celery -A api.celery_app.celery_app worker`
- beat: `celery -A api.celery_app.celery_app beat`
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

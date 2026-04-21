# Troubleshooting

## Alembic

- Check `AUTH_DATABASE_URL` points to the expected DB.
- Run `alembic current` and `alembic history` when revision state is unclear.
- For local SQLite reset, use `make reset-local` or remove `.runtime/users.db` and run `alembic upgrade head`.

## Redis And Celery

- `/api/v1/ready` reports Redis and worker state.
- Start Redis before workers.
- On Windows local development, use Celery `--pool=solo`.

## Microsoft Graph

- Verify tenant, client id, client secret, sender, notification URL, and webhook `clientState`.
- Subscriptions expire quickly; check `/api/v1/monitoring/summary` and Celery beat renewal logs.

## Auth

- In production, `AUTH_SECRET` must be set and long enough.
- JWT login expects form data: `username` and `password`.

## OpenAPI

- Regenerate with `python scripts/export_openapi.py`.
- Validate with `python scripts/check_openapi.py`.

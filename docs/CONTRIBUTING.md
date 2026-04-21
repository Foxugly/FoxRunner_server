# Contributing

## Workflow

1. Install dependencies from `requirements-dev.txt` (runtime deps + ruff, coverage, pre-commit).
2. Add or update tests with behavioral changes.
3. Run `.\scripts\ci.ps1` on Windows.
4. Regenerate `openapi.json` when API contracts change.
5. Add an Alembic migration for DB schema changes.
6. Update `CHANGELOG.md` and the relevant `docs/*.md` when behavior, operations, or environment variables change.

## Tests

Use helpers from `tests/helpers.py` for service/API fixtures. Keep service tests focused and avoid real Celery workers, browsers, Redis, or Microsoft Graph calls.

## Migrations

Migration filenames use `YYYYMMDD_NNNN_short_description.py`. Each migration should support `upgrade` and `downgrade`.

## API Contract

Public routes live under `/api/v1`. Legacy unprefixed routes exist only for compatibility.

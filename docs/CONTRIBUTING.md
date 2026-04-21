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

## Dependencies

- `requirements.txt` / `requirements-dev.txt` are the *input* lists (unpinned).
- `requirements.lock` / `requirements-dev.lock` are the compiled, pinned lockfiles. CI and the production Docker image install from the lockfiles so builds are reproducible.
- Re-lock after changing an input list: `make relock` (requires `pip-tools`). Run this with a Python **3.12** interpreter ideally; the resolver uses the current interpreter version and a newer Python may pin wheels that are unavailable at the CI/Docker target. Commit both `.lock` files together with their `.txt` input.

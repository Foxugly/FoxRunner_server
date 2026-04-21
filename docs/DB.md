# Database Operations

## Source Of Truth

The API database is the source of truth for users, scenarios, slots, jobs, audit records, Graph subscriptions, settings, idempotency keys, and execution history.

Compatibility JSON files are still read or synchronized for CLI workflows, but new API features should persist to the database first.

## Timezones

Technical timestamps are stored and compared as UTC. API serializers return UTC ISO 8601 values. Local business time belongs to user profiles or future slot-level configuration.

## Migrations

Apply migrations:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Validate migration reversibility locally:

```powershell
make migrate-test
```

Create a migration:

```powershell
make migration m="describe change"
```

Without `make`, call Alembic directly:

```powershell
.\.venv\Scripts\alembic.exe revision --autogenerate -m "describe change"
```

Migration filenames should follow `YYYYMMDD_NNNN_short_description.py`. Keep migrations reversible unless a one-way data migration is explicitly documented.

## Local Reset

Reset local SQLite data and re-apply migrations:

```powershell
make reset-local
```

This removes `.runtime/users.db`. Runtime JSON files are not removed by this target.

## SQLite Backup And Restore

Create a timestamped backup:

```powershell
make backup-sqlite
```

Restore a backup:

```powershell
make restore-sqlite file=.runtime/backups/users-YYYYMMDD-HHMMSS.db
```

Stop API and workers before restoring.

## Future DB Engines

SQLAlchemy and Alembic keep the project portable to PostgreSQL or another SQL database. Before switching engines, validate:

- migration cycle on the target engine;
- JSON column behavior;
- timestamp UTC behavior;
- indexes and unique constraints;
- backup and restore procedure;
- connection pool settings for API and Celery workers.

## Compatibility Matrix

| Engine | Status | Notes |
| --- | --- | --- |
| SQLite + aiosqlite | Supported for local/dev | Current default. Good for local workflows, not ideal for multi-worker production. |
| PostgreSQL + asyncpg | Prepared | Dependency is installed. Validate migrations and JSON/date behavior before production use. |
| Oracle | Not validated | SQLAlchemy can support Oracle, but migrations, JSON columns, async driver choice, and CI coverage must be checked first. |

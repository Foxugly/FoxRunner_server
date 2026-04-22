# Architecture Decisions

## ADR 001: FastAPI Backend

Decision: use FastAPI for the API layer.

Rationale:

- strong OpenAPI generation for Angular;
- async SQLAlchemy support;
- easy integration with FastAPI Users;
- smaller migration from the existing Python CLI code than a full Django rewrite.

## ADR 002: Celery + Redis Jobs

Decision: use Celery with Redis for persistent background execution.

Rationale:

- mature worker model;
- retries, beat scheduling, and operational familiarity;
- clean separation between API request latency and scenario execution.

## ADR 003: Versioned API

Decision: expose public API under `/api/v1`.

Rationale:

- stable frontend contract;
- safe future breaking changes;
- unprefixed legacy routes can remain local compatibility routes.

## ADR 004: DB Source Of Truth With JSON Compatibility

Decision: API runtime uses DB as source of truth while preserving JSON scenario/slot compatibility files.

Rationale:

- avoids breaking existing CLI workflows;
- enables gradual migration;
- keeps import/export paths simple.

## ADR 005: Microsoft Graph Mail And Webhooks

Decision: use Microsoft Graph for mail and webhook integrations.

Rationale:

- aligns with enterprise identity and mail infrastructure;
- webhook subscriptions can be monitored and renewed by Celery beat;
- avoids maintaining separate SMTP-only operational assumptions.

## ADR 006: Timezones

Decision: store DB/API timestamps in UTC and use `User.timezone_name` for local business planning and frontend display.

Details: see [ADR_TIMEZONES.md](ADR_TIMEZONES.md).

## ADR 007: Switch to Django + Ninja

Decision: replace the FastAPI backend with Django 5 + Django Ninja, retaining the same `/api/v1` contract.

Rationale:

- richer ORM and admin UI for operators;
- mature migration system (Django migrations) replaces Alembic;
- single Python framework for the whole web stack reduces conceptual overhead;
- djoser + simple-jwt covers register/login/reset without a custom auth router;
- Ninja keeps OpenAPI generation lean and matches the Pydantic-style schemas the frontend already expects.

Trade-offs:

- adapters needed to keep the form-urlencoded login the Angular client uses (`POST /auth/jwt/login`);
- pagination envelope `{items, total, limit, offset}` enforced manually rather than relying on DRF defaults;
- one-shot data migration (`catalog/0002_normalize_owner_user_id`) consolidates email/UUID ownership before the FK promotion.

See `docs/superpowers/plans/2026-04-22-django-migration.md` for the full execution plan.

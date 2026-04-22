# Changelog

## 0.2.0 — Django backend

### Backend

- Replaced the FastAPI backend with Django 5 + Django Ninja under the same `/api/v1` contract (ADR 007).
- Auth via djoser + simple-jwt; `POST /api/v1/auth/jwt/login` continues to accept form-urlencoded credentials and returns `{access_token, token_type}`. Logout is a no-op matching FastAPI bearer-transport semantics.
- `owner_user_id`/`actor_user_id`/`user_id` columns normalized from email strings to UUID strings, then promoted to `ForeignKey(User)` via `catalog/0003_owner_fk_promotion` and `ops/0003_user_fk_promotion`. The FastAPI dual email/UUID ownership match is gone; API responses still surface `owner_user_id: str` to preserve the frontend contract.
- Celery tasks rewritten on top of the sync Django ORM (`run_scenario_job` full; Graph renewal and retention are stub handlers during the dual-stack window and promoted in Phase 12/13 before the swap).
- Microsoft Graph `clientState` validation ported byte-for-byte: per-subscription secret OR global `GRAPH_WEBHOOK_CLIENT_STATE` fallback; production rejects deliveries when both are empty.
- Rate limiter ported to a Redis sliding-window via `django_redis` with an in-process fallback.
- Payload size enforced via Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` plus a middleware rendering the existing `{code: payload_too_large, ...}` envelope.
- Security headers (`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`) + `X-Request-ID` propagation preserved.

### Infrastructure

- `manage.py` replaces `uvicorn`/`alembic` invocations across docs and CI.
- Tests run via `python manage.py test --parallel`; coverage floor stays at 84 % (currently at 96 %).
- New env var names: `DATABASE_URL`, `DJANGO_SECRET_KEY`, `CORS_ALLOWED_ORIGINS`. Removed: `API_CREATE_TABLES_ON_STARTUP`, `API_ENABLE_LEGACY_ROUTES`. Legacy names remain accepted during the dual-stack window.
- `scripts/bootstrap_admin.py` replaced by `python manage.py bootstrap_admin --email …`. Password still read from `BOOTSTRAP_PASSWORD` env or interactive prompt; never accepted as a CLI flag.

### Known gaps (closed in Phase 13 swap)

- `renew_graph_subscriptions_task` and `prune_retention_task` remain stubs in the Django tree; Celery beat still points at the FastAPI equivalents during the dual-stack window.
- `openapi.django.json` is the Ninja-side contract; `openapi.json` remains the FastAPI contract until the swap.
- Scenarios/slots JSON-file sync to `config/*.json` (CLI-compat, ADR 004) is handled by the FastAPI app during dual-stack; Django mutations currently write only to the DB. The file sync lands in Phase 13.

## Unreleased

### Security

- Graph webhook `clientState` is now validated against the per-subscription value stored at creation, with the global `GRAPH_WEBHOOK_CLIENT_STATE` as a fallback. Production rejects deliveries when both are empty.
- Scenario ownership checks accept both the user's UUID and email, so the 403 that affected owners created from the JSON seed path is gone.
- Rate limiter now uses a Redis sliding window (via `API_RATE_LIMIT_REDIS_URL` or the Celery broker URL) instead of an in-process dict that multiplied the effective limit by the worker count. Falls back to in-process when Redis is unreachable.
- Payload size limit no longer bypasses on `Transfer-Encoding: chunked` requests; the ASGI receive channel is wrapped to count bytes as they arrive.
- Idempotency-Key store now handles concurrent inserts cleanly (returns the stored response or 409 for fingerprint mismatch, never 500).
- `AUTH_SECRET` is checked in one place (`api.main.lifespan`) with both default-value and minimum-length rules.
- `scripts/bootstrap_admin.py` no longer accepts `--password`; the password is read from `BOOTSTRAP_PASSWORD` or `getpass`.
- Docker image is now multi-stage and runs as a non-root `app` user with `HEALTHCHECK` on `/api/v1/health`. `docker-compose.yml` reads credentials from `.env` (`POSTGRES_*`, `AUTH_SECRET`) and removes bind-mounts of the project source.

### Reliability

- `scenarios/engine.py` parallel block no longer marks `extract_text_to_context` / `extract_attribute_to_context` as parallel-safe: both call into Selenium, which is not thread-safe.
- `scheduler/model.py` builds slot datetimes via the `datetime` constructor instead of `datetime.replace(hour=...)` so DST transitions pick the correct UTC offset.
- `scheduler/service.py::run_check_mode` no longer has the tautological `dry_run=True if not dry_run else dry_run` expression.
- `scenarios/runner.py` hook steps no longer clobber the driver with `None` when the hook doesn't produce one, fixing a driver leak after `before_steps` / `on_success_steps`.
- `state/store.py::HistoryStore` serializes `append` and `prune` through a process-level lock.
- `state/store.py::ProcessLock` default stale timeout dropped from 12 h to 1 h (`APP_LOCK_STALE_SECONDS=3600`). Stale PID detection remains the primary recovery mechanism.
- `api/main.py::lifespan` uses `async with async_session_maker()` for the seed call instead of `async for session in get_async_session()`.
- `api/catalog.py::save_scenario_definition` serializes concurrent writes per file via an `asyncio.Lock` keyed on the resolved path.

### Observability

- `api/mail.py` logs an ERROR when both Graph mail and SMTP are unavailable for password reset.
- `api/tasks.py` logs an ERROR per subscription when Graph renewal fails (previously errors were only visible in the Celery task result).

### Tooling / CI

- GitHub Actions workflow now runs on `ubuntu-latest` and `windows-latest`, and adds `check_env_example.py` plus `pre-commit run --all-files` steps.
- Development tools (`ruff`, `coverage`, `pre-commit`) moved to `requirements-dev.txt`. The runtime `requirements.txt` is what the production image installs.
- `.dockerignore` excludes `.env*`, `tests/`, `docs/`, Markdown files, and `Makefile`.
- `alembic.ini` uses a placeholder URL; the effective URL comes from `AUTH_DATABASE_URL` via `migrations/env.py`.
- `.env.example` ships `CELERY_RESULT_BACKEND` on DB 1 (aligned with `docker-compose.yml`) and documents `POSTGRES_*` for the compose stack.

### Backwards-incompatible

- `api.catalog.get_scenario_for_user`, `list_scenarios_for_user`, `scenario_ids_for_user` accept a new keyword `email: str | None`. Callers passing only `user_id` still work; passing `email` as well is strongly recommended so the ownership match covers both identifiers.
- `api.catalog_queries.accessible_scenarios_query` / `list_accessible_scenarios` / `accessible_slots_query` / `list_accessible_slots` take the same new keyword.
- Existing ownership rows stored as email continue to match, but new code should normalize on UUID. A follow-up migration will rewrite email-stored owners to UUIDs (tracked separately).

## 0.1.0

- FastAPI backend with versioned `/api/v1` routes.
- FastAPI Users auth, password reset hooks, user timezone profiles.
- SQLAlchemy/Alembic persistence for catalog, jobs, audit, settings, Graph, and history.
- Celery + Redis job execution and periodic maintenance tasks.
- Microsoft Graph mail/webhook/subscription support.
- UTC timestamp policy and frontend timezone guidance.
- CI, coverage threshold, OpenAPI/docs checks, Windows CI script, smoke test script.
- Final hardening docs for contributing, environment, observability, troubleshooting, release, and security checklist.
- Service-level tests for tasks, admin, Graph, and scenarios.

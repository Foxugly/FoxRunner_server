# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Windows-first project. A local virtualenv at `.venv/` is assumed; the `Makefile` and docs invoke `.\.venv\Scripts\python.exe`, `alembic.exe`, `uvicorn.exe`, `celery.exe`, `ruff.exe` directly. Python 3.12 (matches `ruff target-version` and CI). Tests use stdlib `unittest`, **not** pytest.

Use `make <target>` when available (Linux/Git Bash) or run the equivalent `.\.venv\Scripts\...` binary on PowerShell. `scripts/ci.ps1` reproduces the full CI pipeline locally on Windows.

## Common Commands

```bash
# Run all tests
.\.venv\Scripts\python.exe -m unittest

# Run a single test module / class / method
.\.venv\Scripts\python.exe -m unittest tests.test_api
.\.venv\Scripts\python.exe -m unittest tests.test_api.SomeTestCase
.\.venv\Scripts\python.exe -m unittest tests.test_api.SomeTestCase.test_method

# Lint / format
./.venv/Scripts/ruff.exe check .
./.venv/Scripts/ruff.exe format .

# Coverage (fails under 84%)
make coverage

# Migrations
make migrate                          # alembic upgrade head
make migration m="short description"  # autogenerate new revision
make migrate-test                     # upgrade → downgrade → upgrade cycle

# Run API / workers
make run-api       # uvicorn api.main:app --reload
make run-worker    # celery worker --pool=solo  (solo pool required on Windows)
make run-beat      # celery beat

# Full local CI
.\scripts\ci.ps1   # compile + lint + coverage + migrate-cycle + openapi + docs + env check
make ci            # same on Linux/Git Bash (no env check)

# OpenAPI regeneration (required when API contracts change)
make openapi       # writes openapi.json
make openapi-check # export + verify against committed file
```

The CLI entrypoint is `python main.py` (see README for flags like `--validate-config`, `--plan`, `--run-next`, `--dry-run`). Module entrypoints `python -m app` and `python -m cli` also exist.

## Architecture

FoxRunner is **two overlapping products in one repo**:

1. **CLI scheduler / Selenium automation engine** (`app/`, `scheduler/`, `scenarios/`, `operations/`, `network/`, `state/`) — the original product. Reads JSON files from `config/`, writes runtime state to `.runtime/`.
2. **FastAPI backend** (`api/`) wrapping that engine for a future Angular/PrimeNG UI, with auth, persistent jobs, and Graph integration.

Both share the same scenario/slot definitions: the API treats the DB as source of truth but **seeds from JSON on startup** (`api.catalog.seed_catalog_from_json` in the lifespan) to preserve compatibility with CLI workflows. See ADR 004 in `docs/ADR.md`.

### API layout (`api/`)

- `api/main.py` — `create_app()` builds the FastAPI app. Routes are mounted **twice**: under `/api/v1` (public, in OpenAPI) and unprefixed (legacy compat, hidden). The unprefixed mount can be disabled via `API_ENABLE_LEGACY_ROUTES=false`. When changing routes, remember both mounts.
- `api/routers/` — **thin routers only**: request validation, dependency injection, permission checks, delegate to services, return response model.
- `api/services/` — business logic lives here. Persistence helpers sit in sibling modules (`api/catalog.py`, `api/jobs.py`, `api/history.py`, `api/settings.py`, `api/audit.py`).
- `api/celery_app.py` — Celery app; `api/tasks.py` defines tasks (scenario execution, Graph subscription renewal, retention pruning).
- `api/auth.py` — FastAPI Users + JWT. Production startup enforces `AUTH_SECRET` ≥ 32 chars.
- Middleware is installed in order: error handlers, HTTP logging, payload limit, rate limit, CORS, security headers. Every response carries `X-Request-ID`.

### CLI engine layout

- `scenarios/` — DSL loader, JSON-schema validation, execution engine. The engine (`scenarios/engine.py`) supports composite block steps: `group`, `repeat`, `parallel`, `try`. Atomic steps are dispatched through `operations/registry.py`.
- `operations/` — executable step implementations (selenium, http, notify, time, context, network).
- `scheduler/` — `TimeSlot` model and `SchedulerService` that orchestrates planning, network-guard checks, execution, and history.
- `network/` — VPN / enterprise-network detection used as a precondition for execution.
- `state/store.py` — JSON-file state used by the CLI (`next.json`, `last_run.json`, `executions.json`, `history.jsonl`, `scheduler.lock`) under `APP_STATE_DIR` (default `.runtime/`).

### Timezones (ADR 006)

**All DB/API timestamps are UTC.** Serializers emit ISO 8601 with `Z`. `APP_TIMEZONE` is only a fallback for local business calculations. `User.timezone_name` (IANA) is used by planning endpoints for slot windows; the frontend converts UTC → user timezone for display. Keep Pydantic response fields as `datetime` and never strip the UTC suffix.

### Graph integration

Microsoft Graph handles mail and webhook subscriptions. Celery beat periodically renews subscriptions (`GRAPH_SUBSCRIPTION_RENEW_*` env vars). SMTP is a fallback when `GRAPH_MAIL_ENABLED=false`.

## Conventions

- **Ruff** is the only linter/formatter. `line-length = 180`, enabled rules: `F`, `E4`, `E7`, `E9`, `I`.
- **Migrations**: filenames `YYYYMMDD_NNNN_short_description.py`; every migration must implement `upgrade` and `downgrade`. After DB schema changes run `make migrate-test` to validate the full cycle.
- **Tests**: use `tests/helpers.py` for service/API fixtures. Do **not** spin up real Celery workers, real browsers, real Redis, or hit real Microsoft Graph — mock at the boundary. Coverage floor is 84% across `api, app, cli, network, operations, scenarios, scheduler, state`.
- **Docs**: when you change behavior, operations, or env vars, update the relevant file in `docs/` — `scripts/check_docs.py` runs in CI and will fail otherwise.
- **OpenAPI**: `openapi.json` is committed. Run `make openapi` after any API contract change; `scripts/check_openapi.py` verifies the committed file matches the live app.
- **Env vars**: when adding one, add it to `.env.example` — `scripts/check_env_example.py` runs in `ci.ps1`.
- **Celery on Windows**: worker requires `--pool=solo` (see Makefile).

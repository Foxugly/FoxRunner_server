# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment

Windows-first project. A local virtualenv at `.venv/` is assumed; the `Makefile` and docs invoke `.\.venv\Scripts\python.exe`, `celery.exe`, `ruff.exe` directly. Python 3.12 (matches `ruff target-version` and CI). Tests use stdlib `unittest` for the CLI engine and Django's test runner for the API.

Use `make <target>` when available (Linux/Git Bash) or run the equivalent `.\.venv\Scripts\...` binary on PowerShell. `scripts/ci.ps1` reproduces the full CI pipeline locally on Windows.

## Common Commands

```bash
# --- Django backend ---

# Run Django tests (coverage floor 84%)
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py test --parallel
.\.venv\Scripts\python.exe manage.py test catalog.tests.test_scenarios

# Django migrations
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py makemigrations

# Django dev server
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000

# Bootstrap the first admin (password from BOOTSTRAP_PASSWORD env or interactive prompt)
.\.venv\Scripts\python.exe manage.py bootstrap_admin --email admin@localhost

# Celery (Windows requires --pool=solo for the worker)
make run-worker    # celery -A foxrunner.celery_app worker --pool=solo
make run-beat      # celery -A foxrunner.celery_app beat

# --- CLI engine (framework-agnostic) ---

# Run engine tests
.\.venv\Scripts\python.exe -m unittest

# Run a single test module / class / method
.\.venv\Scripts\python.exe -m unittest tests.test_scheduler
.\.venv\Scripts\python.exe -m unittest tests.test_scheduler.SchedulerTests
.\.venv\Scripts\python.exe -m unittest tests.test_scheduler.SchedulerTests.test_method

# --- Shared ---

# Lint / format
./.venv/Scripts/ruff.exe check .
./.venv/Scripts/ruff.exe format .

# Coverage
make coverage           # CLI engine (fails under 84%)
make coverage-django    # Django apps (fails under 84%)

# Full local CI
.\scripts\ci.ps1   # compile + lint + coverage + django + openapi + docs + env check
make ci            # same on Linux/Git Bash

# OpenAPI regeneration (required when API contracts change)
make openapi       # writes openapi.json
make openapi-check # export + verify against committed file
```

The CLI entrypoint is `python main.py` (see README for flags like `--validate-config`, `--plan`, `--run-next`, `--dry-run`). Module entrypoints `python -m app` and `python -m cli` also exist.

## Architecture

FoxRunner is **two overlapping products in one repo**:

1. **CLI scheduler / Selenium automation engine** (`app/`, `scheduler/`, `scenarios/`, `operations/`, `network/`, `state/`, `cli/`) — the original product. Reads JSON files from `config/`, writes runtime state to `.runtime/`.
2. **Django + Ninja backend** (`accounts/`, `catalog/`, `ops/`, `foxrunner/`) wrapping that engine for an Angular/PrimeNG UI, with auth, persistent jobs, and Graph integration.

Both share the same scenario/slot definitions: the API treats the DB as source of truth but **syncs catalog mutations back to `config/*.json`** via `catalog.services.save_scenario_definition` + `sync_slots_file` so the CLI keeps working unchanged. See ADR 004 in `docs/ADR.md`.

### Django layout

- `foxrunner/` — Django project: settings, URL conf, Celery app, exception handlers, pagination helper, request-context middleware, payload-limit + rate-limit + security-header middleware.
- `accounts/` — custom User (UUID PK, email login), djoser mounted at `/api/v1/auth/`, Ninja wrappers for the form-urlencoded login the Angular client uses, `bootstrap_admin` management command.
- `catalog/` — Scenario, Slot, ScenarioShare, step-collection endpoints, planning, history. `Scenario.owner` and `ScenarioShare.user` are `ForeignKey(User)`; the JSON envelope still surfaces `owner_user_id: str` for client compatibility.
- `ops/` — Job + JobEvent (Celery-backed), AuditEntry, AppSetting, IdempotencyKey, Microsoft Graph subscriptions + notifications, monitoring/metrics, artifacts.
- Routes are mounted **once** under `/api/v1/`. The Ninja `NinjaAPI` is built in `foxrunner/api.py`.
- Production startup enforces `DJANGO_SECRET_KEY` ≥ 32 chars (legacy `AUTH_SECRET` still accepted as fallback).

### CLI engine layout

- `scenarios/` — DSL loader, JSON-schema validation, execution engine. The engine (`scenarios/engine.py`) supports composite block steps: `group`, `repeat`, `parallel`, `try`. Atomic steps are dispatched through `operations/registry.py`.
- `operations/` — executable step implementations (selenium, http, notify, time, context, network).
- `scheduler/` — `TimeSlot` model and `SchedulerService` that orchestrates planning, network-guard checks, execution, and history.
- `network/` — VPN / enterprise-network detection used as a precondition for execution.
- `state/store.py` — JSON-file state used by the CLI (`next.json`, `last_run.json`, `executions.json`, `history.jsonl`, `scheduler.lock`) under `APP_STATE_DIR` (default `.runtime/`).
- `app/` — shared helpers: `app/redaction.py`, `app/logging_config.py` (referenced by Django `LOGGING`), `app/mail.py` (password-reset mail; delegates to `ops.graph.send_graph_mail`).

### Timezones (ADR 006)

**All DB/API timestamps are UTC.** Serializers emit ISO 8601 with `Z`. `APP_TIMEZONE` is only a fallback for local business calculations. `User.timezone_name` (IANA) is used by planning endpoints for slot windows; the frontend converts UTC → user timezone for display.

### Graph integration

Microsoft Graph handles mail and webhook subscriptions. Celery beat periodically renews subscriptions (`GRAPH_SUBSCRIPTION_RENEW_*` env vars). SMTP is a fallback when `GRAPH_MAIL_ENABLED=false`. The Graph HTTP client lives at `ops/graph.py`.

## Conventions

- **Ruff** is the only linter/formatter. `line-length = 180`, enabled rules: `F`, `E4`, `E7`, `E9`, `I`, `B`, `UP`, `SIM`. `**/migrations` is excluded so `makemigrations` output stays untouched.
- **Migrations**: Django app migrations live under `accounts/migrations/`, `catalog/migrations/`, `ops/migrations/`. After schema changes run `make migrate-test` to validate the full apply/zero/apply cycle.
- **Tests**: Django tests under `accounts/tests/`, `catalog/tests/`, `ops/tests/`, `foxrunner/tests/`. Engine tests under `tests/`. Do **not** spin up real Celery workers, real browsers, real Redis, or hit real Microsoft Graph — mock at the boundary.
- **Docs**: when you change behavior, operations, or env vars, update the relevant file in `docs/` — `scripts/check_docs.py` runs in CI and will fail otherwise.
- **OpenAPI**: `openapi.json` is committed (the Ninja schema). Run `make openapi` after any API contract change; `scripts/check_openapi.py` verifies the committed file matches the live app.
- **Env vars**: when adding one, add it to `.env.example` — `scripts/check_env_example.py` runs in `ci.ps1`.
- **Celery on Windows**: worker requires `--pool=solo` (see Makefile).

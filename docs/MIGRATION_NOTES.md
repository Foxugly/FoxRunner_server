# Migration notes (FastAPI → Django)

This file captures the handful of behavioural differences and non-obvious decisions that surfaced during the FastAPI → Django + Ninja migration (see ADR 007).

## Contract-preserved

These are documented here so a reviewer doesn't "fix" them accidentally in a future refactor:

- **Login**: form-urlencoded (`username` + `password`) returning `{access_token, token_type: "bearer"}`, not the default djoser `/auth/jwt/create` JSON shape. The wrapper lives in `accounts/api.py`.
- **Pagination**: `{items, total, limit, offset}` envelope, not DRF's `{count, next, previous, results}`. Helper: `foxrunner/pagination.py`.
- **Error envelope**: `{code, message, details}`, not Django's default HTML or DRF's `{detail: "..."}`. Handler: `foxrunner/exception_handlers.py`.
- **Idempotency-Key**: honoured on the same 3 endpoints as FastAPI (`POST /scenarios`, `POST /slots`, `POST /users/{id}/scenarios/{sid}/jobs`) and partitioned by `current_user.id` (UUID, post-Phase-5) — a minor divergence from FastAPI which partitioned by email.
- **X-Request-ID**: echoed on every response by `RequestContextMiddleware`.
- **UTC Z suffix**: explicit `dt.astimezone(utc).isoformat().replace("+00:00", "Z")` — Django's default emits `+00:00`.

## Dual-stack quirks (active until Phase 13)

- The shared dev DB at `.runtime/users.db` has Alembic-managed schema. Django's `makemigrations` runs against the test DB (fresh schema); applying Django migrations to the dev DB requires `--fake-initial` for the initial per-app migrations since the tables already exist.
- Celery beat still points at the FastAPI tasks (`api.tasks.renew_graph_subscriptions_task`, `api.tasks.prune_retention_task`). The Django replacements are stubs until Phase 12/13.
- Scenarios/slots JSON-file sync to `config/*.json` is handled by the FastAPI app. Django mutations write only to the DB. Phase 13 adds the file sync to `catalog/services.save_scenario_definition`.

## Ruff + migrations

- Auto-generated Django migration files are excluded via `[tool.ruff] extend-exclude = ["server_django/**/migrations"]` in `pyproject.toml`. Keeps `makemigrations` output lint-clean without manual reformatting.
- `SILENCED_SYSTEM_CHECKS = ["models.E034"]` in `settings.py` allows preserving Alembic-era index names longer than 30 chars (Django's E034 is a legacy Oracle limit).

## Index parity

The Django `Meta.indexes` and `Meta.constraints` declarations mirror the indexes declared in the Alembic chain. Any single-column index that's auto-derived from `db_index=True` / `unique=True` / `ForeignKey(...)` is left implicit; only composites and a few late-added single-column indexes are declared explicitly. The verified inventory lives in the docstrings of `catalog/models.py` and `ops/models.py`.

## UUID promotion order

Phase 5 is **data migration → schema migration → FK promotion**, in that order, per app:

1. `catalog/0002_normalize_owner_user_id.py`, `ops/0002_normalize_actor_user_id.py`: `RunPython` rewrites email-stored values to UUID strings.
2. `catalog/0003_owner_fk_promotion.py`, `ops/0003_user_fk_promotion.py`: `AlterField` CharField(320) → UUIDField → ForeignKey(User).

Any row whose `*_user_id` value doesn't resolve to a real User after the data migration will block the FK promotion. `/admin/import` now skips such rows and reports `skipped_scenarios: N` in the response.

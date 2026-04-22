# FoxRunner Backend — FastAPI → Django + Ninja Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the FastAPI backend (`api/`) with a functionally-equivalent Django + Django Ninja stack living under `server_django/`, while leaving every non-web module intact (`scheduler/`, `scenarios/`, `operations/`, `network/`, `state/`, `cli/`, `app/`).

**Architecture:** Three Django apps (`accounts`, `catalog`, `ops`) behind a single `NinjaAPI` mounted at `/api/v1/`. JWT auth via `djangorestframework-simplejwt` exposed through djoser (`/api/v1/auth/...`) plus thin Ninja wrappers that preserve the existing `POST /auth/jwt/login` (form-urlencoded) / `POST /auth/jwt/logout` contract the Angular client uses. Celery tasks become sync Django-ORM calls. Postgres in prod, SQLite in dev. The migration runs side-by-side until phase 13, where `api/` and `migrations/` are deleted and `server_django/*` is promoted to repo root.

**Tech Stack:** Python 3.12, Django 5.x, django-ninja ≥1.3, djoser ≥2.2, djangorestframework-simplejwt ≥5.3, django-redis, django-cors-headers, django-ratelimit, psycopg[binary] (prod), Celery, Redis. Tests use `django.test.TestCase` + Ninja `TestClient`. Lint: ruff. Coverage floor: 84 %.

**Branch:** `refactor/django` (already created — `f68db62 scaffold(django)` is the starting point).

**Estimated effort:** 25–30 hours across 13 phases.

---

## Frontend contract — what MUST NOT change

The Angular client is volontairement couplé à ces 6 conventions FastAPI. Reproduce them à l'identique:

| Convention | Required behaviour |
|---|---|
| Login | `POST /api/v1/auth/jwt/login`, body `application/x-www-form-urlencoded` `username=…&password=…`, response `{"access_token": str, "token_type": "bearer"}` |
| Pagination envelope | `{"items": [], "total": 0, "limit": 100, "offset": 0}` with `?limit=&offset=` query params (NOT DRF's `count/next/previous/results`) |
| Errors | `{"code": "snake_case", "message": "Texte FR.", "details": Any \| null}` |
| `Idempotency-Key` header | Honoured on `POST /scenarios`, `POST /slots`, `POST /users/{id}/scenarios/{sid}/jobs` |
| `X-Request-ID` | Echoed on every response (already wired in `server_django/foxrunner/middleware.py`) |
| Timestamps | UTC ISO 8601 with `Z` suffix |

Step-collection reads (`GET /users/{id}/scenarios/{sid}/step-collections/{collection}`) **return raw arrays**, not the paginated envelope.

---

## Operating rules (apply throughout)

- **Don't touch** `app/`, `scheduler/`, `scenarios/`, `operations/`, `network/`, `state/`, `cli/`. Only phase 13 fixes broken imports there.
- **Don't push** without the user's explicit OK. Atomic, conventional commits on `refactor/django`.
- **No new features.** Functional parity. Subtle behaviours that lack an obvious Django equivalent → document in `docs/MIGRATION_NOTES.md`.
- **Error messages stay French**, mot pour mot.
- **Commit cadence:** at the end of every phase + at every "tests vert" checkpoint inside a phase. Use conventional commits (`feat(django): …`, `test(django): …`, `chore(django): …`, `refactor(django): …`).
- **Always re-read** the existing FastAPI module before porting — it is the authoritative spec.
- **Verification commands run from the repo root**, but Django commands run from `server_django/`. Use `..\.venv\Scripts\python.exe manage.py …` from `server_django/`, or `.\.venv\Scripts\python.exe server_django\manage.py …` from the root.
- **Coverage and lint** must stay green at every commit. If a commit drops coverage below 84 %, add tests in the same commit.
- **Russell-conserve durcissements récents** (CHANGELOG.md `Unreleased` section): Graph clientState validation, idempotency race handling, payload limit on chunked, rate limit Redis, AUTH_SECRET ≥32 chars, HistoryStore process lock, ProcessLock 3600 s, mail/Graph ERROR logs.

---

## Self-review before any code (analyst phase)

Before starting Phase 1, the executing agent must:
1. `git status` clean, on branch `refactor/django`.
2. Read the entire scaffold under `server_django/` (already created at commit `f68db62`).
3. Read `api/main.py`, `api/auth.py`, `api/dependencies.py`, `api/permissions.py`, `api/redaction.py`, `api/db.py`, `api/timezones.py`, `api/feature_flags.py`, `api/version.py`, `api/health.py` and `migrations/versions/20260422_0012_normalize_owner_user_id.py`. These define the contract this plan migrates.
4. Confirm Python 3.12, venv at `.venv\`, FastAPI tests pass: `.\.venv\Scripts\python.exe -m unittest`.
5. Install Django deps once: `.\.venv\Scripts\python.exe -m pip install -r server_django\requirements.txt`.
6. Run the scaffold's smoke check: `cd server_django && ..\.venv\Scripts\python.exe manage.py check && ..\.venv\Scripts\python.exe manage.py migrate`.

If any of those fail — **stop and report**, do not improvise.

---

## File structure (final state, after phase 13 swap)

```
foxrunner/
  manage.py
  pyproject.toml
  requirements.txt / requirements-dev.txt / *.lock
  Dockerfile
  docker-compose.yml
  Makefile
  alembic.ini                # DELETED
  api/                       # DELETED
  migrations/                # DELETED (Alembic — Django migrations live inside each app)
  app/                       # untouched (CLI config + entrypoint)
  scheduler/ scenarios/ operations/ network/ state/ cli/  # untouched
  config/ schemas/           # untouched
  foxrunner/                 # ex server_django/foxrunner — settings, urls, celery, ninja api
    settings.py urls.py wsgi.py asgi.py celery.py api.py auth.py middleware.py exception_handlers.py rate_limit.py idempotency.py pagination.py serializers.py
  accounts/ catalog/ ops/    # ex server_django/accounts etc.
  tests/                     # untouched non-API tests + rewritten test_api*
```

Until phase 13 the new code lives under `server_django/` and FastAPI under `api/`.

---

## Phase 1 — Verify scaffold + dependency baseline (~30 min)

The scaffold (`f68db62`) is already in place. This phase ensures the foundation actually runs and adds the missing dev-deps to the lock files.

### Task 1.1 — Smoke test the scaffold

**Files (read-only):** `server_django/manage.py`, `server_django/foxrunner/settings.py`, `server_django/foxrunner/urls.py`, `server_django/foxrunner/api.py`.

- [ ] **Step 1 — Confirm working tree clean and on the right branch**

```bash
git status
git branch --show-current   # expect: refactor/django
```

- [ ] **Step 2 — Install Django deps into the existing venv**

```bash
.\.venv\Scripts\python.exe -m pip install -r server_django\requirements.txt
```

Expected: pip resolves successfully (existing FastAPI deps stay in place).

- [ ] **Step 3 — Run Django system check + migrate**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py migrate
```

Expected: `System check identified no issues (0 silenced).` and `Applying accounts.0001_initial… OK`.

- [ ] **Step 4 — Boot dev server on port 8001 (background)** and curl `/api/v1/health`

In one shell:
```bash
cd server_django
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```
In another shell:
```bash
curl -i http://127.0.0.1:8001/api/v1/health
```
Expected: `HTTP/1.1 200 OK`, body `{"status":"ok"}`, header `X-Request-ID: <uuid>`.

- [ ] **Step 5 — Confirm Ninja OpenAPI is reachable**

```bash
curl -s http://127.0.0.1:8001/api/v1/openapi.json | head -c 400
```
Expected: JSON starting with `{"openapi": "3.…"`.

- [ ] **Step 6 — Stop the dev server.** No commit yet.

### Task 1.2 — Pin dev dependencies, set up tooling

**Files:**
- Modify: `server_django/requirements.txt` (no change yet — pinning happens at phase 13)
- Read: `pyproject.toml` (check ruff target stays Python 3.12)

- [ ] **Step 1 — Confirm ruff is happy with the scaffold**

```bash
.\.venv\Scripts\ruff.exe check server_django
.\.venv\Scripts\ruff.exe format --check server_django
```
Expected: both pass. If `format --check` fails, run `ruff format server_django` and commit as `chore(django): apply ruff format to scaffold`.

- [ ] **Step 2 — Add coverage configuration for `server_django` apps**

Edit `pyproject.toml`'s `[tool.coverage.run]` section: append `"server_django/accounts"`, `"server_django/catalog"`, `"server_django/ops"`, `"server_django/foxrunner"` to `source` (do **not** remove the existing `api`, `app`, `cli`, etc. — both backends coexist until phase 13).

```bash
.\.venv\Scripts\python.exe -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['tool']['coverage']['run'])"
```
Expected: source list includes both old and new dirs.

- [ ] **Step 3 — Commit**

```bash
git add pyproject.toml
git commit -m "chore(django): include server_django apps in coverage source"
```

---

## Phase 2 — Models for catalog + ops (~2 h)

Port the 11 SQLAlchemy models from `api/models.py` (User is already done) into `catalog/models.py` and `ops/models.py`. Do **not** add the email→UUID FKs yet — that's phase 5.

> **Correction (post-Phase-2.1 review):** All `*_user_id` columns (`Scenario.owner_user_id`, `ScenarioShare.user_id`, `Job.user_id`, `AuditEntry.actor_user_id`, `AppSetting.updated_by`, `IdempotencyKey.user_id`) **stay as `CharField(max_length=320)`** in Phase 2 to match the existing Alembic `String(320)` columns. Using `UUIDField` here would create a silent schema mismatch on PostgreSQL during the dual-stack window (the prod DB has `varchar(320)`, the Django model would claim `uuid`). Phase 5 does the type flip (`CharField(320)` → `UUIDField` → `ForeignKey(User)`) in one `AlterField` step **after** the data migration normalizes any email-stored values to UUID strings.

> **Correction:** Only the indexes that actually exist in the Alembic chain go into `Meta.indexes`. Verified set on catalog tables (from `migrations/versions/20260421_0001_initial.py` and `20260421_0011_operational_indexes.py`):
> - `scenarios`: `ix_scenarios_owner_user_id` (single col), `ix_scenarios_scenario_id` (single col, unique). Both auto-derived from `db_index=True` / `unique=True` on the field — no `Meta.indexes` entry needed.
> - `scenario_shares`: `ix_scenario_shares_scenario_id` (auto from FK), `ix_scenario_shares_user_id` (auto from `db_index=True`). + named unique constraint `uq_scenario_share_user` on `(scenario_id, user_id)`.
> - `slots`: `ix_slots_scenario_id` (auto from FK), `ix_slots_slot_id` (auto from `unique=True`), `ix_slots_scenario_enabled` (composite, **must be declared explicitly** in `Slot.Meta.indexes`).

Before writing any field, re-open `api/models.py`. The mapping table from the handoff brief is the authoritative target.

> **Dual-stack migration quirk (`--fake-initial`).** During Phases 2–12 the dev DB at `.runtime/users.db` is shared with the FastAPI app — Alembic has already created the catalog/ops/auth tables. When you run `manage.py migrate <app>` for the first time, Django crashes with "table X already exists" because `django_migrations` has no record of the initial migration. The correct workaround during the dual-stack window is `manage.py migrate <app> --fake-initial`, which records the initial migration as applied without re-creating the tables. This is the standard Django pattern for "schema already exists, just record the migration as done." After Phase 13, when Alembic is gone, `migrate` works normally. Document this in `docs/MIGRATION_NOTES.md` (created in Phase 11) so contributors don't waste time debugging it.

### Task 2.1 — Catalog models (Scenario, ScenarioShare, Slot)

**Files:**
- Modify: `server_django/catalog/models.py`
- Test: `server_django/catalog/tests/test_models.py` (create)

- [ ] **Step 1 — Write failing model test**

Create `server_django/catalog/__init__.py` already exists. Create the tests directory and a smoke test:

```python
# server_django/catalog/tests/__init__.py  (empty)
# server_django/catalog/tests/test_models.py
from django.test import TestCase

from catalog.models import Scenario, ScenarioShare, Slot


class CatalogModelSmokeTest(TestCase):
    def test_scenario_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo", owner_user_id="00000000-0000-0000-0000-000000000001", description="d", definition={"x": 1})
        self.assertEqual(s.definition, {"x": 1})
        self.assertEqual(s.description, "d")
        self.assertIsNotNone(s.created_at)

    def test_slot_round_trip(self):
        s = Scenario.objects.create(scenario_id="demo2", owner_user_id="00000000-0000-0000-0000-000000000002")
        slot = Slot.objects.create(slot_id="slot1", scenario=s, days=[0, 1], start="08:00", end="09:00")
        self.assertEqual(slot.days, [0, 1])
        self.assertTrue(slot.enabled)

    def test_share_uniqueness(self):
        s = Scenario.objects.create(scenario_id="demo3", owner_user_id="00000000-0000-0000-0000-000000000003")
        ScenarioShare.objects.create(scenario=s, user_id="00000000-0000-0000-0000-000000000004")
        with self.assertRaises(Exception):
            ScenarioShare.objects.create(scenario=s, user_id="00000000-0000-0000-0000-000000000004")
```

- [ ] **Step 2 — Run the test → expect ImportError**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test catalog -v 2
```
Expected: failure (`ImportError: cannot import name 'Scenario'`).

- [ ] **Step 3 — Implement the three models in `server_django/catalog/models.py`**

Use the SQLAlchemy column types from `api/models.py:25-61` as the spec. Field-by-field correspondence:

```python
from __future__ import annotations
from django.db import models


class Scenario(models.Model):
    scenario_id = models.CharField(max_length=128, unique=True, db_index=True)
    owner_user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5 (CharField -> UUIDField -> ForeignKey)
    description = models.TextField(default="", blank=True)
    definition = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scenarios"

    def __str__(self) -> str:
        return self.scenario_id


class ScenarioShare(models.Model):
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="shares", to_field="scenario_id", db_column="scenario_id")
    user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5

    class Meta:
        db_table = "scenario_shares"
        constraints = [
            models.UniqueConstraint(fields=["scenario", "user_id"], name="uq_scenario_share_user"),
        ]


class Slot(models.Model):
    slot_id = models.CharField(max_length=128, unique=True, db_index=True)
    scenario = models.ForeignKey(Scenario, on_delete=models.CASCADE, related_name="slots", to_field="scenario_id", db_column="scenario_id")
    days = models.JSONField(default=list, blank=True)
    start = models.CharField(max_length=5)
    end = models.CharField(max_length=5)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "slots"
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(fields=["scenario", "enabled"], name="ix_slots_scenario_enabled"),
        ]
```

> Note on FK to `scenario_id` (CharField): mirrors the SQLAlchemy schema where the FK is on the `scenario_id` business key, not the surrogate `id`. This keeps the existing DB column layout and avoids a destructive rewrite during phase 13.

> Note on `owner_user_id`/`user_id` typing: `CharField(max_length=320)` matches the existing Alembic `String(320)` column exactly. Do NOT use `UUIDField` here — that would be a silent PostgreSQL schema mismatch during the dual-stack window. Phase 5 normalizes the values then `AlterField`s the column type.

- [ ] **Step 4 — Generate migration**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py makemigrations catalog
```
Expected: `catalog/migrations/0001_initial.py` created.

- [ ] **Step 5 — Run tests → expect PASS**

```bash
..\.venv\Scripts\python.exe manage.py test catalog -v 2
```
Expected: 3 tests pass.

- [ ] **Step 6 — Commit**

```bash
git add server_django/catalog/models.py server_django/catalog/migrations/0001_initial.py server_django/catalog/tests/
git commit -m "feat(django): catalog models (Scenario, ScenarioShare, Slot)"
```

### Task 2.2 — Ops models (Job, JobEvent, AuditEntry, ExecutionHistory, AppSetting, IdempotencyKey, GraphSubscription, GraphNotification)

**Files:**
- Modify: `server_django/ops/models.py`
- Test: `server_django/ops/tests/test_models.py` (create)

- [ ] **Step 1 — Write failing smoke tests**

Cover one round-trip per model + the two unique constraints (`(execution_id, slot_id, scenario_id)` for ExecutionHistory, `(user_id, key)` for IdempotencyKey, `(subscription, resource, change_type, lifecycle_event)` for GraphNotification).

```python
# server_django/ops/tests/test_models.py
from django.test import TestCase

from ops.models import (
    AppSetting, AuditEntry, ExecutionHistory, GraphNotification, GraphSubscription,
    IdempotencyKey, Job, JobEvent,
)


class OpsModelSmokeTest(TestCase):
    def test_job_and_event(self):
        job = Job.objects.create(job_id="j1", user_id="00000000-0000-0000-0000-000000000001", kind="run", target_id="t1", status="queued", payload={"a": 1})
        evt = JobEvent.objects.create(job=job, event_type="started", message="ok")
        self.assertEqual(job.events.count(), 1)
        self.assertEqual(evt.payload, {})

    def test_history_unique(self):
        ExecutionHistory.objects.create(slot_key="k", slot_id="s1", scenario_id="sc1", execution_id="e1", executed_at="2026-04-22T10:00:00Z", status="ok")
        with self.assertRaises(Exception):
            ExecutionHistory.objects.create(slot_key="k", slot_id="s1", scenario_id="sc1", execution_id="e1", executed_at="2026-04-22T10:00:00Z", status="ok")

    def test_idempotency_unique(self):
        IdempotencyKey.objects.create(user_id="00000000-0000-0000-0000-000000000001", key="k1", request_fingerprint="f1", response={"a": 1})
        with self.assertRaises(Exception):
            IdempotencyKey.objects.create(user_id="00000000-0000-0000-0000-000000000001", key="k1", request_fingerprint="f1", response={"a": 1})

    def test_graph_notification_dedupe(self):
        sub = GraphSubscription.objects.create(subscription_id="sub1")
        GraphNotification.objects.create(subscription_id="sub1", change_type="updated", resource="r1", lifecycle_event=None, raw_payload={})
        # Same key should still insert because lifecycle_event=NULL is not equal to NULL in SQL strict mode.
        # But the model-level constraint must reject identical non-null tuples — verify:
        GraphNotification.objects.create(subscription_id="sub1", change_type="updated", resource="r1", lifecycle_event="renew", raw_payload={})
        with self.assertRaises(Exception):
            GraphNotification.objects.create(subscription_id="sub1", change_type="updated", resource="r1", lifecycle_event="renew", raw_payload={})

    def test_audit_and_settings(self):
        AppSetting.objects.create(key="k", value={"a": 1}, description="d")
        AuditEntry.objects.create(actor_user_id="00000000-0000-0000-0000-000000000001", action="create", target_type="scenario", target_id="s1", before={}, after={"a": 1})
```

- [ ] **Step 2 — Run tests → expect ImportError**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test ops -v 2
```

- [ ] **Step 3 — Implement `ops/models.py`** using `api/models.py:64-184` as the spec. Mirror every column type, default, nullability, and constraint:

```python
from __future__ import annotations
from django.db import models


class Job(models.Model):
    job_id = models.CharField(max_length=64, unique=True, db_index=True)
    celery_task_id = models.CharField(max_length=128, null=True, blank=True, db_index=True)
    user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5 (CharField -> UUIDField -> ForeignKey)
    kind = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=128, db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    dry_run = models.BooleanField(default=True)
    exit_code = models.IntegerField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "jobs"
        indexes = [
            # From migrations/versions/20260421_0007_query_indexes.py
            models.Index(fields=["status", "updated_at"], name="ix_jobs_status_updated_at"),
        ]


class JobEvent(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="events", to_field="job_id", db_column="job_id")
    event_type = models.CharField(max_length=64, db_index=True)
    level = models.CharField(max_length=16, default="info")
    message = models.TextField(default="", blank=True)
    step = models.CharField(max_length=128, null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "job_events"
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(fields=["job", "created_at"], name="ix_job_events_job_created_at"),
        ]


class GraphSubscription(models.Model):
    subscription_id = models.CharField(max_length=128, unique=True, db_index=True)
    resource = models.CharField(max_length=512, default="", db_index=True)
    change_type = models.CharField(max_length=128, default="")
    notification_url = models.CharField(max_length=1024, default="")
    lifecycle_notification_url = models.CharField(max_length=1024, null=True, blank=True)
    client_state = models.CharField(max_length=256, null=True, blank=True)
    expiration_datetime = models.DateTimeField(null=True, blank=True, db_index=True)  # ix_graph_subscriptions_expiration (rev 20260421_0007)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "graph_subscriptions"


class GraphNotification(models.Model):
    subscription_id = models.CharField(max_length=128, db_index=True)
    change_type = models.CharField(max_length=128, db_index=True)
    resource = models.CharField(max_length=1024, default="")
    tenant_id = models.CharField(max_length=128, null=True, blank=True)
    client_state = models.CharField(max_length=256, null=True, blank=True)
    lifecycle_event = models.CharField(max_length=128, null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "graph_notifications"
        constraints = [
            models.UniqueConstraint(
                fields=["subscription_id", "resource", "change_type", "lifecycle_event"],
                name="uq_graph_notification_dedupe",
            ),
        ]


class AuditEntry(models.Model):
    actor_user_id = models.CharField(max_length=320, null=True, blank=True, db_index=True)  # FK(User) + nullable promotion in phase 5
    action = models.CharField(max_length=128, db_index=True)
    target_type = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=320, db_index=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)  # ix_audit_log_created_at (rev 20260421_0007)

    class Meta:
        db_table = "audit_log"


class ExecutionHistory(models.Model):
    slot_key = models.CharField(max_length=256, db_index=True)
    slot_id = models.CharField(max_length=128, db_index=True)
    scenario_id = models.CharField(max_length=128, db_index=True)
    execution_id = models.CharField(max_length=128, null=True, db_index=True)
    executed_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    step = models.CharField(max_length=128, default="", blank=True)
    message = models.TextField(default="", blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "execution_history"
        constraints = [
            models.UniqueConstraint(fields=["execution_id", "slot_id", "scenario_id"], name="uq_execution_history_identity"),
        ]
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(fields=["scenario_id", "executed_at"], name="ix_execution_history_scenario_executed_at"),
        ]


class AppSetting(models.Model):
    key = models.CharField(max_length=128, unique=True, db_index=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.TextField(default="", blank=True)
    updated_by = models.CharField(max_length=320, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_settings"


class IdempotencyKey(models.Model):
    user_id = models.CharField(max_length=320, db_index=True)  # internal key; not promoted in phase 5
    key = models.CharField(max_length=128, db_index=True)
    request_fingerprint = models.CharField(max_length=128)
    status_code = models.IntegerField(default=200)
    response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_keys"
        constraints = [
            models.UniqueConstraint(fields=["user_id", "key"], name="uq_idempotency_user_key"),
        ]
```

- [ ] **Step 4 — Generate migration**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py makemigrations ops
..\.venv\Scripts\python.exe manage.py migrate
```

- [ ] **Step 5 — Run tests → expect PASS**

```bash
..\.venv\Scripts\python.exe manage.py test ops -v 2
```

- [ ] **Step 6 — Verify migration is drift-free**

```bash
..\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```
Expected: `No changes detected`.

- [ ] **Step 7 — Commit**

```bash
git add server_django/ops/models.py server_django/ops/migrations/ server_django/ops/tests/
git commit -m "feat(django): ops models (Job, JobEvent, AuditEntry, History, Settings, Idempotency, Graph)"
```

### Task 2.3 — Register everything in Django admin

**Files:** `server_django/catalog/admin.py`, `server_django/ops/admin.py`.

- [ ] **Step 1 — Implement minimal `@admin.register(...)` for each model**

Use `list_display`, `search_fields`, `list_filter`, `readonly_fields=("id", "created_at", "updated_at")` patterns. Mirror the spirit of `accounts/admin.py:9-27`. Group: `actor_user_id`, `target_id` filterable on AuditEntry; `status`, `kind` on Job; `key` searchable on AppSetting; etc. Keep it short — one ModelAdmin per model.

- [ ] **Step 2 — Verify admin loads**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py createsuperuser --email admin@local --noinput || true
echo "DJANGO_SUPERUSER_PASSWORD=changeme" | ..\.venv\Scripts\python.exe manage.py shell -c "from accounts.models import User; u=User.objects.get(email='admin@local'); u.set_password('changeme'); u.save()"
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001 &
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/admin/
```
Expected: `302` (redirect to login). Then stop the server.

- [ ] **Step 3 — Commit**

```bash
git add server_django/catalog/admin.py server_django/ops/admin.py
git commit -m "feat(django): wire all models into Django admin"
```

---

## Phase 3 — Auth: djoser + Ninja wrappers (~2 h)

The scaffold already mounts djoser at `/api/v1/auth/` and `/api/v1/auth/jwt/`. djoser's defaults give `POST /auth/jwt/create` (JSON `{email, password}` → `{access, refresh}`) and `POST /auth/users/` (register). The Angular client uses different shapes — wrappers must bridge that gap.

### Task 3.1 — Wrap login (`POST /api/v1/auth/jwt/login`, form-urlencoded)

**Files:**
- Modify: `server_django/accounts/api.py`
- Test: `server_django/accounts/tests/test_auth.py` (create)

- [ ] **Step 1 — Failing test: POST form data → 200 with `{access_token, token_type}`**

```python
# server_django/accounts/tests/__init__.py  (empty)
# server_django/accounts/tests/test_auth.py
from django.test import TestCase, Client
from accounts.models import User


class JwtLoginTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="u@x.com", password="passw0rd!")

    def test_form_login_returns_access_token(self):
        client = Client()
        r = client.post(
            "/api/v1/auth/jwt/login",
            data="username=u@x.com&password=passw0rd!",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertIn("access_token", body)

    def test_form_login_bad_password_returns_401_with_french_error(self):
        client = Client()
        r = client.post(
            "/api/v1/auth/jwt/login",
            data="username=u@x.com&password=wrong",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(r.status_code, 401)
        body = r.json()
        self.assertEqual(body["code"], "unauthorized")
        self.assertIn("invalides", body["message"].lower())
```

- [ ] **Step 2 — Run → expect 404**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test accounts -v 2
```

- [ ] **Step 3 — Implement the wrapper in `server_django/accounts/api.py`**

```python
from __future__ import annotations
from typing import Any
from urllib.parse import parse_qs

from django.contrib.auth import authenticate
from ninja import Router
from ninja.errors import HttpError
from rest_framework_simplejwt.tokens import RefreshToken

router = Router(tags=["auth"])


@router.post("/auth/jwt/login", auth=None, include_in_schema=True, summary="Login (form data) — frontend contract")
def jwt_login(request) -> dict[str, Any]:
    raw = request.body.decode("utf-8") if request.body else ""
    form = parse_qs(raw)
    username = (form.get("username", [""]) + form.get("email", [""]))[0]
    password = (form.get("password", [""]))[0]
    if not username or not password:
        raise HttpError(400, "Identifiants invalides.")
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        raise HttpError(401, "Identifiants invalides.")
    refresh = RefreshToken.for_user(user)
    return {"access_token": str(refresh.access_token), "token_type": "bearer"}


@router.post("/auth/jwt/logout", summary="Logout — blacklist the current refresh token")
def jwt_logout(request) -> dict[str, str]:
    raw = request.body.decode("utf-8") if request.body else ""
    form = parse_qs(raw)
    refresh_str = (form.get("refresh", [""]) or [""])[0]
    if refresh_str:
        try:
            RefreshToken(refresh_str).blacklist()
        except Exception:
            pass
    return {"status": "ok"}
```

> The placeholder `users_me_placeholder` in the scaffold is replaced in Task 3.3.

- [ ] **Step 4 — Run tests → expect PASS**

```bash
..\.venv\Scripts\python.exe manage.py test accounts.tests.test_auth -v 2
```

- [ ] **Step 5 — Commit**

```bash
git add server_django/accounts/api.py server_django/accounts/tests/
git commit -m "feat(django): /api/v1/auth/jwt/login form-urlencoded wrapper"
```

### Task 3.2 — Password reset wrappers (`forgot-password`, `reset-password`)

**Files:** `server_django/accounts/api.py` (extend), tests in same file.

The FastAPI contract is `POST /api/v1/auth/forgot-password {email}` and `POST /api/v1/auth/reset-password {token, password}`. djoser exposes them under `/auth/users/reset_password/` and `/auth/users/reset_password_confirm/`. Add Ninja routes that proxy to the djoser internals (or to Django's `PasswordResetTokenGenerator` directly — simpler since we control mailing via `api/mail.py`).

- [ ] **Step 1 — Test: POST `/api/v1/auth/forgot-password` returns 202 even for unknown email** (security: don't leak existence)

```python
def test_forgot_password_silent_for_unknown_email(self):
    r = self.client.post("/api/v1/auth/forgot-password", data={"email": "nobody@x.com"}, content_type="application/json")
    self.assertEqual(r.status_code, 202)
    self.assertEqual(r.json(), {"status": "queued"})
```

- [ ] **Step 2 — Test: known email triggers `api.mail.send_password_reset_email` (mock the mailer)**

Use `unittest.mock.patch("api.mail.send_password_reset_email")` and assert it was called once with the user.

- [ ] **Step 3 — Implement `forgot-password`** in `accounts/api.py`:

```python
from ninja import Schema
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from accounts.models import User


class ForgotPasswordIn(Schema):
    email: str


@router.post("/auth/forgot-password", auth=None, response={202: dict})
def forgot_password(request, payload: ForgotPasswordIn):
    user = User.objects.filter(email=payload.email).first()
    if user is not None:
        token = PasswordResetTokenGenerator().make_token(user)
        from api.mail import send_password_reset_email  # reuse FastAPI helper until phase 13
        send_password_reset_email(user, token)
    return 202, {"status": "queued"}
```

- [ ] **Step 4 — Implement `reset-password`** symmetrically:

```python
class ResetPasswordIn(Schema):
    token: str
    password: str
    user_id: str  # email or UUID


@router.post("/auth/reset-password", auth=None)
def reset_password(request, payload: ResetPasswordIn):
    try:
        user = User.objects.get(email=payload.user_id)
    except User.DoesNotExist:
        try:
            user = User.objects.get(id=payload.user_id)
        except (User.DoesNotExist, ValueError):
            raise HttpError(400, "Token invalide ou expire.")
    if not PasswordResetTokenGenerator().check_token(user, payload.token):
        raise HttpError(400, "Token invalide ou expire.")
    user.set_password(payload.password)
    user.save(update_fields=["password"])
    return {"status": "ok"}
```

- [ ] **Step 5 — Run tests → expect PASS**

- [ ] **Step 6 — Commit**

```bash
git add server_django/accounts/api.py server_django/accounts/tests/test_auth.py
git commit -m "feat(django): /api/v1/auth/forgot-password and /reset-password wrappers"
```

### Task 3.3 — `/users/me` (GET, PATCH)

**Files:** `server_django/accounts/api.py`, schemas in `server_django/foxrunner/serializers.py` (create file).

- [ ] **Step 1 — Failing test for both verbs**

```python
def test_users_me_get(self):
    self._login()
    r = self.client.get("/api/v1/users/me", HTTP_AUTHORIZATION=f"Bearer {self.token}")
    self.assertEqual(r.status_code, 200)
    body = r.json()
    self.assertEqual(body["email"], "u@x.com")
    self.assertEqual(body["timezone_name"], "Europe/Brussels")
    self.assertFalse(body["is_superuser"])

def test_users_me_patch_timezone(self):
    self._login()
    r = self.client.patch(
        "/api/v1/users/me",
        data={"timezone_name": "Europe/Paris"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {self.token}",
    )
    self.assertEqual(r.status_code, 200)
    self.assertEqual(r.json()["timezone_name"], "Europe/Paris")
```

(Add a `_login()` helper in setUp that does the form login and stores the token.)

- [ ] **Step 2 — Create `server_django/foxrunner/serializers.py`** with reusable `UserOut`, `UserPatchIn`:

```python
from __future__ import annotations
from datetime import datetime
from uuid import UUID

from ninja import Schema


class UserOut(Schema):
    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    timezone_name: str
    date_joined: datetime


class UserPatchIn(Schema):
    timezone_name: str | None = None
    email: str | None = None
```

- [ ] **Step 3 — Replace the placeholder `users_me_placeholder` in `accounts/api.py`** with real GET + PATCH using the schemas. Validate `timezone_name` via `zoneinfo.ZoneInfo` and raise `HttpError(422, "Timezone IANA invalide.")` on failure.

- [ ] **Step 4 — Run tests → expect PASS**

- [ ] **Step 5 — Commit**

```bash
git add server_django/accounts/api.py server_django/foxrunner/serializers.py server_django/accounts/tests/test_auth.py
git commit -m "feat(django): /api/v1/users/me GET + PATCH"
```

### Task 3.4 — Bootstrap superuser command

**Files:** `server_django/accounts/management/commands/bootstrap_admin.py` (create), tests in `server_django/accounts/tests/test_bootstrap.py`.

Replicate `scripts/bootstrap_admin.py` semantics: read `BOOTSTRAP_PASSWORD` from env (or prompt via getpass), create-or-update an admin user, never accept `--password` on the CLI.

- [ ] **Step 1 — Failing test**

```python
import os
from django.core.management import call_command
from django.test import TestCase
from accounts.models import User


class BootstrapAdminTest(TestCase):
    def test_creates_superuser_from_env(self):
        os.environ["BOOTSTRAP_PASSWORD"] = "S3cret!Strong"
        try:
            call_command("bootstrap_admin", "--email", "boot@x.com")
        finally:
            del os.environ["BOOTSTRAP_PASSWORD"]
        u = User.objects.get(email="boot@x.com")
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.check_password("S3cret!Strong"))
```

- [ ] **Step 2 — Implement** the management command. Skeleton:

```python
import os
from getpass import getpass
from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Create or update a superuser. Password from BOOTSTRAP_PASSWORD env or interactive prompt."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)

    def handle(self, *args, email: str, **opts):
        password = os.environ.get("BOOTSTRAP_PASSWORD") or getpass("Mot de passe: ")
        if len(password) < 8:
            raise SystemExit("Mot de passe trop court (min 8).")
        user, _ = User.objects.get_or_create(email=email, defaults={"is_superuser": True, "is_staff": True, "is_active": True, "is_verified": True})
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        user.is_verified = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Superuser {email} ready."))
```

- [ ] **Step 3 — Run test → expect PASS**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test accounts.tests.test_bootstrap -v 2
```

- [ ] **Step 4 — Commit**

```bash
git add server_django/accounts/management/
git commit -m "feat(django): bootstrap_admin management command (replaces scripts/bootstrap_admin.py)"
```

---

## Phase 4 — Catalog endpoints (scenarios + slots + shares + steps + planning) (~5 h)

**This is the largest phase.** Reference: `api/routers/catalog.py` (422 LoC), `api/services/scenarios.py`, `api/services/slots.py`, `api/services/steps.py`, `api/catalog.py`, `api/catalog_queries.py`, `api/permissions.py`, `api/idempotency.py`, `api/pagination.py`, `api/serializers.py`.

Strategy: port one endpoint at a time, keeping the URL/verb/payload/response shape identical. Each endpoint goes through the same TDD loop (failing test → implement → green). Group commits by sub-feature (CRUD scenarios, then shares, then slots, then steps, then planning).

### Task 4.1 — Build the shared plumbing

**Files (create):**
- `server_django/foxrunner/pagination.py`
- `server_django/foxrunner/idempotency.py`
- `server_django/foxrunner/permissions.py`
- `server_django/catalog/services.py` (extend)

- [ ] **Step 1 — Pagination helper**

```python
# server_django/foxrunner/pagination.py
from __future__ import annotations
from typing import Any, TypeVar

from django.db.models import QuerySet
from ninja import Query, Schema


class PageQuery(Schema):
    limit: int = 100
    offset: int = 0


T = TypeVar("T")


def paginate(qs: QuerySet[T], *, page: PageQuery, serialize) -> dict[str, Any]:
    limit = max(1, min(page.limit, 500))
    offset = max(0, page.offset)
    total = qs.count()
    items = [serialize(obj) for obj in qs[offset:offset + limit]]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
```

- [ ] **Step 2 — Idempotency helper** — port `api/idempotency.py:1-72` to Django ORM:

```python
# server_django/foxrunner/idempotency.py
from __future__ import annotations
import hashlib
import json
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from ninja.errors import HttpError

from ops.models import IdempotencyKey


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_idempotent_response(request, *, user_id: UUID, payload: Any) -> dict[str, Any] | None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    record = IdempotencyKey.objects.filter(user_id=user_id, key=key).first()
    if record is None:
        return None
    if record.request_fingerprint != _fingerprint(payload):
        raise HttpError(409, "Idempotency-Key reutilisee avec un payload different.")
    return record.response or {}


def store_idempotent_response(request, *, user_id: UUID, payload: Any, response: dict[str, Any], status_code: int = 200) -> None:
    key = request.headers.get("Idempotency-Key")
    if not key:
        return
    try:
        with transaction.atomic():
            IdempotencyKey.objects.create(
                user_id=user_id, key=key,
                request_fingerprint=_fingerprint(payload),
                response=response, status_code=status_code,
            )
    except IntegrityError:
        existing = IdempotencyKey.objects.filter(user_id=user_id, key=key).first()
        if existing is not None and existing.request_fingerprint != _fingerprint(payload):
            raise HttpError(409, "Idempotency-Key reutilisee avec un payload different.")
```

- [ ] **Step 3 — Permission helpers** — extend `accounts/permissions.py` with:

```python
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError
from accounts.models import User


def resolve_user(user_id_str: str):
    """Accepts UUID or email. Returns User or raises 404."""
    try:
        return User.objects.get(id=user_id_str)
    except (User.DoesNotExist, ValueError):
        try:
            return User.objects.get(email=user_id_str)
        except User.DoesNotExist:
            raise HttpError(404, "Utilisateur introuvable.")


def require_self_or_superuser(actor, target):
    if actor.is_superuser or actor.id == target.id:
        return
    raise HttpError(403, "Acces interdit a cet utilisateur.")
```

- [ ] **Step 4 — Catalog service skeleton** — port `api/catalog.py::save_scenario_definition` with a `threading.Lock` keyed on `scenario_id`:

```python
# server_django/catalog/services.py
from __future__ import annotations
import threading
from collections import defaultdict
from typing import Any

from django.db import transaction

from catalog.models import Scenario, ScenarioShare, Slot

_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)
_LOCKS_GUARD = threading.Lock()


def _lock_for(scenario_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS[scenario_id]


@transaction.atomic
def save_scenario_definition(scenario: Scenario, definition: dict[str, Any], description: str | None = None) -> Scenario:
    with _lock_for(scenario.scenario_id):
        scenario.definition = definition
        if description is not None:
            scenario.description = description
        scenario.save()
        # Mirror api/catalog.py JSON write here in phase 4.4 (planning) when we wire scenarios_file.
        return scenario
```

- [ ] **Step 5 — Tests for plumbing**

Cover: pagination clamps `limit ≤ 500`; idempotency returns the stored response on replay and 409 on fingerprint mismatch; `resolve_user` works for both UUID and email and raises 404 otherwise. Drop them in `server_django/foxrunner/tests/test_plumbing.py` (create `__init__.py` next to it).

- [ ] **Step 6 — Run tests → expect PASS**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test foxrunner -v 2
```

- [ ] **Step 7 — Commit**

```bash
git add server_django/foxrunner/pagination.py server_django/foxrunner/idempotency.py server_django/accounts/permissions.py server_django/catalog/services.py server_django/foxrunner/tests/
git commit -m "feat(django): pagination, idempotency, user-resolver, scenario-write lock helpers"
```

### Task 4.2 — Scenarios CRUD (`POST /scenarios`, `PATCH/DELETE /scenarios/{id}`, `POST /scenarios/{id}/duplicate`)

**Files:**
- Modify: `server_django/catalog/api.py`, `server_django/catalog/services.py`
- Schemas: `server_django/catalog/schemas.py` (create)
- Test: `server_django/catalog/tests/test_scenarios.py` (create)

Port behaviour from `api/routers/catalog.py:1-140` (scenario CRUD section). Honour:
- `Idempotency-Key` on POST.
- Owner-only writes (after phase 5 normalization, comparison is `scenario.owner_user_id == request.auth.id`; until then, also accept email match — see `api/permissions.py::_is_scenario_owner` for the full predicate).
- Duplicate: deep-copy `definition`, regenerate `scenario_id` from query param, respect uniqueness.
- All response fields match `api/serializers.py::serialize_scenario`.

Run TDD per endpoint (failing test → implement → green → commit). Group commits per verb:

```bash
git commit -m "feat(django): POST /api/v1/scenarios"
git commit -m "feat(django): PATCH /api/v1/scenarios/{id}"
git commit -m "feat(django): DELETE /api/v1/scenarios/{id}"
git commit -m "feat(django): POST /api/v1/scenarios/{id}/duplicate"
```

### Task 4.3 — Scenario shares (`GET/POST/DELETE /scenarios/{id}/shares`)

Same TDD pattern. Reference: `api/routers/catalog.py:140-220`, `api/services/scenarios.py::list_shares/add_share/remove_share`.

Commit:
```bash
git commit -m "feat(django): scenarios/{id}/shares CRUD"
```

### Task 4.4 — Slots CRUD (`GET/POST/PATCH/DELETE /slots[/{id}]`)

Reference: `api/routers/catalog.py:220-290`, `api/services/slots.py`. Idempotency on `POST /slots`.

Commit per verb:
```bash
git commit -m "feat(django): /api/v1/slots GET (paginated, optional ?scenario_id)"
git commit -m "feat(django): /api/v1/slots POST + Idempotency-Key"
git commit -m "feat(django): /api/v1/slots/{id} GET PATCH DELETE"
```

### Task 4.5 — Step-collections (`GET/POST/PUT/DELETE /users/{user_id}/scenarios/{scenario_id}/step-collections/...`)

Reference: `api/routers/catalog.py:290-380`, `api/services/steps.py` (107 LoC). Reads return raw arrays, writes return the updated collection. Validate against the JSON-schema in `schemas/` via the existing loader (see `scenarios/loader.py`).

Commit per group:
```bash
git commit -m "feat(django): step-collections list + read endpoints"
git commit -m "feat(django): step-collections POST/PUT/DELETE"
```

### Task 4.6 — User-scoped catalog views (`GET /users/{user_id}/scenarios[/{id}]`, `/users/{id}/scenario-data`)

Reference: `api/routers/catalog.py:380-422`, `api/services/scenarios.py::list_scenarios_for_user/get_scenario_for_user`. Each scenario response has `role: "owner" | "shared"` and `writable: bool`.

Commit:
```bash
git commit -m "feat(django): /users/{id}/scenarios list + detail (role/writable)"
git commit -m "feat(django): /users/{id}/scenario-data aggregate"
```

### Task 4.7 — Planning (`GET /users/{id}/plan`, `GET /users/{id}/slots`, `POST /users/{id}/scenarios/{sid}/run`, `POST /users/{id}/run-next`)

Reference: `api/routers/catalog.py` (planning section near the end), `scheduler/service.py::SchedulerService` — keep using the existing helper; just adapt the session/DB layer. The user's `timezone_name` controls slot windows.

Commit per endpoint:
```bash
git commit -m "feat(django): /users/{id}/plan + /users/{id}/slots"
git commit -m "feat(django): /users/{id}/scenarios/{sid}/run (queues a Celery job)"
git commit -m "feat(django): /users/{id}/run-next"
```

### Task 4.8 — History (`GET /users/{id}/history`)

Reference: `api/services/users.py` + `api/history.py`. The endpoint synchronizes the legacy JSONL file before reading the DB (this is documented behaviour and must be preserved).

```bash
git commit -m "feat(django): /users/{id}/history (DB + legacy JSONL sync)"
```

### Phase 4 verification

- [ ] **Run all catalog + accounts tests**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py test catalog accounts -v 2
```
Expected: all green.

- [ ] **Cross-check OpenAPI**: `curl -s http://127.0.0.1:8001/api/v1/openapi.json | .\.venv\Scripts\python.exe -c "import json,sys; spec=json.load(sys.stdin); paths=sorted(spec['paths']); print('\n'.join(paths))"` — every `/scenarios*`, `/slots*`, `/users/{user_id}/*` path from `docs/API.md` must appear.

---

## Phase 5 — UUID normalization data migration (~1 h)

Replicate `migrations/versions/20260422_0012_normalize_owner_user_id.py` as a Django data migration, then **AlterField** the three `*_user_id` columns from `CharField(max_length=320)` to `UUIDField` (post-data-migration values are guaranteed to parse as UUID), then promote them to ForeignKey on User and delete the email-fallback ownership code. The schema migration order matters: data normalization MUST happen before the type change, or `AlterField` will fail on rows whose column still holds an email.

### Task 5.1 — Data migration

**Files:**
- Create: `server_django/catalog/migrations/0002_normalize_owner_user_id.py`
- Create: `server_django/ops/migrations/0002_normalize_actor_user_id.py`

- [ ] **Step 1 — Generate empty migrations**

```bash
cd server_django
..\.venv\Scripts\python.exe manage.py makemigrations catalog --empty -n normalize_owner_user_id
..\.venv\Scripts\python.exe manage.py makemigrations ops --empty -n normalize_actor_user_id
```

- [ ] **Step 2 — Implement `forward()`** (no `reverse_func` — the original Alembic migration has `pass` for downgrade). Use a `RunPython` operation that walks `User.objects.all()` and rewrites rows where the string column matches `email`:

```python
from django.db import migrations


def normalize_catalog(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Scenario = apps.get_model("catalog", "Scenario")
    ScenarioShare = apps.get_model("catalog", "ScenarioShare")
    for u in User.objects.all():
        Scenario.objects.filter(owner_user_id=u.email).update(owner_user_id=str(u.id))
        ScenarioShare.objects.filter(user_id=u.email).update(user_id=str(u.id))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("accounts", "0001_initial"),
    ]
    operations = [migrations.RunPython(normalize_catalog, reverse_code=migrations.RunPython.noop)]
```

Symmetric for `ops` (only `AuditEntry.actor_user_id`).

- [ ] **Step 3 — Test the migration**

Add `server_django/catalog/tests/test_migration.py`:

```python
from django.core.management import call_command
from django.test import TestCase
from accounts.models import User
from catalog.models import Scenario, ScenarioShare


class NormalizeOwnerMigrationTest(TestCase):
    """Ensure the data migration runs idempotently and rewrites email→UUID."""
    def test_idempotent_replay(self):
        u = User.objects.create_user(email="o@x.com", password="x")
        Scenario.objects.create(scenario_id="legacy", owner_user_id=u.email)
        # Re-run migrate (idempotent)
        call_command("migrate", "catalog", verbosity=0)
        self.assertEqual(Scenario.objects.get(scenario_id="legacy").owner_user_id, str(u.id))
```

- [ ] **Step 4 — Run tests → expect PASS**

```bash
..\.venv\Scripts\python.exe manage.py test catalog.tests.test_migration -v 2
```

- [ ] **Step 5 — Commit**

```bash
git add server_django/catalog/migrations/0002* server_django/ops/migrations/0002* server_django/catalog/tests/test_migration.py
git commit -m "feat(django): data migration normalizing owner_user_id email→UUID"
```

### Task 5.2 — Promote UUIDFields to ForeignKey + drop the email fallback

**Files:** `server_django/catalog/models.py`, `server_django/ops/models.py`, `server_django/catalog/permissions.py`, `server_django/catalog/services.py`, all callers of those.

- [ ] **Step 1 — Modify models** — change `owner_user_id = UUIDField(...)` → `owner = ForeignKey(User, on_delete=PROTECT, db_column="owner_user_id")`. Same for `ScenarioShare.user_id` (CASCADE) and `AuditEntry.actor_user_id` (SET_NULL, nullable already).

- [ ] **Step 2 — Generate migration**

```bash
..\.venv\Scripts\python.exe manage.py makemigrations catalog ops -n promote_user_fks
```

- [ ] **Step 3 — Drop ownership fallback** — delete `_owner_candidates`-equivalent code (it doesn't exist in the Django codebase yet — just confirm `catalog/permissions.py::require_scenario_owner` only does `scenario.owner_id == user.id`, which is already the case).

- [ ] **Step 4 — Update every queryset and serializer** that referenced `owner_user_id` to use `owner_id` (or `owner` for related-object access). Run grep to find them all:

```bash
grep -rn "owner_user_id" server_django
grep -rn "actor_user_id" server_django
```

- [ ] **Step 5 — Run all tests → expect PASS**

```bash
..\.venv\Scripts\python.exe manage.py test
```

- [ ] **Step 6 — Commit**

```bash
git add -A
git commit -m "refactor(django): promote owner/actor user fields to FK, drop email fallback"
```

---

## Phase 6 — Jobs + Celery tasks (~3 h)

### Task 6.1 — Jobs endpoints

**Files:**
- Modify: `server_django/ops/api.py`
- Service: `server_django/ops/services.py`
- Test: `server_django/ops/tests/test_jobs_api.py`

Endpoints to port (reference `api/routers/jobs.py`):

```
GET /api/v1/jobs                          -> paginated
GET /api/v1/jobs/{job_id}                  -> detail
GET /api/v1/jobs/{job_id}/events           -> raw array
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
POST /api/v1/users/{user_id}/scenarios/{sid}/jobs?dry_run=true   (idempotent)
GET  /api/v1/users/{user_id}/scenarios/{sid}/jobs                (filter helper)
```

TDD per endpoint, commit per verb:
```bash
git commit -m "feat(django): GET /api/v1/jobs (paginated)"
git commit -m "feat(django): GET /api/v1/jobs/{id} + events"
git commit -m "feat(django): POST /api/v1/jobs/{id}/cancel + /retry"
git commit -m "feat(django): POST /api/v1/users/{id}/scenarios/{sid}/jobs (idempotent)"
```

### Task 6.2 — Celery tasks

**Files:** `server_django/ops/tasks.py` (replace stubs).

Replicate `api/tasks.py` but with sync Django ORM. Three tasks:

- `run_scenario_job(job_id, scenario_id, dry_run)` — drives the Selenium runner via `scheduler.service.SchedulerService`. Update `Job.status`, `started_at`, `finished_at`, `result`, `exit_code`. Append `JobEvent` rows along the way.
- `renew_graph_subscriptions_task()` — periodic; per-subscription error log via `logger.error(...)`.
- `prune_retention_task()` — periodic; reads `RETENTION_*` env vars.

- [ ] **Step 1 — Failing test for `run_scenario_job`** (mock `SchedulerService.run_specific_scenario`)

```python
from unittest.mock import patch
from django.test import TestCase
from accounts.models import User
from catalog.models import Scenario
from ops.models import Job
from ops.tasks import run_scenario_job


class RunScenarioJobTest(TestCase):
    def test_marks_job_done(self):
        u = User.objects.create_user(email="o@x.com", password="x")
        Scenario.objects.create(scenario_id="s1", owner=u, definition={})
        job = Job.objects.create(job_id="j1", user_id=u.id, kind="run", target_id="s1", status="queued", dry_run=True)
        with patch("scheduler.service.SchedulerService.run_specific_scenario", return_value={"status": "ok"}):
            run_scenario_job("j1", "s1", True)
        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.exit_code, 0)
        self.assertIsNotNone(job.finished_at)
```

- [ ] **Step 2 — Implement** the three tasks (port verbatim from `api/tasks.py`, swap async session for `Job.objects.get(...)` + `.save()`).

- [ ] **Step 3 — Run tests → expect PASS**

- [ ] **Step 4 — Commit**

```bash
git add server_django/ops/tasks.py server_django/ops/services.py server_django/ops/tests/
git commit -m "feat(django): port Celery tasks to sync Django ORM (run_scenario_job, graph renewal, retention)"
```

### Task 6.3 — History endpoint relocation check

The history endpoint already lives under `/users/{id}/history` (phase 4.8). Confirm it reads from `ops.ExecutionHistory`. If it still proxies to the FastAPI `api/history.py`, port it now.

```bash
git commit -m "refactor(django): /users/{id}/history reads ops.ExecutionHistory directly"
```

---

## Phase 7 — Admin + monitoring + audit + settings + artifacts (~3 h)

Reference: `api/routers/admin.py` (159 LoC), `api/routers/artifacts.py` (59 LoC), `api/services/admin.py` (184 LoC), `api/monitoring.py`.

### Endpoints to port

```
GET    /api/v1/admin/users                        (paginated)
PATCH  /api/v1/admin/users/{target_user_id}
GET    /api/v1/admin/config-checks
GET    /api/v1/admin/db-stats
GET    /api/v1/admin/export                       (returns response model — see CHANGELOG entry "redact_text + admin/export response model")
POST   /api/v1/admin/import?dry_run=true
DELETE /api/v1/admin/retention?jobs_days=30&audit_days=180&graph_notifications_days=30
GET    /api/v1/admin/settings                     (paginated)
PUT    /api/v1/admin/settings/{key}
DELETE /api/v1/admin/settings/{key}
GET    /api/v1/audit
GET    /api/v1/artifacts
GET    /api/v1/artifacts/{kind}/{name}
DELETE /api/v1/artifacts?older_than_days=30
GET    /api/v1/monitoring/summary
GET    /api/v1/metrics                            (Prometheus text/plain)
```

All require `require_superuser(request.auth)` at the top.

TDD + commit per group:

```bash
git commit -m "feat(django): /admin/users CRUD + retention"
git commit -m "feat(django): /admin/config-checks + /db-stats"
git commit -m "feat(django): /admin/export + /admin/import"
git commit -m "feat(django): /admin/settings CRUD"
git commit -m "feat(django): /audit endpoint"
git commit -m "feat(django): /artifacts CRUD"
git commit -m "feat(django): /monitoring/summary + /metrics"
```

### Phase 7 verification

- [ ] Coverage check after this phase

```bash
cd server_django
..\.venv\Scripts\python.exe -m coverage run --source=accounts,catalog,ops,foxrunner manage.py test
..\.venv\Scripts\python.exe -m coverage report
```
Expected: ≥ 84 %. If lower, add tests before moving on.

---

## Phase 8 — Microsoft Graph endpoints (~2 h)

Reference: `api/routers/graph.py` (106 LoC), `api/services/graph.py` (83 LoC), `api/graph.py`.

### Endpoints

```
POST   /api/v1/graph/subscriptions
GET    /api/v1/graph/subscriptions          (paginated)
PATCH  /api/v1/graph/subscriptions/{id}
DELETE /api/v1/graph/subscriptions/{id}
GET    /api/v1/graph/notifications
POST   /api/v1/graph/webhook                (NO auth — uses clientState validation)
POST   /api/v1/graph/lifecycle              (NO auth — same)
```

### Critical: clientState validation

Port `api/graph.py::_validate_client_state` **line by line** (it is the security primitive). Validation order:
1. Per-subscription `client_state` from the `GraphSubscription` row — strict equality.
2. Global `GRAPH_WEBHOOK_CLIENT_STATE` env var — fallback.
3. In production both empty → reject (HTTP 401).

The webhook also handles Microsoft's `validationToken` echo (return the token as `text/plain` with HTTP 200 when present in the query string).

TDD:
- [ ] Test 1 — webhook returns 200 with the validationToken when query param present.
- [ ] Test 2 — webhook with valid clientState stores notifications and returns 202.
- [ ] Test 3 — webhook with mismatched clientState returns 401.
- [ ] Test 4 — webhook in production with both clientState empty returns 401.

Commits:
```bash
git commit -m "feat(django): graph subscriptions CRUD"
git commit -m "feat(django): graph webhook + lifecycle endpoints (clientState validation)"
git commit -m "feat(django): graph notifications listing"
```

---

## Phase 9 — Transverse middleware + scripts (~3 h)

### Task 9.1 — Rate limiting (Redis sliding window)

**Files:** `server_django/foxrunner/rate_limit.py` (create), wire in `MIDDLEWARE`.

Port `api/rate_limit.py:1-97` to a Django middleware. Key target paths via `_is_limited_path`. Use `django_redis.get_redis_connection("default")` for the client. Falls back to in-process dict when Redis is unreachable, exactly like the FastAPI version. Returns the `{"code":"rate_limited", ...}` JSON envelope.

- [ ] **Tests** — replicate `tests/test_hardening_edges.py` rate-limit cases against the new middleware.
- [ ] **Commit:** `git commit -m "feat(django): rate-limit middleware (Redis sliding window + in-process fallback)"`

### Task 9.2 — Payload size limit

`DATA_UPLOAD_MAX_MEMORY_SIZE` (already set in settings) covers `Content-Length`-bounded requests. For the chunked case, write `server_django/foxrunner/payload_limit.py` mirroring `api/payload_limit.py` (ASGI-style middleware that wraps `receive`). Wire it into `MIDDLEWARE`.

- [ ] **Tests** — port the chunked case from `tests/test_hardening_edges.py`.
- [ ] **Commit:** `git commit -m "feat(django): payload-limit middleware (chunked-aware)"`

### Task 9.3 — Audit OpenAPI export

**Files:** `server_django/scripts/export_openapi.py` (create or port from `scripts/export_openapi.py`).

Run-time spec is at `/api/v1/openapi.json`; the export script writes it to a file path so CI can diff.

```python
# server_django/scripts/export_openapi.py
import json, os, sys, django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "foxrunner.settings")
django.setup()
from foxrunner.api import api
spec = api.get_openapi_schema()
print(json.dumps(spec, indent=2, sort_keys=True))
```

Update `Makefile` `openapi` target:
```make
openapi:
	./.venv/Scripts/python.exe server_django/scripts/export_openapi.py > openapi.json
```

- [ ] **Run** `make openapi` and inspect the diff against the existing `openapi.json`. Names of schemas may differ (Pydantic-vs-Ninja). Paths and verbs must match. Document any unavoidable differences in `docs/MIGRATION_NOTES.md`.
- [ ] **Commit:** `git commit -m "chore(django): export_openapi script + Makefile target"`

### Task 9.4 — env-var rename

Adapt `scripts/check_env_example.py` to the new var names (`DJANGO_SECRET_KEY`, `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`). Update `.env.example` accordingly. Drop `API_CREATE_TABLES_ON_STARTUP` and `API_ENABLE_LEGACY_ROUTES`.

- [ ] **Commit:** `git commit -m "chore(django): rename env vars (DATABASE_URL, DJANGO_SECRET_KEY, CORS_ALLOWED_ORIGINS)"`

### Task 9.5 — Security headers smoke test

The scaffold sets `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY="no-referrer"`, `X_FRAME_OPTIONS="DENY"`. Add an integration test that hits `/api/v1/health` and asserts all three headers are present.

```bash
git commit -m "test(django): security headers regression test"
```

---

## Phase 10 — Tests (~4 h)

### Task 10.1 — Rewrite `tests/test_api.py`

The existing FastAPI `tests/test_api.py` (1 341 LoC) tests every endpoint via `httpx.AsyncClient`. Rewrite as `tests/test_api_django.py` (or just replace `test_api.py` content) using `django.test.Client` or `ninja.testing.TestClient`.

Strategy:
1. Read each test method in `tests/test_api.py`.
2. Find the equivalent Django endpoint.
3. Port the test, replacing the client setup and the auth bootstrap.
4. Keep assertions identical.

Group commits by router:
```bash
git commit -m "test(django): port catalog API tests"
git commit -m "test(django): port jobs API tests"
git commit -m "test(django): port admin/audit/settings API tests"
git commit -m "test(django): port graph webhook tests"
```

### Task 10.2 — Port HTTP-layer tests in `test_hardening_edges.py`

Only the parts that actually hit FastAPI (`payload_limit`, `rate_limit` integration tests). The rest (`Graph clientState`, ownership, DST, `HistoryStore`, `ProcessLock`, `_run_with_timeout`) are framework-agnostic and stay as-is.

```bash
git commit -m "test(django): port hardening HTTP tests to Ninja TestClient"
```

### Task 10.3 — Coverage check

```bash
cd server_django
..\.venv\Scripts\python.exe -m coverage run --source=accounts,catalog,ops,foxrunner manage.py test
..\.venv\Scripts\python.exe -m coverage report --fail-under=84
```

If under 84 %, add targeted tests until the floor is met. Commit with:
```bash
git commit -m "test(django): cover gaps to keep coverage ≥ 84%"
```

---

## Phase 11 — Documentation (~2 h)

Update each of these to mention Django/Ninja and the new env vars:

- [ ] `CLAUDE.md` — replace FastAPI references with Django; update commands (`manage.py test`, `manage.py migrate`, `manage.py runserver`).
- [ ] `docs/ARCHITECTURE.md` — replace ADR 001 reference with ADR 007 + brief description of the apps.
- [ ] `docs/API.md` — update auth section (djoser endpoints + Ninja wrappers); other endpoints unchanged.
- [ ] `docs/ENVIRONMENT.md` — rename env vars, document `DJANGO_SECRET_KEY`, `DATABASE_URL`, `CORS_ALLOWED_ORIGINS`.
- [ ] `docs/OPERATIONS.md` — replace `uvicorn` with `gunicorn`; replace `alembic upgrade` with `manage.py migrate`; mention `manage.py createsuperuser`.
- [ ] `docs/PRODUCTION.md` — same replacements; ensure the AUTH_SECRET≥32 rule is mentioned for `DJANGO_SECRET_KEY`.
- [ ] `docs/FIRST_DEPLOYMENT.md` — replace bootstrap script with `manage.py bootstrap_admin --email …` + `BOOTSTRAP_PASSWORD=…`.
- [ ] `docs/ADR.md` — add ADR 007:

```markdown
## ADR 007: Switch to Django + Ninja

Decision: replace the FastAPI backend with Django + Django Ninja, retaining the same `/api/v1` contract.

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
```

- [ ] `CHANGELOG.md` — add a new section above `Unreleased`:

```markdown
## 0.2.0 — Django backend

### Backend

- Replaced the FastAPI backend with Django 5 + Django Ninja under the same `/api/v1` contract (ADR 007).
- Auth via djoser + simple-jwt; `POST /api/v1/auth/jwt/login` continues to accept form-urlencoded credentials and returns `{access_token, token_type}`.
- `owner_user_id` columns normalized to UUID and promoted to ForeignKey; the FastAPI email/UUID dual-match is gone.
- Celery tasks rewritten on top of the sync Django ORM.
- New env var names: `DATABASE_URL`, `DJANGO_SECRET_KEY`, `CORS_ALLOWED_ORIGINS`. Removed: `API_CREATE_TABLES_ON_STARTUP`, `API_ENABLE_LEGACY_ROUTES`.

### Tooling

- `manage.py` replaces `uvicorn`/`alembic` invocations across docs and CI.
- Tests run via `python manage.py test --parallel`; coverage floor stays at 84 %.
```

- [ ] `docs/MIGRATION_NOTES.md` (create) — list any subtle FastAPI→Django behavioural differences encountered during the migration.

Commit:
```bash
git commit -m "docs: ADR 007, CHANGELOG, ARCHITECTURE/API/ENVIRONMENT/OPERATIONS for Django backend"
```

---

## Phase 12 — CI + Docker (~1 h)

### Task 12.1 — GitHub Actions

**Files:** `.github/workflows/ci.yml`.

Replace test command:
```yaml
- name: Test
  run: |
    cd server_django
    python manage.py migrate --run-syncdb
    python manage.py test --parallel
```

Replace migration cycle:
```yaml
- name: Migration cycle
  run: |
    cd server_django
    python manage.py migrate
    python manage.py migrate accounts zero
    python manage.py migrate
```

Update `openapi-check`:
```yaml
- name: OpenAPI check
  run: |
    python server_django/scripts/export_openapi.py > /tmp/openapi.live.json
    diff openapi.json /tmp/openapi.live.json
```

### Task 12.2 — Dockerfile

Replace the CMD line:
```dockerfile
CMD ["gunicorn", "foxrunner.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
```

The healthcheck stays on `/api/v1/health`. Keep the multi-stage build and non-root `app` user.

### Task 12.3 — docker-compose

The `api` service runs `python manage.py migrate && gunicorn …`. The `worker` service runs `celery -A foxrunner worker --pool=solo`. Beat: `celery -A foxrunner beat`.

### Task 12.4 — Makefile

Replace targets:
```make
run-api:
	./.venv/Scripts/python.exe server_django/manage.py runserver 0.0.0.0:8000

migrate:
	./.venv/Scripts/python.exe server_django/manage.py migrate

migrate-test:
	./.venv/Scripts/python.exe server_django/manage.py migrate
	./.venv/Scripts/python.exe server_django/manage.py migrate accounts zero
	./.venv/Scripts/python.exe server_django/manage.py migrate

test:
	./.venv/Scripts/python.exe server_django/manage.py test --parallel
```

### Task 12.5 — Lock files

```bash
make relock
```

Commit (one big commit is fine for infra):
```bash
git commit -m "chore(ci/docker): wire Django backend into CI, Dockerfile, compose, Makefile"
```

---

## Phase 13 — Final swap (~30 min)

**Run only after all previous phases pass and the user has reviewed the diff.**

### Task 13.1 — Delete the FastAPI tree

- [ ] **Step 1 — Confirm Django tests pass + lint clean**

```bash
cd server_django && ..\.venv\Scripts\python.exe manage.py test --parallel && cd ..
.\.venv\Scripts\ruff.exe check .
```

- [ ] **Step 2 — Delete FastAPI**

```bash
rm -rf api/ migrations/ alembic.ini
```

- [ ] **Step 3 — Move `server_django/*` to repo root**

```bash
git mv server_django/* .
git mv server_django/.* . 2>/dev/null || true
rmdir server_django
```

- [ ] **Step 4 — Fix any imports that referenced `api.*`** in the non-web modules:

```bash
grep -rn "from api" app/ scheduler/ scenarios/ operations/ network/ state/ cli/ scripts/ tests/ 2>&1 | grep -v "test_api\|test_hardening"
```

Each match → either rewrite to use the Django equivalent (e.g. `from foxrunner.exception_handlers import …`, `from accounts.models import User`), or move the helper function to a neutral location (e.g. `from app.redaction import redact_text` if you keep the helper in `api/redaction.py`, move it under `app/` first).

- [ ] **Step 5 — Update `pyproject.toml` coverage source** — drop the temporary entries that referenced `api`, `server_django/...`. Final form:

```toml
[tool.coverage.run]
source = ["accounts", "catalog", "ops", "foxrunner", "app", "cli", "network", "operations", "scenarios", "scheduler", "state"]
```

- [ ] **Step 6 — Final test + lint pass**

```bash
.\.venv\Scripts\python.exe manage.py test --parallel
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m coverage run --source=accounts,catalog,ops,foxrunner,app,cli,network,operations,scenarios,scheduler,state manage.py test
.\.venv\Scripts\python.exe -m coverage report --fail-under=84
```

- [ ] **Step 7 — Commit**

```bash
git add -A
git commit -m "refactor(django): swap — delete FastAPI tree, promote server_django to root"
```

### Task 13.2 — Hand-off to the user

- [ ] Summarize the branch state in the PR/handoff message:
  - branch `refactor/django` ahead of `main` by N commits.
  - tests: X passing, coverage Y %.
  - all docs updated; ADR 007 added.
  - `make ci` clean.
  - new `openapi.json` committed.
  - bootstrap admin: `python manage.py bootstrap_admin --email admin@local` with `BOOTSTRAP_PASSWORD` in env.

Do **not** push or merge. The user reviews and merges.

---

## Frontend hand-off note (to send when phase 13 is done)

Send the frontend agent these answers up front, ahead of the openapi diff:

| Frontend question | Answer |
|---|---|
| Auth stack | djoser + djangorestframework-simplejwt (JWT). Frontend's existing `POST /api/v1/auth/jwt/login` form-urlencoded call remains supported by the Ninja wrapper. |
| `/api/v1` prefix | Kept. |
| Endpoint names | Identical (`/scenarios`, `/slots`, `/users/{id}/history`, etc.). |
| Pagination | Same `{items, total, limit, offset}` envelope, same `?limit=&offset=` query params. **Not** DRF's `count/next/previous/results`. |
| Errors | Same `{code, message, details}` shape. |
| Idempotency-Key | Honoured on `POST /scenarios`, `POST /slots`, `POST /users/{id}/scenarios/{sid}/jobs`. |
| X-Request-ID | Echoed on every response. |
| Timestamps | UTC ISO 8601 with `Z`. |
| Test admin | `python manage.py bootstrap_admin --email admin@local` with `BOOTSTRAP_PASSWORD` in env. |
| Port | `127.0.0.1:8000` (same as FastAPI). |
| CORS | `http://localhost:4200` by default; configurable via `CORS_ALLOWED_ORIGINS`. |

---

## Self-review checklist (run after writing the plan)

- [ ] **Spec coverage:** every phase from the brief (1–13) has a section. Every model in the mapping table is in phase 2 or phase 5. Every endpoint in `docs/API.md` is referenced in phases 3, 4, 6, 7, 8.
- [ ] **No placeholders:** no "TBD", no "fill in", no "implement later" without a concrete sub-task.
- [ ] **Type consistency:** `owner_user_id` (phases 2/4) → `owner_id` (after phase 5); same for `actor_user_id`. Reviewed.
- [ ] **Frontend contract preserved:** the 6 conventions block at the top of this plan is wired into Phase 3 (login wrapper), Phase 4.1 (pagination + idempotency helpers), Phase 9 (rate limit + payload limit + headers), and verified in Phase 10.
- [ ] **CLAUDE.md rules respected:** Windows venv, ruff-only, ≥84 % coverage, `make migrate-test` cycle (replicated as Django zero/up cycle in 12.4).

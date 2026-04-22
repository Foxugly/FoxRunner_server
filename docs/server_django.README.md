# FoxRunner Django backend (migration in progress)

This directory contains the Django + Django Ninja rewrite of the FastAPI
backend in `../api/`. It ships alongside the FastAPI project during the
migration so both can run and be diffed side-by-side. At the end of the
migration (phase 13 of the handoff brief) the FastAPI tree disappears and
this directory's contents are promoted to the repository root.

## Layout

```
server_django/
  manage.py
  foxrunner/          # project config (settings, urls, celery, ninja api, middleware)
  accounts/           # User + auth wrappers on top of djoser
  catalog/            # Scenario, Slot, ScenarioShare, step collections
  ops/                # Job, JobEvent, History, Audit, Graph, Settings, Artifacts, Monitoring
  requirements.txt    # Django-specific deps to merge into top-level requirements.txt at swap
```

Non-web code (`app/`, `scheduler/`, `scenarios/`, `operations/`, `network/`,
`state/`, `cli/`) lives **one level up** and is imported as-is — `manage.py`
adds the repository root to `sys.path` so those modules remain first-class
imports. Do not duplicate or rewrite them here.

## Quick start

```powershell
# From repo root
.\.venv\Scripts\python.exe -m pip install -r server_django\requirements.txt
cd server_django
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py createsuperuser
..\.venv\Scripts\python.exe manage.py runserver 8001
```

Port 8001 keeps the FastAPI dev server free on 8000 during the transition.

## Contract

- `/api/v1/auth/...` — djoser (register, JWT create/refresh, password reset).
- `/api/v1/auth/jwt/login` and `/auth/jwt/logout` — Ninja wrappers that match
  the existing FastAPI contract the Angular frontend expects.
- `/api/v1/...` — Ninja routers (scenarios, slots, jobs, history, admin,
  graph, monitoring, artifacts).
- `/admin/` — Django admin (exposed for superusers only, CSRF-protected,
  uses session auth independently from the JWT flow).

See `../docs/API.md` for the full endpoint inventory — the target is
functional equivalence with the FastAPI implementation.

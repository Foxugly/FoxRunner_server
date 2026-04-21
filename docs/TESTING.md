# Testing

## Local Checks

Run unit and API tests:

```powershell
.\.venv\Scripts\python.exe -m unittest
```

Run lint:

```powershell
.\.venv\Scripts\ruff.exe check .
```

Run coverage with the configured minimum:

```powershell
.\.venv\Scripts\python.exe -m coverage run -m unittest
.\.venv\Scripts\python.exe -m coverage report --fail-under=84
```

Run the migration cycle:

```powershell
$env:AUTH_DATABASE_URL='sqlite+aiosqlite:///./.runtime/ci-validation.db'
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe downgrade base
.\.venv\Scripts\alembic.exe upgrade head
```

Check OpenAPI:

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py
.\.venv\Scripts\python.exe scripts\check_openapi.py
```

Check docs:

```powershell
.\.venv\Scripts\python.exe scripts\check_docs.py
```

Run a lightweight project audit:

```powershell
.\.venv\Scripts\python.exe scripts\audit_project.py
```

## Windows CI Script

Without `make`, use:

```powershell
.\scripts\ci.ps1
```

## Cleanup

```powershell
.\.venv\Scripts\python.exe scripts\clean_runtime_artifacts.py
```

## Smoke Test

With the API running:

```powershell
$env:SMOKE_BASE_URL='http://127.0.0.1:8000'
.\.venv\Scripts\python.exe scripts\smoke_api.py
```

Set `SMOKE_EMAIL` and `SMOKE_PASSWORD` to include login, `/users/me`, and `/config/client`.

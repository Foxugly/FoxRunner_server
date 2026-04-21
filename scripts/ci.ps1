$ErrorActionPreference = "Stop"
$env:APP_ENV = "test"
$env:API_LOG_HTTP_ENABLED = "false"
$env:APP_LOG_CONSOLE_ENABLED = "false"

.\.venv\Scripts\python.exe -m compileall api app cli migrations network operations scenarios scheduler scripts state tests
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m coverage run -m unittest
.\.venv\Scripts\python.exe -m coverage report --fail-under=84
$env:AUTH_DATABASE_URL = "sqlite+aiosqlite:///./.runtime/ci-validation.db"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe downgrade base
.\.venv\Scripts\alembic.exe upgrade head
Remove-Item .runtime\ci-validation.db -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe scripts\export_openapi.py
.\.venv\Scripts\python.exe scripts\check_openapi.py
.\.venv\Scripts\python.exe scripts\check_docs.py
.\.venv\Scripts\python.exe scripts\check_env_example.py

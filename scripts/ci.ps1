$ErrorActionPreference = "Stop"
$env:APP_ENV = "test"
$env:API_LOG_HTTP_ENABLED = "false"
$env:APP_LOG_CONSOLE_ENABLED = "false"

.\.venv\Scripts\python.exe -m compileall accounts app catalog cli foxrunner network operations ops scenarios scheduler scripts state tests
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m coverage run -m unittest
.\.venv\Scripts\python.exe -m coverage report --fail-under=84

$env:DATABASE_URL = "sqlite:///./.runtime/django-ci.db"
$env:DJANGO_SECRET_KEY = "change-me-before-production-32-bytes-minimum-ci"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe -m coverage run --source=accounts,catalog,ops,foxrunner manage.py test catalog ops accounts foxrunner --parallel=1
.\.venv\Scripts\python.exe -m coverage report --fail-under=84
Remove-Item .runtime\django-ci.db -ErrorAction SilentlyContinue

.\.venv\Scripts\python.exe scripts\export_openapi.py
.\.venv\Scripts\python.exe scripts\check_openapi.py
.\.venv\Scripts\python.exe scripts\check_docs.py
.\.venv\Scripts\python.exe scripts\check_env_example.py

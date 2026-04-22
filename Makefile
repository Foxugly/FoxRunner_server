PYTHON := ./.venv/Scripts/python.exe
ALEMBIC := ./.venv/Scripts/alembic.exe
UVICORN := ./.venv/Scripts/uvicorn.exe
CELERY := ./.venv/Scripts/celery.exe
DJANGO_MANAGE := server_django/manage.py

.PHONY: install relock lint format test test-django coverage coverage-django migrate migration migrate-test migrate-django migrate-django-test run-api run-api-django run-worker run-beat reset-local docker-up docker-down backup-sqlite restore-sqlite openapi openapi-django openapi-check docs-check audit smoke ci ci-django clean

install:
	$(PYTHON) -m pip install -r requirements-dev.lock

relock:
	$(PYTHON) -m pip install pip-tools
	$(PYTHON) -m piptools compile --quiet --strip-extras --output-file=requirements.lock requirements.txt
	$(PYTHON) -m piptools compile --quiet --strip-extras --output-file=requirements-dev.lock requirements-dev.txt

test:
	$(PYTHON) -m unittest

test-django:
	$(PYTHON) $(DJANGO_MANAGE) test catalog ops accounts foxrunner

lint:
	./.venv/Scripts/ruff.exe check .

format:
	./.venv/Scripts/ruff.exe format .

coverage:
	$(PYTHON) -m coverage run -m unittest
	$(PYTHON) -m coverage report --fail-under=84

coverage-django:
	$(PYTHON) -m coverage run --source=server_django/accounts,server_django/catalog,server_django/ops,server_django/foxrunner $(DJANGO_MANAGE) test catalog ops accounts foxrunner
	$(PYTHON) -m coverage report --fail-under=84

migrate:
	$(ALEMBIC) upgrade head

migrate-django:
	$(PYTHON) $(DJANGO_MANAGE) migrate

migrate-test:
	powershell -NoProfile -Command "$$env:AUTH_DATABASE_URL='sqlite+aiosqlite:///./.runtime/migration-test.db'; $(ALEMBIC) upgrade head; $(ALEMBIC) downgrade base; $(ALEMBIC) upgrade head; Remove-Item .runtime/migration-test.db -ErrorAction SilentlyContinue"

migrate-django-test:
	powershell -NoProfile -Command "$$env:DATABASE_URL='sqlite:///./.runtime/migration-django-test.db'; $(PYTHON) $(DJANGO_MANAGE) migrate; $(PYTHON) $(DJANGO_MANAGE) migrate accounts zero; $(PYTHON) $(DJANGO_MANAGE) migrate; Remove-Item .runtime/migration-django-test.db -ErrorAction SilentlyContinue"

migration:
	$(ALEMBIC) revision --autogenerate -m "$(m)"

run-api:
	$(UVICORN) api.main:app --reload

run-api-django:
	$(PYTHON) $(DJANGO_MANAGE) runserver 127.0.0.1:8001

run-worker:
	$(CELERY) -A api.celery_app.celery_app worker --loglevel=INFO --pool=solo

run-beat:
	$(CELERY) -A api.celery_app.celery_app beat --loglevel=INFO

reset-local:
	powershell -NoProfile -Command "Stop-Process -Name uvicorn,celery -ErrorAction SilentlyContinue; Remove-Item .runtime/users.db -ErrorAction SilentlyContinue"
	$(ALEMBIC) upgrade head

docker-up:
	docker compose up --build

docker-down:
	docker compose down

backup-sqlite:
	powershell -NoProfile -Command "New-Item -ItemType Directory -Force .runtime/backups | Out-Null; Copy-Item .runtime/users.db .runtime/backups/users-$$(Get-Date -Format yyyyMMdd-HHmmss).db"

restore-sqlite:
	powershell -NoProfile -Command "Copy-Item '$(file)' .runtime/users.db -Force"

openapi:
	$(PYTHON) scripts/export_openapi.py

openapi-django:
	$(PYTHON) server_django/scripts/export_openapi.py

openapi-check:
	$(PYTHON) scripts/export_openapi.py
	$(PYTHON) scripts/check_openapi.py

docs-check:
	$(PYTHON) scripts/check_docs.py

audit:
	$(PYTHON) scripts/audit_project.py

routes:
	$(PYTHON) scripts/list_routes.py

smoke:
	$(PYTHON) scripts/smoke_api.py

ci: lint coverage migrate-test openapi-check docs-check

# Django side of CI (runs in addition to `ci` during dual-stack)
ci-django: lint coverage-django migrate-django-test openapi-django

clean:
	$(PYTHON) scripts/clean_runtime_artifacts.py

PYTHON := ./.venv/Scripts/python.exe
CELERY := ./.venv/Scripts/celery.exe
DJANGO_MANAGE := manage.py

.PHONY: install relock lint format test test-django coverage coverage-django migrate migrate-test run-api run-worker run-beat reset-local docker-up docker-down backup-sqlite restore-sqlite openapi openapi-check docs-check audit smoke ci clean

install:
	$(PYTHON) -m pip install -r requirements-dev.lock

relock:
	$(PYTHON) -m pip install pip-tools
	$(PYTHON) -m piptools compile --quiet --strip-extras --output-file=requirements.lock requirements.txt
	$(PYTHON) -m piptools compile --quiet --strip-extras --output-file=requirements-dev.lock requirements-dev.txt

# CLI engine tests (framework-agnostic). Targets tests/ explicitly so
# unittest discover doesn't pick up the Django app test packages, which
# require a Django settings bootstrap and must run via manage.py test.
test:
	$(PYTHON) -m unittest discover -s tests

# Django backend tests.
test-django:
	$(PYTHON) $(DJANGO_MANAGE) test catalog ops accounts foxrunner

lint:
	./.venv/Scripts/ruff.exe check .

format:
	./.venv/Scripts/ruff.exe format .

coverage:
	$(PYTHON) -m coverage run --source=app,cli,network,operations,scenarios,scheduler,state -m unittest discover -s tests
	$(PYTHON) -m coverage report --fail-under=75

coverage-django:
	$(PYTHON) -m coverage run --source=accounts,catalog,ops,foxrunner $(DJANGO_MANAGE) test catalog ops accounts foxrunner
	$(PYTHON) -m coverage report --fail-under=84

migrate:
	$(PYTHON) $(DJANGO_MANAGE) migrate

migrate-test:
	powershell -NoProfile -Command "$$env:DATABASE_URL='sqlite:///./.runtime/migration-django-test.db'; $(PYTHON) $(DJANGO_MANAGE) migrate; $(PYTHON) $(DJANGO_MANAGE) migrate accounts zero; $(PYTHON) $(DJANGO_MANAGE) migrate; Remove-Item .runtime/migration-django-test.db -ErrorAction SilentlyContinue"

run-api:
	$(PYTHON) $(DJANGO_MANAGE) runserver 127.0.0.1:8000

run-worker:
	$(CELERY) -A foxrunner.celery_app worker --loglevel=INFO --pool=solo

run-beat:
	$(CELERY) -A foxrunner.celery_app beat --loglevel=INFO

reset-local:
	powershell -NoProfile -Command "Stop-Process -Name celery -ErrorAction SilentlyContinue; Remove-Item .runtime/users.db -ErrorAction SilentlyContinue"
	$(PYTHON) $(DJANGO_MANAGE) migrate

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

openapi-check:
	$(PYTHON) scripts/export_openapi.py
	$(PYTHON) scripts/check_openapi.py

docs-check:
	$(PYTHON) scripts/check_docs.py

audit:
	$(PYTHON) scripts/audit_project.py

smoke:
	$(PYTHON) scripts/smoke_api.py

ci: lint test-django coverage-django migrate-test openapi-check docs-check

clean:
	$(PYTHON) scripts/clean_runtime_artifacts.py

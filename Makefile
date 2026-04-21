PYTHON := ./.venv/Scripts/python.exe
ALEMBIC := ./.venv/Scripts/alembic.exe
UVICORN := ./.venv/Scripts/uvicorn.exe
CELERY := ./.venv/Scripts/celery.exe

.PHONY: install lint format test coverage migrate migration migrate-test run-api run-worker run-beat reset-local docker-up docker-down backup-sqlite restore-sqlite openapi openapi-check docs-check audit smoke ci clean

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m unittest

lint:
	./.venv/Scripts/ruff.exe check .

format:
	./.venv/Scripts/ruff.exe format .

coverage:
	$(PYTHON) -m coverage run -m unittest
	$(PYTHON) -m coverage report --fail-under=84

migrate:
	$(ALEMBIC) upgrade head

migrate-test:
	powershell -NoProfile -Command "$$env:AUTH_DATABASE_URL='sqlite+aiosqlite:///./.runtime/migration-test.db'; $(ALEMBIC) upgrade head; $(ALEMBIC) downgrade base; $(ALEMBIC) upgrade head; Remove-Item .runtime/migration-test.db -ErrorAction SilentlyContinue"

migration:
	$(ALEMBIC) revision --autogenerate -m "$(m)"

run-api:
	$(UVICORN) api.main:app --reload

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

clean:
	$(PYTHON) scripts/clean_runtime_artifacts.py

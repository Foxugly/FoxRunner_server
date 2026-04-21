# Roadmap

## Legacy JSON

Current state:

- DB is the API source of truth.
- Scenario and slot JSON files remain compatibility files for CLI workflows.
- API history is stored in DB and synchronized from the JSONL history file.

Target:

- CLI reads from API or DB-backed repositories.
- JSON files become import/export artifacts only.
- Legacy unprefixed API routes are disabled in production and eventually removed.

Suggested removal phases:

1. Keep `API_ENABLE_LEGACY_ROUTES=true` for local development.
2. Set `API_ENABLE_LEGACY_ROUTES=false` in staging and production.
3. Move operational scripts to `/api/v1` or documented CLI commands.
4. Remove unprefixed routes after one stable frontend release cycle.

## Frontend

Next frontend milestones:

- Generate Angular client from `openapi.json`.
- Use `/api/v1` exclusively.
- Preserve `X-Request-ID` in frontend error reports.
- Map typed page payloads to PrimeNG tables.

## Observability

Future improvements:

- OpenTelemetry tracing.
- Sentry integration.
- OpenAPI diff checks in CI.
- Lightweight pagination performance tests in CI.

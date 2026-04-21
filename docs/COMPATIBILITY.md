# Compatibility Policy

## Public API

The public API is `/api/v1`.

OpenAPI intentionally exposes only `/api/v1` paths. Angular and external clients should not depend on unprefixed routes.

## Legacy Routes

Unprefixed API routes remain enabled for:

- existing local tests;
- CLI-adjacent local tooling;
- gradual migration of scripts.

They are compatibility routes, not the public frontend contract. They may be removed after the frontend and operational scripts use `/api/v1`.

Disable them in production-like deployments with:

```env
API_ENABLE_LEGACY_ROUTES=false
```

## CLI and JSON Files

The database is the runtime source of truth for the API. JSON scenario and slot files remain compatibility inputs/outputs for the existing CLI engine and examples.

Current policy:

- Scenario/slot API mutations sync compatible JSON files.
- CLI commands remain supported as local operational tools.
- New UI features should prefer API/database flows over direct JSON editing.

## Deprecation Checklist

Before removing unprefixed routes or JSON compatibility:

- Angular uses only `/api/v1`.
- Operational scripts use `/api/v1` or documented CLI commands.
- CI covers `/api/v1` critical paths.
- A migration/export path exists for runtime data.

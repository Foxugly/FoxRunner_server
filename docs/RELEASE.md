# Release Checklist

Before a release:

1. Run `.\scripts\ci.ps1` or the equivalent CI workflow.
2. Backup the database.
3. Apply migrations in staging.
4. Smoke test auth, `/api/v1/ready`, `/api/v1/version`, scenario listing, job creation, and Graph webhook validation.
5. Regenerate and commit `openapi.json` if API contracts changed.
6. Review `.env.example` for new configuration keys.
7. Confirm no secret appears in `/ready`, `/runtime`, `/admin/config-checks`, or `/config/client`.

For production deploys, stop workers only when required by the migration. Keep a rollback backup available before applying schema changes.

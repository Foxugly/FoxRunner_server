# Security Checklist

- Configure `AUTH_SECRET` with a strong production-only value.
- Keep `.env` out of source control and rotate leaked secrets immediately.
- Use HTTPS behind a reverse proxy.
- Restrict `API_CORS_ORIGINS` to trusted frontend origins.
- Keep API rate limiting enabled and add reverse-proxy rate limiting in production.
- Require Graph webhook `clientState` and known subscriptions in production.
- Verify `/ready`, `/runtime`, `/admin/config-checks`, and `/config/client` never expose raw secrets.
- Backup the DB before migrations and before release deploys.
- Review superuser accounts regularly.
- Keep `GRAPH_CLIENT_SECRET` and mail sender permissions scoped to the minimum required.
- Validate `openapi.json` before frontend client regeneration.

# API

Interactive docs:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`

The documented public API base path is `/api/v1`. Endpoint bullets below are relative to that base path unless they already start with `/api/v1`. Legacy unprefixed routes remain enabled for compatibility but are hidden from OpenAPI.

## Auth

### Ninja wrappers (frontend contract)

- `POST /api/v1/auth/jwt/login` — form-urlencoded `username` + `password`, returns `{access_token, token_type: "bearer"}`.
- `POST /api/v1/auth/jwt/logout` — returns `{status: "ok"}`.
- `POST /api/v1/auth/forgot-password` — JSON `{email}`, returns 202 `{status: "queued"}`.
- `POST /api/v1/auth/reset-password` — JSON `{token, password}`.
- `GET /api/v1/users/me`, `PATCH /api/v1/users/me`.

### djoser (direct)

- `POST /api/v1/auth/users/` — register.
- `POST /api/v1/auth/jwt/create/` — JSON login.
- `POST /api/v1/auth/jwt/refresh/` — refresh token.
- `POST /api/v1/auth/jwt/verify/`.
- `POST /api/v1/auth/users/reset_password/`, `/reset_password_confirm/`.

Non-auth endpoints are unchanged from the FastAPI-era contract — see the rest of this doc for the catalog/jobs/admin/graph endpoints.

Protected routes require:

```text
Authorization: Bearer <token>
```

`{user_id}` can be the authenticated user's UUID or email. Superusers may access other users.

User profiles include `timezone_name` as an IANA timezone, for example `Europe/Brussels`. The default is `APP_TIMEZONE`/`Europe/Brussels` at account creation time, and users can update it with `PATCH /api/v1/users/me`.

All API timestamps are returned as UTC ISO 8601 strings, using `Z` when applicable. Frontends should convert timestamps to `timezone_name` for display.

`GET /timezones/common` returns a curated list for selectors. It is not a hard whitelist; the API accepts any valid IANA timezone.

## Runtime

- `GET /health`
- `GET /ready`
- `GET /version`
- `GET /timezones/common`
- `GET /runtime`
- `GET /config/validate`

Errors use a stable JSON shape:

```json
{
  "code": "validation_error",
  "message": "Payload ou parametres invalides.",
  "details": []
}
```

## Admin

Superuser only:

- `GET /admin/users`
- `PATCH /admin/users/{target_user_id}`
- `GET /admin/config-checks`
- `GET /admin/db-stats`
- `GET /admin/export`
- `POST /admin/import?dry_run=true`
- `DELETE /admin/retention?jobs_days=30&audit_days=180&graph_notifications_days=30`
- `GET /admin/settings?limit=100&offset=0`
- `PUT /admin/settings/{key}`
- `DELETE /admin/settings/{key}`
- `GET /audit`

Paginated list endpoints accept `limit` and `offset` and return:

```json
{
  "items": [],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

## Scenarios

- `POST /scenarios`
- `PATCH /scenarios/{scenario_id}`
- `POST /scenarios/{scenario_id}/duplicate?new_scenario_id=...`
- `DELETE /scenarios/{scenario_id}`
- `GET /scenarios/{scenario_id}/shares`
- `POST /scenarios/{scenario_id}/shares`
- `DELETE /scenarios/{scenario_id}/shares/{share_user_id}`
- `GET /users/{user_id}/plan`
- `GET /users/{user_id}/slots?limit=100&offset=0`
- `GET /users/{user_id}/scenarios?limit=100&offset=0`
- `GET /users/{user_id}/scenarios/{scenario_id}`
- `POST /users/{user_id}/scenarios/{scenario_id}/run?dry_run=true`
- `POST /users/{user_id}/run-next?dry_run=true`
- `GET /users/{user_id}/history?limit=20&offset=0`
- `GET /users/{user_id}/scenario-data`

User-scoped scenario responses include `role` and `writable`. Shared scenarios are read-only for non-owners.

History responses are paginated from the database. For compatibility, the API synchronizes the legacy JSONL history file before reading.

Planning endpoints compute local slot windows using the target user's `timezone_name` when a matching profile exists, otherwise the authenticated user's timezone and finally `APP_TIMEZONE`.

`POST /scenarios`, `POST /slots`, and `POST /users/{user_id}/scenarios/{scenario_id}/jobs` support `Idempotency-Key` headers.

## Slots

- `GET /slots?limit=100&offset=0`
- `GET /slots?scenario_id=...&limit=100&offset=0`
- `POST /slots`
- `GET /slots/{slot_id}`
- `PATCH /slots/{slot_id}`
- `DELETE /slots/{slot_id}`

Disabling a slot keeps it in DB but excludes it from scheduler execution and JSON sync.

## Steps

Supported collections:

- `before_steps`
- `steps`
- `on_success`
- `on_failure`
- `finally_steps`

Endpoints:

- `GET /users/{user_id}/scenarios/{scenario_id}/step-collections`
- `GET /users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}`
- `GET /users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}`
- `POST /users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}`
- `PUT /users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}`
- `DELETE /users/{user_id}/scenarios/{scenario_id}/step-collections/{collection}/{index}`

Payload:

```json
{
  "step": {
    "type": "sleep",
    "seconds": 1
  }
}
```

Step collection reads intentionally return raw ordered arrays. Other catalog/admin list endpoints use the paginated envelope shown above.

## Jobs

- `POST /users/{user_id}/scenarios/{scenario_id}/jobs?dry_run=true`
- `GET /jobs?limit=100&offset=0`
- `POST /jobs/{job_id}/cancel?user_id=<email-or-uuid>`
- `POST /jobs/{job_id}/retry?user_id=<email-or-uuid>`
- `GET /jobs/{job_id}?user_id=<email-or-uuid>`
- `GET /jobs/{job_id}/events?user_id=<email-or-uuid>`

Jobs are persisted in the database and executed by Celery workers.

## Artifacts

Superuser only:

- `GET /artifacts`
- `GET /artifacts/{kind}/{name}`
- `DELETE /artifacts?older_than_days=30`

Supported kinds: `screenshots`, `pages`.

## Microsoft Graph

Superuser only, except webhooks:

- `POST /graph/subscriptions`
- `GET /graph/subscriptions?limit=100&offset=0`
- `PATCH /graph/subscriptions/{subscription_id}`
- `DELETE /graph/subscriptions/{subscription_id}`
- `GET /graph/notifications`
- `POST /graph/webhook`
- `POST /graph/lifecycle`

## Monitoring

Superuser only:

- `GET /monitoring/summary`
- `GET /metrics`

The response contains basic job counters, stuck jobs, average job duration, and Graph subscriptions close to expiration. `/metrics` returns Prometheus text format.

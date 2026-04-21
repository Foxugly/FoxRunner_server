# Angular Client Guide

## Structure

Recommended folders:

```text
src/app/core/api
src/app/core/auth
src/app/core/http
src/app/features
```

Use a generated OpenAPI client for DTOs and low-level calls. Keep application-specific logic in Angular services so generated files remain replaceable.

## Interceptors

Add interceptors for:

- JWT bearer token;
- `X-Request-ID` generation or propagation;
- global error handling;
- optional loading indicators.

On errors, preserve:

- HTTP status;
- API `code`;
- API `message`;
- `X-Request-ID`.

## Auth

Use `/api/v1/auth/jwt/login` with form data. Store tokens according to the deployment threat model:

- memory-only storage is safest against persistent XSS token theft;
- session storage is simpler but increases exposure;
- local storage should be avoided for sensitive deployments.

Handle expiry by redirecting to login or by adding a refresh-token flow later. The current backend exposes JWT login/logout and password reset, not refresh tokens.

## Feature Flags

Use:

```text
GET /api/v1/users/me/features
```

The response exposes only UI-safe feature flags:

```json
{
  "features": {
    "dashboard": true
  }
}
```

Admin-only flags use the `feature.admin.` prefix and are hidden from non-superusers.

## Client Config

Use:

```text
GET /api/v1/config/client
```

The response exposes only frontend-safe values:

```json
{
  "api_version": "1.0.0",
  "environment": "local",
  "default_timezone": "Europe/Brussels",
  "features": {
    "dashboard": true
  }
}
```

Use `GET /api/v1/timezones/common` for an initial timezone selector.

Example DTO fixtures live in `examples/api_fixtures.json`.

## Dates

API timestamps are UTC ISO 8601 strings. Display them with the current user's `timezone_name` from `GET /api/v1/users/me`.

```ts
export function formatApiDate(value: string, timezoneName: string): string {
  return new Intl.DateTimeFormat('fr-BE', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: timezoneName,
  }).format(new Date(value));
}
```

Do not convert slot form values such as `08:00` to UTC in the UI. Slot hours are local business times; the backend resolves them during planning.

## Tables

PrimeNG table mapping:

- `items` -> table value;
- `total` -> `totalRecords`;
- `limit` -> `rows`;
- `offset` -> `first`.

## Legacy Routes

Angular must use `/api/v1` only. Unprefixed routes are compatibility routes for local tools and can be disabled in production.

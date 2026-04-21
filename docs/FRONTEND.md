# Frontend Integration

The public backend base path is:

```text
/api/v1
```

Legacy unprefixed routes are enabled for local compatibility, but new UI code should use `/api/v1` only.

## Auth Flow

Use FastAPI Users endpoints:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/jwt/login`
- `POST /api/v1/auth/jwt/logout`
- `POST /api/v1/auth/forgot-password`
- `POST /api/v1/auth/reset-password`
- `GET /api/v1/users/me`
- `PATCH /api/v1/users/me`

JWT login uses form data:

```text
username=<email>
password=<password>
```

Protected requests use:

```text
Authorization: Bearer <access_token>
```

`GET /api/v1/users/me` returns `timezone_name`. Let the user update it with `PATCH /api/v1/users/me`:

```json
{
  "timezone_name": "Europe/Brussels"
}
```

Use IANA timezone names only.

`GET /api/v1/timezones/common` returns a compact list suitable for a first selector. Keep a free-search option if the UI needs full IANA coverage.

## Dates And Timezones

The backend stores and returns timestamps in UTC. Display them in the user's `timezone_name`; if missing, fall back to the browser timezone.

```ts
export function formatApiDate(value: string, timezoneName: string): string {
  return new Intl.DateTimeFormat('fr-BE', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: timezoneName,
  }).format(new Date(value));
}
```

Slot definitions such as `08:00-08:15` are local business times. Planning endpoints resolve them with the user's timezone and return concrete timestamps in UTC.

## Errors

API errors use a stable JSON shape:

```json
{
  "code": "validation_error",
  "message": "Payload ou parametres invalides.",
  "details": []
}
```

The frontend should display `message` and keep `code` for typed handling.

## Pagination

List endpoints return:

```json
{
  "items": [],
  "total": 0,
  "limit": 100,
  "offset": 0
}
```

PrimeNG tables can map:

- `rows` -> `limit`
- `first` -> `offset`
- `totalRecords` -> `total`
- table value -> `items`

OpenAPI now exposes typed page schemas such as `ScenarioPagePayload`, `SlotPagePayload`, `JobPagePayload`, `AuditPagePayload`, and `HistoryPagePayload`.

## Idempotency

Mutation endpoints that create asynchronous or persisted resources support `Idempotency-Key`:

- `POST /api/v1/scenarios`
- `POST /api/v1/slots`
- `POST /api/v1/users/{user_id}/scenarios/{scenario_id}/jobs`

Use a UUID per user action and reuse it only for retries of the same action.

## OpenAPI

Generate the client from `openapi.json`, which only exposes `/api/v1` routes:

```powershell
make openapi
```

Recommended generated-client policy:

- Treat generated models as read-only.
- Keep API adapters in a small Angular service layer.
- Do not call legacy unprefixed routes from Angular.

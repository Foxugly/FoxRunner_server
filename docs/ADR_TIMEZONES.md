# ADR: Timezones

Decision: store technical timestamps in UTC, expose API timestamps as UTC ISO 8601 values, and use user profile timezones only for business-time calculations and display.

Rationale:

- UTC storage keeps DB comparisons, Celery jobs, Graph expirations, and audits deterministic.
- `APP_TIMEZONE` remains a fallback, not a storage timezone.
- `User.timezone_name` stores an IANA timezone used by planning endpoints.
- Slot windows such as `08:00-08:15` are local business times; concrete executions are UTC instants.
- Frontends convert API timestamps to the user's timezone for display.

Consequences:

- API serializers must keep returning explicit UTC timestamps.
- Pydantic response schemas use `datetime` for timestamp fields.
- Any future `slot.timezone_name` should override the owner timezone only when a scenario needs a fixed business timezone.

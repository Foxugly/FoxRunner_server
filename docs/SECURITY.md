# Security

## Authentication

The API uses FastAPI Users with JWT bearer authentication.

Production requirements:

- set `AUTH_SECRET` to a strong secret with at least 32 characters;
- rotate secrets through your deployment secret manager;
- use HTTPS at the reverse proxy;
- keep token lifetime controlled with `AUTH_JWT_LIFETIME_SECONDS`.

## CORS

Configure allowed frontend origins explicitly:

```env
API_CORS_ORIGINS=https://app.example.com
```

Do not use broad origins in production.

## Rate Limiting

The API limiter for auth and Graph webhook routes uses a Redis sliding window shared across workers when Redis is available, and falls back to an in-process counter otherwise. Keep reverse-proxy or gateway rate limiting as the primary production control.

Relevant settings:

```env
API_RATE_LIMIT_ENABLED=true
API_RATE_LIMIT_WINDOW_SECONDS=60
API_RATE_LIMIT_MAX_REQUESTS=60
API_RATE_LIMIT_REDIS_URL=redis://redis:6379/2   # optional; falls back to CELERY_BROKER_URL
```

## Payload Size

Requests above `API_MAX_BODY_BYTES` are rejected with HTTP 413. The limit is enforced on both declared `Content-Length` and chunked bodies (the receive channel is wrapped to count bytes as they arrive).

```env
API_MAX_BODY_BYTES=1048576
```

## Webhooks

Graph webhooks are authenticated by their `clientState`. FoxRunner accepts a delivery when the value matches either the per-subscription state saved at creation (`graph_subscriptions.client_state`) or the current `GRAPH_WEBHOOK_CLIENT_STATE` global — supporting rotation in either direction. Production refuses to process webhooks when `GRAPH_WEBHOOK_CLIENT_STATE` is empty. Duplicate notifications are deduplicated on (subscription, resource, change type, lifecycle event).

## Legacy Routes

Disable unprefixed compatibility routes in production:

```env
API_ENABLE_LEGACY_ROUTES=false
```

## Security Headers

Responses include:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `X-Request-ID`

## Secret Redaction

Sensitive keys such as `secret`, `password`, `token`, `key`, `authorization`, and `client_state` are redacted from API error/log payload helpers. Graph raw payloads are stored redacted.
